"""Offline tests for Phase 6 spatial intelligence."""

import json

import pytest

from gis import (OfflineGeocoder, PendingGeocoder, SpatialConfig,
                 cluster_incidents_spatial, detect_hotspots,
                 enrich_spatial_record, haversine_distance_km,
                 normalize_location, rank_spatial_priority,
                 run_spatial_pipeline, to_geojson, validate_coordinates)


def record(i, lat=None, lon=None, **extra):
    result = {"incident_id": f"INC_{i}", "location": ["Place"],
              "priority_score": 0, "severity_score": 0, "urgency_score": 0,
              "decision_flags": [], "casualties": []}
    if lat is not None:
        result.update(latitude=lat, longitude=lon, geocoding_status="success")
    result.update(extra)
    return result


def test_normalization_strips_whitespace_and_preserves_original():
    assert normalize_location("  Downtown   Mumbai ") == [{
        "original_text": "  Downtown   Mumbai ", "normalized_text": "Downtown Mumbai", "status": "normalized"}]


@pytest.mark.parametrize("value", [None, "", "  ", [], {}])
def test_empty_location_safe(value):
    assert normalize_location(value) == []


def test_duplicate_mentions_removed_case_insensitively():
    assert len(normalize_location(["Kochi", " kochi ", "KOCHI", "Aluva"])) == 2


@pytest.mark.parametrize("lat,lon,valid", [(0, 0, True), (90, 180, True), (-90, -180, True),
                                           (90.1, 0, False), (0, 181, False), ("bad", 3, False),
                                           (None, 2, False), (True, 0, False)])
def test_coordinate_validation(lat, lon, valid):
    assert validate_coordinates(lat, lon)[0] is valid


def test_offline_geocoder_success_and_invalid_coordinates():
    g = OfflineGeocoder({"Kochi": (9.9312, 76.2673), "Bad": (100, 0)})
    assert g.geocode("kochi")["status"] == "success"
    assert g.geocode("Bad")["status"] == "failed"


def test_failed_and_pending_geocoding():
    assert OfflineGeocoder().geocode("Unknown")["status"] == "failed"
    assert PendingGeocoder().geocode("Unknown")["status"] == "pending"


def test_haversine_known_pair_and_missing_coordinates():
    distance = haversine_distance_km(0, 0, 0, 1)
    assert distance == pytest.approx(111.195, rel=1e-4)
    assert haversine_distance_km(None, 0, 0, 1) is None


def test_spatial_clusters_noise_and_missing_coordinates_deterministically():
    cfg = SpatialConfig(cluster_radius_km=2, cluster_min_incidents=2)
    data = [record("b", 0, 0), record("a", 0, .005), record("noise", 1, 1), record("missing")]
    first = cluster_incidents_spatial(data, cfg)
    second = cluster_incidents_spatial(data, cfg)
    assert first[0]["spatial_cluster_id"] == first[1]["spatial_cluster_id"]
    assert first[0]["spatial_cluster_id"] == second[0]["spatial_cluster_id"]
    assert first[2]["spatial_cluster_status"] == "noise"
    assert first[3]["spatial_cluster_status"] == "missing_coordinates"


def test_singleton_is_valid_noise_not_cluster():
    result = cluster_incidents_spatial([record(1, 1, 1)])[0]
    assert result["spatial_cluster_id"] is None
    assert result["spatial_cluster_status"] == "noise"


def test_hotspot_explains_cluster_signals():
    cfg = SpatialConfig(hotspot_min_incidents=2)
    clustered = cluster_incidents_spatial([
        record(1, 1, 1, spatial_cluster_id="X", priority_score=80, severity_score=70,
               urgency_score=90, decision_flags=["RESCUE_OPERATION"], casualties=["injured"]),
        record(2, 1, 1.001, spatial_cluster_id="X", priority_score=20),
    ], cfg)
    hotspot = detect_hotspots(clustered, cfg)[0]
    assert hotspot["incident_count"] == 2
    assert hotspot["high_priority_count"] == 1
    assert any("rescue" in reason for reason in hotspot["reason"])


def test_spatial_priority_ranks_high_priority_area_first():
    rows = [record(1, 1, 1, spatial_cluster_id="low", priority_score=10),
            record(2, 1, 1, spatial_cluster_id="high", priority_score=90)]
    ranked = rank_spatial_priority(rows)
    assert ranked[0]["spatial_cluster_id"] == "high"


def test_geojson_uses_lon_lat_and_skips_unresolved():
    geo = to_geojson([record(1, 10, 20), record(2)])
    assert geo["features"][0]["geometry"]["coordinates"] == [20.0, 10.0]
    assert len(geo["features"]) == 1
    json.dumps(geo)


def test_phase5_fields_preserved_through_spatial_enrichment():
    source = record("phase5", priority_score=77, severity_score=70, urgency_score=60,
                    confidence_score=90, resource_estimates={"a": 1},
                    decision_flags=["RESCUE_OPERATION"], cluster_id="CLUSTER_OLD")
    out = enrich_spatial_record(source, OfflineGeocoder({"Place": (1, 2)}))
    for key in ("incident_id", "priority_score", "severity_score", "urgency_score",
                "confidence_score", "resource_estimates", "decision_flags", "cluster_id"):
        assert out[key] == source[key]
    assert out["geocoding_status"] == "success"


def test_pipeline_phase4_input_and_deterministic_spatial_ids():
    config = SpatialConfig(cluster_radius_km=5)
    geocoder = OfflineGeocoder({"A": (10, 10), "B": (10.001, 10)})
    inputs = [{"text": "Flood A", "location": ["A"], "disaster_type": "flood"},
              {"text": "Flood B", "location": ["B"], "disaster_type": "flood"}]
    first = run_spatial_pipeline(inputs, geocoder, config)
    second = run_spatial_pipeline(inputs, geocoder, config)
    assert len(first["incidents"]) == 2
    assert first["incidents"][0]["spatial_cluster_id"] == second["incidents"][0]["spatial_cluster_id"]
    assert first["incidents"][0]["cluster_id"]
    assert first["clusters"][0]["incident_count"] == 2
    json.dumps(first)


def test_pipeline_empty_input():
    result = run_spatial_pipeline([])
    assert result["incidents"] == []
    assert result["hotspots"] == []
    assert result["geojson"]["features"] == []


def test_malformed_record_and_geocoder_error_safe():
    class Broken:
        def geocode(self, text):
            raise RuntimeError("offline")
    out = enrich_spatial_record({"location": [None, 12]}, Broken())
    assert out["geocoding_status"] == "failed"
    assert out["latitude"] is None
    json.dumps(out)


def test_ambiguous_status_supported_without_coordinates():
    out = enrich_spatial_record({"location": ["Springfield"]},
                                OfflineGeocoder({"Springfield": {"status": "ambiguous", "candidates": ["A", "B"]}}))
    assert out["geocoding_status"] == "ambiguous"
    assert out["latitude"] is None
