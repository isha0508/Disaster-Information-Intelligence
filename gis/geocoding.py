"""Provider-neutral geocoding contracts and offline implementation."""

from abc import ABC, abstractmethod

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
