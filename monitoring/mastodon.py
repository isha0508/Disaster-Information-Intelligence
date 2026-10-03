"""Read-only adapter for public Mastodon hashtag timelines."""

from __future__ import annotations

from html.parser import HTMLParser
import json
import math
import re
import time
from urllib.parse import quote, urlsplit

from monitoring.normalization import sanitize_source_value, utc_now
from monitoring.real_sources import ADAPTER_VERSION, RealSourceConfig, SourceFetchError, _http_get, _new_report, _safe_error
from monitoring.sources import EventSource

_TAG_RE = re.compile(r"^[\w-]{1,64}$", re.UNICODE)
_BLOCK_TAGS = {"p", "div", "br", "li", "blockquote"}


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if not self.hidden and tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def mastodon_html_to_text(content):
    parser = _PlainText()
    parser.feed(content if isinstance(content, str) else "")
    parser.close()
    return " ".join("".join(parser.parts).split())


class MastodonPublicSource(EventSource):
    """Fetch public hashtag timelines from one Mastodon instance."""

    name = "mastodon"
    source_type = "public_social"
    adapter_name = "MastodonPublicSource"

    def __init__(self, base_url="https://mastodon.social", *, hashtags=("earthquake", "flood", "wildfire", "hurricane"),
                 limit=10, timeout_seconds=20, user_agent=None, max_response_bytes=8_000_000):
        self.base_url = str(base_url).rstrip("/")
        self.hashtags = tuple(dict.fromkeys(str(tag).strip().lstrip("#").casefold() for tag in hashtags if str(tag).strip()))
        self.limit = int(limit)
        self.timeout_seconds = float(timeout_seconds)
        self.user_agent = user_agent or RealSourceConfig.user_agent
        self.max_response_bytes = int(max_response_bytes)
        self.last_report = _new_report(self.name)

    def fetch(self):
        events, report = self.fetch_with_report()
        self.last_report = report
        if report.get("status") == "error":
            raise SourceFetchError(str(report.get("error") or "Mastodon retrieval failed"))
        return events

    def fetch_with_report(self):
        started = time.perf_counter()
        report = _new_report(self.name)
        report.update({"api_endpoint": self.base_url + "/api/v1/timelines/tag/{hashtag}",
                       "hashtags": list(self.hashtags), "status": "empty", "duplicates_removed": 0,
                       "hashtag_results": []})
        try:
            self._validate()
            by_id, received, valid_received, errors, attempted = {}, 0, 0, [], 0
            for hashtag in self.hashtags:
                attempted += 1
                url = f"{self.base_url}/api/v1/timelines/tag/{quote(hashtag, safe='')}?limit={self.limit}"
                tag_result = {"hashtag": hashtag, "records_received": 0, "valid_records": 0,
                              "retained_records": 0, "status": "error"}
                try:
                    body, _, _ = _http_get(url, timeout=self.timeout_seconds,
                        user_agent=self.user_agent, max_bytes=self.max_response_bytes)
                    payload = json.loads(body.decode("utf-8-sig"))
                    if not isinstance(payload, list):
                        raise ValueError("Mastodon timeline response must be a list")
                    received += len(payload)
                    tag_result["records_received"] = len(payload)
                    before_tag = len(by_id)
                    for item in payload:
                        try:
                            event = mastodon_post_to_event(item, self.base_url)
                        except (TypeError, ValueError, KeyError):
                            continue
                        valid_received += 1
                        by_id.setdefault(event["source_event_id"], event)
                    tag_result["valid_records"] = valid_received - (received - len(payload))
                    tag_result["retained_records"] = len(by_id) - before_tag
                    tag_result["status"] = "success" if payload else "empty"
                except Exception as exc:
                    tag_result["error"] = _safe_error(exc)
                    errors.append(f"hashtag #{hashtag}: {tag_result['error']}")
                report["hashtag_results"].append(tag_result)
            duplicates = valid_received - len(by_id)
            events = list(by_id.values())
            status = "partial" if errors and (events or attempted > len(errors)) else "error" if errors else "success" if events else "empty"
            report.update({"status": status, "records_received": received,
                "valid_records": len(events), "retained_records": len(events),
                "duplicates_removed": max(0, duplicates), "hashtags_requested": attempted,
                "invalid_records": received - valid_received,
                "errors": errors, "error": "; ".join(errors) if errors else None,
                "retrieval_timestamp": utc_now()})
            return events, report
        except Exception as exc:
            message = _safe_error(exc)
            report.update({"status": "error", "error": message, "errors": [message],
                           "retrieval_timestamp": utc_now()})
            return [], report
        finally:
            report["latency_seconds"] = round(time.perf_counter() - started, 6)

    def _validate(self):
        parts = urlsplit(self.base_url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError("Mastodon base URL must be HTTPS and contain no credentials")
        if not self.hashtags or any(not _TAG_RE.fullmatch(tag) for tag in self.hashtags):
            raise ValueError("at least one valid Mastodon hashtag is required")
        if not 1 <= self.limit <= 40:
            raise ValueError("Mastodon limit must be within 1..40")
        if self.timeout_seconds <= 0:
            raise ValueError("Mastodon timeout_seconds must be positive")


def mastodon_post_to_event(item, base_url="https://mastodon.social"):
    """Convert one public Mastodon Status into the shared raw event contract."""
    if not isinstance(item, dict):
        raise ValueError("Mastodon status must be an object")
    post_id = item.get("id")
    uri = item.get("uri")
    content = item.get("content")
    text = mastodon_html_to_text(content)
    if not isinstance(post_id, (str, int)) or not str(post_id).strip():
        raise ValueError("Mastodon status ID is required")
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError("Mastodon status URI is required")
    if not text:
        raise ValueError("Mastodon status text is required")
    account = item.get("account") if isinstance(item.get("account"), dict) else {}
    hashtags = item.get("tags") if isinstance(item.get("tags"), list) else []
    tags = [tag.get("name") for tag in hashtags if isinstance(tag, dict) and isinstance(tag.get("name"), str)]
    card = item.get("card") if isinstance(item.get("card"), dict) else {}
    external_url = card.get("url") if isinstance(card.get("url"), str) else None
    gps = _explicit_gps_coordinates(text)
    instance = urlsplit(base_url).hostname
    post_url = item.get("url") if isinstance(item.get("url"), str) else uri
    author = {key: account.get(src) for key, src in (
        ("id", "id"), ("username", "username"), ("acct", "acct"), ("display_name", "display_name"),
        ("url", "url")) if isinstance(account.get(src), (str, int))}
    metadata = {"mastodon_id": str(post_id), "post_uri": uri, "post_url": post_url,
        "author": author, "language": item.get("language"), "visibility": item.get("visibility"),
        "hashtags": tags, "raw_content_html": content, "source_instance": instance,
        "external_link": external_url, "created_at": item.get("created_at"),
        "retrieved_at": utc_now()}
    if gps:
        metadata.update({"latitude": gps[0], "longitude": gps[1],
                         "coordinate_provenance": "explicit_source_text_gps"})
    return {"source": "mastodon", "source_type": "public_social",
        "source_event_id": f"{instance}:{post_id}", "title": None, "text": text,
        "created_at": item.get("created_at"), "published_at": None,
        "retrieved_at": utc_now(), "source_url": post_url, "url": post_url,
        "latitude": gps[0] if gps else None, "longitude": gps[1] if gps else None,
        "coordinate_provenance": "explicit_source_text_gps" if gps else None,
        "adapter": "MastodonPublicSource", "adapter_version": ADAPTER_VERSION,
        "metadata": sanitize_source_value(metadata)}


def _explicit_gps_coordinates(text):
    """Read only an explicitly labelled GPS pair from provider text."""
    match = re.search(r"\bGPS\s*:\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*,\s*"
                      r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\b", text, re.IGNORECASE)
    if not match:
        return None
    try:
        latitude, longitude = float(match.group(1)), float(match.group(2))
    except (TypeError, ValueError, OverflowError):
        return None
    if (not math.isfinite(latitude) or not math.isfinite(longitude) or
            not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
        return None
    return latitude, longitude
