"""Configured, non-blocking geocoder safety checks."""

import json
import time

import gis.geocoding as geocoding


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return self.payload


def _await_result(provider, place):
    result = provider.geocode(place)
    deadline = time.monotonic() + 3
    while result["status"] == "pending" and time.monotonic() < deadline:
        time.sleep(.01)
        result = provider.geocode(place)
    return result


def test_configured_provider_resolves_only_a_locality_and_uses_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(geocoding, "urlopen", lambda request, timeout: (
        calls.append((request, timeout)) or _Response([{
            "lat": "12.34", "lon": "56.78", "addresstype": "city",
            "display_name": "Example City"}])))
    with geocoding.NominatimGeocoder._lock:
        geocoding.NominatimGeocoder._last_request = 0
    provider = geocoding.NominatimGeocoder(base_url="https://geo.invalid/search",
        user_agent="DisasterInformationIntelligence/test", timeout=1, min_interval=1)
    result = _await_result(provider, "Unique Test City")
    assert result["status"] == "success"
    assert result["source"] == "nominatim"
    assert result["latitude"] == 12.34 and result["longitude"] == 56.78
    assert len(calls) == 1 and calls[0][1] == 1
    assert calls[0][0].get_header("User-agent") == "DisasterInformationIntelligence/test"
    assert provider.geocode("Unique Test City")["status"] == "success"
    assert len(calls) == 1


def test_geocoder_rejects_country_centroids_and_gracefully_fails(monkeypatch):
    monkeypatch.setattr(geocoding, "urlopen", lambda request, timeout: _Response([{
        "lat": "0", "lon": "0", "addresstype": "country"}]))
    with geocoding.NominatimGeocoder._lock:
        geocoding.NominatimGeocoder._last_request = 0
    provider = geocoding.NominatimGeocoder(base_url="https://geo.invalid/search",
        user_agent="DisasterInformationIntelligence/test", min_interval=1)
    result = _await_result(provider, "Unique Test Country")
    assert result["status"] == "failed"
    assert result["latitude"] is None and result["longitude"] is None


def test_provider_configuration_is_environment_driven(monkeypatch):
    monkeypatch.setenv("DISASTER_GEOCODER_PROVIDER", "offline")
    assert isinstance(geocoding.configured_geocoder(), geocoding.OfflineGeocoder)
    monkeypatch.setenv("DISASTER_GEOCODER_PROVIDER", "nominatim")
    assert isinstance(geocoding.configured_geocoder(), geocoding.NominatimGeocoder)
