"""Provider-neutral geocoding contracts and configurable async providers."""

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import json
import os
import re
import time
import logging
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from gis.schemas import GEOCODING_STATUSES, validate_coordinates


class Geocoder(ABC):
    """Base class for offline, commercial, or future service geocoders."""
    @abstractmethod
    def geocode(self, location_text):
        """Return a mapping with status and optional coordinates/source."""


class OfflineGeocoder(Geocoder):
    """Exact-match geocoder backed only by caller-supplied vetted coordinates."""
    def __init__(self, gazetteer=None, source="offline_gazetteer"):
        self.gazetteer = {str(k).strip().casefold(): v for k, v in (gazetteer or {}).items()}
        self.source = source

    def geocode(self, location_text):
        if not location_text:
            return {"latitude": None, "longitude": None, "status": "failed", "source": self.source}
        result = self.gazetteer.get(str(location_text).strip().casefold())
        if result is None:
            return {"latitude": None, "longitude": None, "status": "failed", "source": self.source}
        if isinstance(result, dict):
            status = result.get("status", "success")
            latitude, longitude = result.get("latitude"), result.get("longitude")
            source = result.get("source", self.source)
            candidates = result.get("candidates")
        else:
            try:
                latitude, longitude = result
            except (TypeError, ValueError):
                return {"latitude": None, "longitude": None, "status": "failed", "source": self.source}
            status, source, candidates = "success", self.source, None
        if status not in GEOCODING_STATUSES:
            status = "failed"
        valid, _ = validate_coordinates(latitude, longitude)
        if status == "success" and not valid:
            status = "failed"
        response = {"latitude": float(latitude) if valid and status == "success" else None,
                    "longitude": float(longitude) if valid and status == "success" else None,
                    "status": status, "source": source}
        if candidates is not None:
            response["candidates"] = candidates
        return response


class PendingGeocoder(Geocoder):
    """Placeholder for asynchronous providers; never fabricates coordinates."""
    def __init__(self, source="pending_provider"):
        self.source = source

    def geocode(self, location_text):
        return {"latitude": None, "longitude": None, "status": "pending", "source": self.source}


class NominatimGeocoder(Geocoder):
    """Single-worker, cached Nominatim lookup that never blocks ingestion."""
    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="incident-geocoder")
    _lock = Lock()
    _cache = {}
    _last_request = 0.0

    def __init__(self, *, base_url=None, user_agent=None, timeout=None,
                 min_interval=None, offline=None):
        self.base_url = (base_url or os.getenv(
            "DISASTER_GEOCODER_URL", "https://nominatim.openstreetmap.org/search")).strip()
        self.user_agent = user_agent or os.getenv(
            "DISASTER_GEOCODER_USER_AGENT", "DisasterInformationIntelligence/1.0")
        self.timeout = float(timeout if timeout is not None else os.getenv(
            "DISASTER_GEOCODER_TIMEOUT", "5"))
        self.min_interval = float(min_interval if min_interval is not None else os.getenv(
            "DISASTER_GEOCODER_MIN_INTERVAL", "15"))
        self.offline = offline or OfflineGeocoder()
        if self.timeout <= 0 or self.min_interval < 1:
            raise ValueError("geocoder timeout must be positive and request interval at least one second")
        self.source = "nominatim"

    def geocode(self, location_text):
        text = re.sub(r"\s+", " ", str(location_text or "")).strip()
        key = (self.base_url.casefold(), text.casefold())
        if not text or _is_offset_description(text):
            return {"latitude": None, "longitude": None, "status": "unresolved", "source": self.source}
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                if hasattr(cached, "done"):
                    if not cached.done():
                        return {"latitude": None, "longitude": None, "status": "pending", "source": self.source}
                    try:
                        result = cached.result()
                    except Exception:
                        result = self._offline_result(text)
                    self._cache[key] = result
                    return dict(result)
                return dict(cached)
            future = self._executor.submit(self._lookup, text)
            self._cache[key] = future
        return {"latitude": None, "longitude": None, "status": "pending", "source": self.source}

    def _offline_result(self, text):
        try:
            result = self.offline.geocode(text)
            return dict(result) if isinstance(result, dict) else {
                "latitude": None, "longitude": None, "status": "failed", "source": self.source}
        except Exception:
            return {"latitude": None, "longitude": None, "status": "failed", "source": self.source}

    def _lookup(self, text):
        # The executor has one worker. This extra gate also enforces the delay
        # when several callers queue different places at once.
        with self._lock:
            delay = self.min_interval - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)
        with self._lock:
            type(self)._last_request = time.monotonic()
        query = urlencode({"q": text, "format": "jsonv2", "limit": 1,
                           "addressdetails": 1})
        request = Request(self.base_url + ("&" if "?" in self.base_url else "?") + query,
                          headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read(512_000).decode("utf-8"))
            if not isinstance(payload, list) or not payload:
                return self._offline_result(text)
            item = payload[0]
            address_type = str(item.get("addresstype") or item.get("type") or "").casefold()
            allowed = {"city", "town", "village", "hamlet", "suburb", "neighbourhood",
                       "municipality", "district", "island", "peninsula", "bay", "strait",
                       "volcano", "mountain", "reef", "locality"}
            # Never turn a country, state, region, continent or free-standing
            # administrative area into an incident point.
            if address_type not in allowed:
                return self._offline_result(text)
            lat, lon = item.get("lat"), item.get("lon")
            valid, _ = validate_coordinates(lat, lon)
            if not valid:
                return self._offline_result(text)
            return {"latitude": float(lat), "longitude": float(lon), "status": "success",
                    "source": self.source, "resolved_name": str(item.get("display_name") or text),
                    "attribution": "© OpenStreetMap contributors"}
        except Exception:
            return self._offline_result(text)


def configured_geocoder():
    """Use the opt-in configured online provider, with vetted offline fallback."""
    provider = os.getenv("DISASTER_GEOCODER_PROVIDER", "nominatim").strip().casefold()
    gazetteer = {}
    gazetteer_path = os.getenv("DISASTER_GEOCODER_GAZETTEER_PATH", "").strip()
    if gazetteer_path:
        path = Path(gazetteer_path).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                gazetteer = value
            else:
                raise ValueError("gazetteer root must be an object")
        except Exception as exc:
            logging.getLogger("gis.geocoding").warning(
                "offline gazetteer could not be loaded from %s: %s", path, type(exc).__name__)
    offline = OfflineGeocoder(gazetteer)
    if provider in {"", "offline", "offline_gazetteer", "none"}:
        return offline
    if provider in {"nominatim", "nominatim+offline"}:
        return NominatimGeocoder(offline=offline)
    raise ValueError(f"unsupported DISASTER_GEOCODER_PROVIDER: {provider}")


def _is_offset_description(text):
    return bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:km|kilometers?|miles?|mi)\s+(?:N|S|E|W|NE|NW|SE|SW)\b.*\bof\b", text, re.I))


is_offset_description = _is_offset_description
