"""Provider adapters for public disaster feeds, sharing one raw event contract.

This module is deliberately independent of the Phase 3–10 business logic.
New providers (including a future authorized social/public information API)
only need to implement :class:`EventSource` and return the same event fields.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import hashlib
import json
import logging
import math
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from monitoring.normalization import normalize_content, sanitize_source_value, utc_now
from monitoring.sources import EventSource


USGS_FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
GDACS_SEARCH_URL = "https://www.gdacs.org/gdacsapi/api/Events/geteventlist/SEARCH"
GDACS_RSS_URL = "https://www.gdacs.org/xml/rss.xml"
ADAPTER_VERSION = "1.0"
DEFAULT_RELEVANCE_TERMS = (
    "earthquake", "flood", "cyclone", "hurricane", "storm", "landslide",
    "wildfire", "tsunami", "volcano", "drought", "disaster", "evacuation",
    "rescue", "emergency",
)
DEFAULT_MASTODON_HASHTAGS = ("earthquake", "flood", "wildfire", "hurricane")


@dataclass(frozen=True)
class RealSourceConfig:
    """Environment-driven public-feed settings; no provider credentials needed."""

    usgs_enabled: bool = True
    usgs_url: str = USGS_FEED_URL
    gdacs_enabled: bool = True
    gdacs_url: str = GDACS_SEARCH_URL
    gdacs_event_types: tuple = ("EQ", "TC", "FL", "VO", "DR", "WF")
    gdacs_days: int = 7
    news_enabled: bool = True
    rss_feeds: tuple = ()
    relevance_filter_enabled: bool = True
    relevance_keywords: tuple = DEFAULT_RELEVANCE_TERMS
    keep_uncertain_articles: bool = True
    timeout_seconds: float = 20.0
    user_agent: str = "DisasterInformationIntelligence/1.0 (public disaster feed client)"
    polling_interval_seconds: float = 360.0
    max_events_per_source: int = 10
    mastodon_enabled: bool = False
    mastodon_base_url: str = "https://mastodon.social"
    mastodon_hashtags: tuple = DEFAULT_MASTODON_HASHTAGS
    mastodon_limit: int = 10

    def __post_init__(self):
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.polling_interval_seconds < 60:
            raise ValueError("public feeds must be polled no faster than once per minute")
        if self.max_events_per_source < 1 or self.gdacs_days < 1:
            raise ValueError("max_events_per_source and gdacs_days must be positive")
        if not 1 <= self.mastodon_limit <= 40:
            raise ValueError("Mastodon limit must be within 1..40")

    @classmethod
    def from_env(cls):
        """Build settings from environment variables; `.env` files are not read."""
        # A public, official disaster-alert feed makes RSS operational by
        # default. An explicitly empty variable still disables RSS cleanly.
        feeds_text = os.environ.get("DISASTER_RSS_FEEDS", GDACS_RSS_URL)
        feeds = tuple(item.strip() for item in re.split(r"[,\r\n]+", feeds_text) if item.strip())
        keyword_text = os.environ.get("DISASTER_RSS_KEYWORDS")
        keywords = tuple(item.strip().casefold() for item in re.split(r"[,\r\n]+", keyword_text)
                         if item.strip()) if keyword_text is not None else DEFAULT_RELEVANCE_TERMS
        event_types = tuple(item.strip().upper() for item in os.environ.get(
            "GDACS_EVENT_TYPES", "EQ,TC,FL,VO,DR,WF").split(",") if item.strip())
        return cls(
            usgs_enabled=_env_bool("USGS_ENABLED", True),
            usgs_url=os.environ.get("USGS_FEED_URL", USGS_FEED_URL),
            gdacs_enabled=_env_bool("GDACS_ENABLED", True),
            gdacs_url=os.environ.get("GDACS_FEED_URL", GDACS_SEARCH_URL),
            gdacs_event_types=event_types,
            gdacs_days=int(os.environ.get("GDACS_DAYS", "7")),
            news_enabled=_env_bool("NEWS_ENABLED", True), rss_feeds=feeds,
            relevance_filter_enabled=_env_bool("DISASTER_RSS_FILTER_ENABLED", True),
            relevance_keywords=keywords,
            keep_uncertain_articles=_env_bool("DISASTER_RSS_KEEP_UNCERTAIN", True),
            timeout_seconds=float(os.environ.get("DISASTER_SOURCE_TIMEOUT", "20")),
            user_agent=os.environ.get("DISASTER_SOURCE_USER_AGENT", cls.user_agent),
            polling_interval_seconds=float(os.environ.get("DISASTER_POLL_INTERVAL", "360")),
            max_events_per_source=int(os.environ.get("DISASTER_MAX_EVENTS_PER_SOURCE", "10")),
            mastodon_enabled=_env_bool("DISASTER_MASTODON_ENABLED", True),
            mastodon_base_url=os.environ.get("DISASTER_MASTODON_BASE_URL", "https://mastodon.social"),
            mastodon_hashtags=tuple(item.strip().lstrip("#").casefold() for item in os.environ.get(
                "DISASTER_MASTODON_HASHTAGS", ",".join(DEFAULT_MASTODON_HASHTAGS)).split(",") if item.strip()),
            mastodon_limit=int(os.environ.get("DISASTER_MASTODON_LIMIT", "10")),
        )


class SourceFetchError(RuntimeError):
    """A feed request or parse failed; a MultiSource will isolate this failure."""


class HttpEventSource(EventSource):
    """Common safe HTTP/report behavior for future provider-specific adapters."""

    def __init__(self, *, timeout_seconds=20.0, user_agent=None, max_response_bytes=8_000_000):
        self.timeout_seconds = float(timeout_seconds)
        self.user_agent = user_agent or RealSourceConfig.user_agent
        self.max_response_bytes = int(max_response_bytes)
        self.last_report = _new_report(self.name)

    def fetch(self):
        events, report = self.fetch_with_report()
        self.last_report = report
        if report["status"] == "error":
            raise SourceFetchError(report["error"])
        return events

    def fetch_with_report(self):
        started = time.perf_counter()
        report = _new_report(self.name)
        try:
            body, content_type, retrieved_at = _http_get(
                self.url, timeout=self.timeout_seconds, user_agent=self.user_agent,
                max_bytes=self.max_response_bytes)
            events, received, invalid, filtered = self.parse(body, content_type, retrieved_at)
            report.update({"status": "success" if events else "empty", "records_received": received,
                           "valid_records": len(events), "invalid_records": invalid,
                           "filtered_records": filtered, "retrieval_timestamp": retrieved_at,
                           "http_status": 200, "content_type": content_type})
            return events, report
        except Exception as exc:
            message = _safe_error(exc)
            report.update({"status": "error", "error": message, "errors": [message],
                           "retrieval_timestamp": utc_now()})
            return [], report
        finally:
            report["latency_seconds"] = round(time.perf_counter() - started, 6)

    def parse(self, body, content_type, retrieved_at):
        raise NotImplementedError


class USGSEarthquakeSource(HttpEventSource):
    """USGS official GeoJSON summary feed adapter."""

    name = "usgs"
    source_type = "official_public_feed"
    adapter_name = "USGSGeoJSONSource"

    def __init__(self, url=USGS_FEED_URL, **kwargs):
        self.url = url
        super().__init__(**kwargs)

    def parse(self, body, content_type, retrieved_at):
        payload = json.loads(body.decode("utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
            raise ValueError("USGS response is not a GeoJSON FeatureCollection")
        features = payload.get("features")
        if not isinstance(features, list):
            raise ValueError("USGS GeoJSON features must be a list")
        events, invalid = [], 0
        for feature in features:
            try:
                events.append(usgs_feature_to_event(feature, retrieved_at))
            except (TypeError, ValueError, KeyError):
                invalid += 1
        return events, len(features), invalid, 0


def usgs_feature_to_event(feature, retrieved_at=None):
    """Convert one source GeoJSON feature; USGS facts stay in source metadata."""
    if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
        raise ValueError("USGS feature and properties are required")
    properties = feature["properties"]
    source_id = feature.get("id") or properties.get("code")
    if source_id is None or not str(source_id).strip():
        raise ValueError("USGS event ID is required")
    title = _text(properties.get("title"))
    place = _text(properties.get("place"))
    magnitude = _finite_or_none(properties.get("mag"))
    if not title and not place:
        raise ValueError("USGS title or place is required for downstream text")
    alert, status = properties.get("alert"), properties.get("status")
    facts = [title or place]
    if magnitude is not None:
        facts.append(f"Source magnitude: {magnitude:g}")
    if alert:
        facts.append(f"Source alert: {alert}")
    if status:
        facts.append(f"Source status: {status}")
    text = ". ".join(facts)
    geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
    coords = geometry.get("coordinates") if geometry.get("type") == "Point" else None
    longitude = latitude = depth = None
    if isinstance(coords, (list, tuple)) and len(coords) >= 2:
        longitude, latitude = _valid_lonlat(coords[0], coords[1])
        if len(coords) >= 3:
            depth = _finite_or_none(coords[2])
    event_timestamp = _epoch_ms(properties.get("time"))
    updated = _epoch_ms(properties.get("updated"))
    source_url = _text(properties.get("url"))
    metadata = {"title": title, "place": place, "magnitude": magnitude,
                "latitude": latitude, "longitude": longitude, "depth_km": depth,
                "alert": properties.get("alert"), "status": properties.get("status"),
                "felt_reports": properties.get("felt"), "tsunami": properties.get("tsunami"),
                "significance": properties.get("sig"), "magnitude_type": properties.get("magType"),
                "feature_type": properties.get("type"), "raw_properties": sanitize_source_value(properties),
                "event_timestamp": event_timestamp}
    return {"source": "usgs", "source_type": "official_public_feed",
            "source_event_id": str(source_id), "title": title, "text": text,
            "event_timestamp": event_timestamp, "published_at": None, "updated_at": updated,
            "source_url": source_url, "url": source_url, "location_text": place,
            "latitude": latitude, "longitude": longitude,
            "observed_at": updated, "retrieved_at": retrieved_at or utc_now(),
            "adapter": "USGSGeoJSONSource", "adapter_version": ADAPTER_VERSION,
            "metadata": metadata}


class GDACSEventSource(HttpEventSource):
    """GDACS official public event-search API adapter (GeoJSON/JSON)."""

    name = "gdacs"
    source_type = "official_public_feed"
    adapter_name = "GDACSEventSource"

    def __init__(self, url=GDACS_SEARCH_URL, *, event_types=("EQ", "TC", "FL", "VO", "DR", "WF"),
                 days=7, **kwargs):
        self.base_url = url
        self.event_types = tuple(event_types)
        self.days = int(days)
        self.url = url  # replaced immediately before each request, based on fresh UTC window
        super().__init__(**kwargs)

    def fetch_with_report(self):
        today = datetime.now(timezone.utc).date()
        query = urlencode({"eventlist": ";".join(self.event_types),
                           "fromdate": (today - timedelta(days=self.days)).isoformat(),
                           "todate": today.isoformat()})
        self.url = self.base_url + ("&" if "?" in self.base_url else "?") + query
        return super().fetch_with_report()

    def parse(self, body, content_type, retrieved_at):
        payload = json.loads(body.decode("utf-8-sig"))
        if isinstance(payload, dict) and isinstance(payload.get("features"), list):
            entries = payload["features"]
        elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
            entries = payload["data"]
        elif isinstance(payload, dict) and isinstance(payload.get("events"), list):
            entries = payload["events"]
        elif isinstance(payload, list):
            entries = payload
        else:
            raise ValueError("GDACS response has no event collection")
        events, invalid = [], 0
        for item in entries:
            try:
                events.append(gdacs_item_to_event(item, retrieved_at))
            except (TypeError, ValueError, KeyError):
                invalid += 1
        return events, len(entries), invalid, 0


def gdacs_item_to_event(item, retrieved_at=None):
    """Convert a GDACS API item while retaining the original event properties."""
    if not isinstance(item, dict):
        raise ValueError("GDACS item must be a mapping")
    props = item.get("properties") if isinstance(item.get("properties"), dict) else item
    event_type = _first(props, "eventtype", "eventType", "event_type", "type")
    event_id = _first(props, "eventid", "eventId", "event_id", "id") or item.get("id")
    if event_id is None:
        raise ValueError("GDACS event identifier is required")
    stable_id = f"{event_type}:{event_id}" if event_type else str(event_id)
    title = _text(_first(props, "name", "title", "eventname"))
    description = _text(_first(props, "description", "alerttext", "summary"))
    alert_level = _first(props, "alertlevel", "alertLevel", "alert")
    content_parts = [value for value in (title, description) if value]
    if alert_level:
        content_parts.append(f"Source alert level: {alert_level}")
    text = " — ".join(content_parts)
    if not text:
        raise ValueError("GDACS name or description is required for downstream text")
    geometry = item.get("geometry") if isinstance(item.get("geometry"), dict) else {}
    longitude = latitude = None
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Point" and isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
        longitude, latitude = _valid_lonlat(coordinates[0], coordinates[1])
    country = _text(_first(props, "country", "countryname", "countryName"))
    region = _text(_first(props, "region", "location", "place"))
    location = ", ".join(part for part in (region, country) if part) or country or region
    source_url = _text(_first(props, "url", "link", "eventurl", "eventUrl"))
    event_timestamp = _date_text(_first(props, "fromdate", "fromDate", "date"))
    published = _date_text(_first(props, "published", "publishedAt", "publicationdate"))
    updated = _date_text(_first(props, "updated", "updatedAt", "modified", "lastUpdate"))
    metadata = {"event_type": event_type, "event_id": str(event_id),
                "episode_id": _first(props, "episodeid", "episodeId"),
                "alert_level": alert_level,
                "country": country, "region": region,
                "event_timestamp": event_timestamp,
                "event_end_at": _date_text(_first(props, "todate", "toDate")),
                "severity": sanitize_source_value(_first(props, "severity", "severitydata")),
                "geometry_type": geometry.get("type"),
                "source_geometry": sanitize_source_value(geometry) if geometry else None,
                "source_coordinates": [longitude, latitude] if latitude is not None else None,
                "raw_properties": sanitize_source_value(props)}
    return {"source": "gdacs", "source_type": "official_public_feed",
            "source_event_id": stable_id, "title": title, "text": text,
            "event_timestamp": event_timestamp, "published_at": published, "updated_at": updated,
            "observed_at": updated, "source_url": source_url, "url": source_url,
            "location_text": location, "latitude": latitude, "longitude": longitude,
            "disaster_type": _text(event_type), "retrieved_at": retrieved_at or utc_now(),
            "adapter": "GDACSEventSource", "adapter_version": ADAPTER_VERSION,
            "metadata": metadata}


class NewsRSSSource(HttpEventSource):
    """Configurable RSS 2.0 / Atom adapter with conservative keyword filtering."""

    name = "news_rss"
    source_type = "external_feed"
    adapter_name = "NewsRSSSource"

    def __init__(self, feeds=(), *, relevance_filter_enabled=True,
                 relevance_keywords=DEFAULT_RELEVANCE_TERMS, keep_uncertain=True, **kwargs):
        self.feeds = tuple(feeds)
        self.relevance_filter_enabled = bool(relevance_filter_enabled)
        self.relevance_keywords = tuple(str(value).casefold() for value in relevance_keywords)
        self.keep_uncertain = bool(keep_uncertain)
        self.last_reports = []
        # A NewsRSSSource aggregates multiple feed URLs and therefore fetches directly.
        self.url = None
        super().__init__(**kwargs)

    def fetch(self):
        events, report = self.fetch_with_report()
        self.last_report = report
        return events

    def fetch_with_report(self):
        if not self.feeds:
            report = _new_report(self.name)
            report.update({"status": "not_configured", "retrieval_timestamp": utc_now()})
            self.last_reports = []
            self.last_report = report
            return [], report
        started = time.perf_counter()
        results, reports = [], []
        for feed_url in self.feeds:
            report = _new_report(self.name)
            feed_start = time.perf_counter()
            try:
                _validate_http_url(feed_url)
                body, content_type, retrieved_at = _http_get(
                    feed_url, timeout=self.timeout_seconds, user_agent=self.user_agent,
                    max_bytes=self.max_response_bytes)
                feed_title, entries = parse_feed(body)
                valid, invalid, filtered = [], 0, 0
                for entry in entries:
                    try:
                        event, included = rss_entry_to_event(
                            entry, feed_url=feed_url, feed_title=feed_title,
                            retrieved_at=retrieved_at,
                            relevance_filter_enabled=self.relevance_filter_enabled,
                            relevance_keywords=self.relevance_keywords,
                            keep_uncertain=self.keep_uncertain)
                        if included:
                            valid.append(event)
                        else:
                            filtered += 1
                    except (TypeError, ValueError, KeyError):
                        invalid += 1
                results.extend(valid)
                report.update({"status": "success" if valid or not entries else "empty",
                               "feed_url": _public_url(feed_url), "feed_title": feed_title,
                               "records_received": len(entries), "valid_records": len(valid),
                               "invalid_records": invalid, "filtered_records": filtered,
                               "retrieval_timestamp": retrieved_at, "http_status": 200,
                               "content_type": content_type})
            except Exception as exc:
                message = _safe_error(exc)
                report.update({"status": "error", "feed_url": _public_url(feed_url),
                               "error": message, "errors": [message],
                               "retrieval_timestamp": utc_now()})
            report["latency_seconds"] = round(time.perf_counter() - feed_start, 6)
            reports.append(report)
        statuses = {item["status"] for item in reports}
        status = "error" if statuses == {"error"} else ("partial" if "error" in statuses else
                 ("success" if results else "empty"))
        aggregate = _new_report(self.name)
        aggregate.update({"status": status, "feed_count": len(reports),
                          "records_received": sum(item.get("records_received", 0) for item in reports),
                          "valid_records": len(results),
                          "invalid_records": sum(item.get("invalid_records", 0) for item in reports),
                          "filtered_records": sum(item.get("filtered_records", 0) for item in reports),
                          "errors": [item["error"] for item in reports if item.get("error")],
                          "retrieval_timestamp": utc_now(), "latency_seconds": round(time.perf_counter()-started, 6),
                          "feeds": reports})
        self.last_reports, self.last_report = reports, aggregate
        return results, aggregate


def parse_feed(body):
    """Return publisher title and RSS/Atom entries without external XML entities."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"malformed RSS/Atom feed: {exc}") from exc
    feed_title = None
    root_name = _local(root.tag).casefold()
    if root_name == "rss":
        channel = next((child for child in root if _local(child.tag).casefold() == "channel"), None)
        if channel is None:
            raise ValueError("RSS channel missing")
        feed_title = _child_text(channel, "title")
        entries = [child for child in channel if _local(child.tag).casefold() == "item"]
    elif root_name == "feed":
        feed_title = _child_text(root, "title")
        entries = [child for child in root if _local(child.tag).casefold() == "entry"]
    else:
        raise ValueError("unsupported XML feed root; expected RSS or Atom")
    return _text(feed_title), [_xml_entry(item) for item in entries]


def rss_entry_to_event(entry, *, feed_url, feed_title=None, retrieved_at=None,
                       relevance_filter_enabled=True, relevance_keywords=DEFAULT_RELEVANCE_TERMS,
                       keep_uncertain=True):
    title = _text(entry.get("title"))
    summary = _text(entry.get("summary") or entry.get("content"))
    if not title and not summary:
        raise ValueError("RSS/Atom item has no title or summary")
    text = " — ".join(part for part in (title, summary) if part)
    match = next((keyword for keyword in relevance_keywords if keyword and keyword.casefold() in text.casefold()), None)
    uncertain = len(text) < 30 or not relevance_keywords
    relevance = "relevant" if match else ("uncertain" if uncertain else "no_keyword_match")
    included = (not relevance_filter_enabled or match is not None or
                (uncertain and keep_uncertain))
    url = _text(entry.get("url"))
    guid = _text(entry.get("id"))
    published = _date_text(entry.get("published"))
    updated = _date_text(entry.get("updated"))
    source_id = None
    if guid:
        # RSS GUIDs are only unique within a publisher/feed in many real-world feeds.
        feed_key = hashlib.sha256(feed_url.casefold().encode("utf-8")).hexdigest()[:12]
        source_id = f"{feed_key}:{guid}"
    else:
        stable = "\0".join((feed_url.casefold(), url or "",
                             normalize_content(title or "").casefold(), published or ""))
        if stable.strip("\0"):
            source_id = "fallback:" + hashlib.sha256(stable.encode("utf-8")).hexdigest()
    metadata = {"publisher": feed_title, "feed_url": _public_url(feed_url),
                "article_id": guid, "categories": entry.get("categories") or [], "relevance_status": relevance,
                "matched_keyword": match, "raw_item": sanitize_source_value(entry)}
    return ({"source": "news_rss", "source_type": "external_feed",
             "source_event_id": source_id, "title": title, "text": text,
             "published_at": published, "updated_at": updated,
             "observed_at": updated, "source_url": url, "url": url,
             "retrieved_at": retrieved_at or utc_now(), "adapter": "NewsRSSSource",
             "adapter_version": ADAPTER_VERSION, "metadata": metadata}, included)


class MultiSource(EventSource):
    """Fetch independent adapters in isolation and expose per-source reports."""

    name = "multi_source"
    source_type = "aggregate"

    def __init__(self, sources):
        self.sources = tuple(sources)
        self.source_reports = {}

    def fetch(self):
        events, reports = [], {}
        for source in self.sources:
            name = getattr(source, "name", source.__class__.__name__)
            try:
                if hasattr(source, "fetch_with_report"):
                    records, report = source.fetch_with_report()
                else:
                    started = time.perf_counter()
                    records = list(source.fetch() or [])
                    report = _new_report(name)
                    report.update({"status": "success" if records else "empty",
                                   "records_received": len(records), "valid_records": len(records),
                                   "retrieval_timestamp": utc_now(),
                                   "latency_seconds": round(time.perf_counter()-started, 6)})
                reports[name] = sanitize_source_value(report)
                if report.get("status") == "error":
                    logging.getLogger("monitoring.sources").warning(
                        "source=%s retrieval failed: %s", name,
                        str(report.get("error") or "; ".join(report.get("errors") or []))[:400])
                events.extend(records or [])
            except Exception as exc:
                report = _new_report(name)
                message = _safe_error(exc)
                report.update({"status": "error", "error": message, "errors": [message],
                               "retrieval_timestamp": utc_now()})
                reports[name] = report
                logging.getLogger("monitoring.sources").warning(
                    "source=%s retrieval failed: %s", name, message)
        self.source_reports = reports
        return events


def configured_sources(config=None):
    """Create enabled configured adapters; ready for third-party adapter injection."""
    config = config or RealSourceConfig.from_env()
    common = {"timeout_seconds": config.timeout_seconds, "user_agent": config.user_agent}
    sources = []
    if config.usgs_enabled:
        sources.append(USGSEarthquakeSource(config.usgs_url, **common))
    if config.gdacs_enabled:
        sources.append(GDACSEventSource(config.gdacs_url, event_types=config.gdacs_event_types,
                                        days=config.gdacs_days, **common))
    if config.news_enabled:
        sources.append(NewsRSSSource(config.rss_feeds,
                                     relevance_filter_enabled=config.relevance_filter_enabled,
                                     relevance_keywords=config.relevance_keywords,
                                     keep_uncertain=config.keep_uncertain_articles, **common))
    if config.mastodon_enabled:
        from monitoring.mastodon import MastodonPublicSource
        sources.append(MastodonPublicSource(config.mastodon_base_url,
            hashtags=config.mastodon_hashtags, limit=config.mastodon_limit, **common))
    return sources


class _TextOnlyHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _http_get(url, *, timeout, user_agent, max_bytes):
    _validate_http_url(url)
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/geo+json, application/json, application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise SourceFetchError(f"HTTP status {status}")
            content_type = response.headers.get("Content-Type", "")
            body = response.read(max_bytes + 1)
    except HTTPError as exc:
        raise SourceFetchError(f"HTTP status {exc.code}") from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError):
            raise SourceFetchError("request timed out") from exc
        raise SourceFetchError(f"network error: {reason}") from exc
    if len(body) > max_bytes:
        raise SourceFetchError(f"response exceeds {max_bytes} byte safety limit")
    return body, content_type, utc_now()


def _validate_http_url(url):
    parts = urlsplit(str(url))
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("feed URL must use http or https and include a host")
    if parts.username or parts.password:
        raise ValueError("feed URL must not embed credentials")


def _public_url(url):
    return sanitize_source_value({"url": url}).get("url")


def _new_report(name):
    return {"source": name, "status": "not_started", "records_received": 0,
            "valid_records": 0, "invalid_records": 0, "new_records": 0,
            "duplicate_records": 0, "updated_records": 0, "errors": []}


def _safe_error(exc):
    message = str(exc)
    message = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", message)
    message = re.sub(r"(?i)(api[_-]?key|token|password|secret)(\s*[:=]\s*)[^\s,;]+",
                     r"\1\2[REDACTED]", message)
    return f"{type(exc).__name__}: {message[:400]}"


def _xml_entry(element):
    fields = {}
    for child in element:
        name = _local(child.tag).casefold()
        text = _xml_text(child)
        if name == "link":
            value = child.attrib.get("href") or text
            rel = str(child.attrib.get("rel", "alternate")).casefold()
            if value and ("url" not in fields or rel == "alternate"):
                fields["url"] = value
        elif name == "category":
            if text:
                fields.setdefault("categories", []).append(text)
        elif name in {"guid", "id"}:
            fields["id"] = text
        elif name in {"description", "summary", "content", "encoded"}:
            fields[name] = text
        elif name in {"pubdate", "published", "updated", "date"}:
            fields["published" if name in {"pubdate", "published", "date"} else "updated"] = text
        elif name == "title":
            fields["title"] = text
        elif text:
            fields[name] = text
    if fields.get("content") and not fields.get("summary"):
        fields["summary"] = fields["content"]
    return fields


def _xml_text(element):
    text = "".join(element.itertext()).strip()
    if not text:
        return None
    parser = _TextOnlyHTMLParser()
    try:
        parser.feed(text)
        return normalize_content(" ".join(parser.parts)) or normalize_content(text)
    except Exception:
        return normalize_content(text)


def _local(tag):
    return str(tag).rsplit("}", 1)[-1].split(":")[-1]


def _child_text(element, name):
    return next((_xml_text(child) for child in element if _local(child.tag).casefold() == name.casefold()), None)


def _text(value):
    return normalize_content(str(value)) if value is not None and str(value).strip() else None


def _first(mapping, *keys):
    return next((mapping[key] for key in keys if mapping.get(key) is not None), None)


def _finite_or_none(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _valid_lonlat(longitude, latitude):
    lon, lat = _finite_or_none(longitude), _finite_or_none(latitude)
    if lon is None or lat is None or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None, None
    return lon, lat


def _epoch_ms(value):
    number = _finite_or_none(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number / 1000.0, timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _date_text(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # GDACS dates are ordinarily ISO text; interpret numeric values as epoch ms only.
        return _epoch_ms(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return None


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}
