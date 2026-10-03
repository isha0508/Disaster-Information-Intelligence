import copy
import json
import threading
from urllib.request import urlopen
from http.server import ThreadingHTTPServer

import pytest

from dashboard.adapters import adapt_incident
from dashboard.components import render_dashboard
from dashboard.data import build_dashboard_data, build_demo_dashboard_data
from dashboard.filters import filter_incidents, sort_incidents
from dashboard.metrics import dashboard_summary
from dashboard.app import make_handler


@pytest.fixture
def phase5():
    return {"incident_id": "I-1", "disaster_type": "flood", "location": "North Ward",
            "severity_score": 80, "severity_level": "HIGH", "urgency_score": 60,
            "urgency_level": "HIGH", "priority_score": 71, "priority_level": "HIGH",
            "confidence_score": 55, "confidence_level": "MEDIUM",
            "casualties": {"count": 4}, "displaced": {"count": 20},
            "infrastructure": ["bridge damaged"], "rescue": {"trapped": 2},
            "requests": ["boats"], "resource_estimates": {"method": "heuristic", "estimated_needs": {"boats": 2}},
            "decision_flags": ["CASUALTY_REPORT"],
            "grounded_intelligence": {"summary": "Flood impacts reported.",
                "recommended_actions": ["Review boat request"], "uncertainties": ["Exact count unclear"],
                "grounding_warnings": ["Location unverified"]},
            "resource_estimates_are_heuristic": True}


def test_accepts_phase5_and_preserves_intelligence(phase5):
    view = adapt_incident(phase5)
    assert view["incident_id"] == "I-1" and view["priority_score"] == 71
    assert view["casualties"] == {"count": 4} and view["requests"] == ["boats"]
    assert view["phase7_uncertainties"] == ["Exact count unclear"]
    assert "human review" in view["recommendations_disclaimer"]
    assert view["resource_estimates_are_heuristic"]


def test_preserves_phase6_fields_and_only_success_points():
    record = {"incident_id": "I", "phase5_phase6_incident": {"incident_id": "I", "priority_score": 10},
              "phase6": {"incidents": [{"incident_id": "I", "normalized_location": "Harbor", "latitude": 19.0,
                                          "longitude": 72.0, "geocoding_status": "success",
                                          "spatial_cluster_id": "cluster-1", "hotspot_id": "hot-1",
                                          "spatial_priority_score": 45}]}}
    view = adapt_incident(record)
    assert (view["latitude"], view["longitude"]) == (19.0, 72.0)
    assert view["spatial_cluster_id"] == "cluster-1" and view["hotspot"]
    record["phase6"]["incidents"][0]["geocoding_status"] = "failed"
    assert adapt_incident(record)["latitude"] is None


def test_phase7_8_9_nested_output_preserved():
    event = {"event_id": "E", "event_state": "UPDATED", "processing_status": "completed",
             "phase_status": {"phase5": "completed"}, "provenance": {"source": "feed"},
             "intelligence": {"phase5": {"incident_id": "I", "priority_level": "LOW", "priority_score": 4},
                              "phase6": {"incidents": [{"incident_id": "I", "geocoding_status": "pending"}]},
                              "phase7": {"summary": "Grounded.", "uncertainties": ["Sparse evidence"]}},
             "alert_decision": {"alert_level": "CRITICAL", "priority_score": 4, "suppressed": True},
             "alert_status": "SUPPRESSED"}
    view = adapt_incident(event)
    assert view["event_state"] == "UPDATED" and view["source"] == "feed"
    assert view["phase7_summary"] == "Grounded." and view["phase7_uncertainties"]
    assert view["alert_level"] == "CRITICAL" and view["alert_suppression"]["suppressed"]
    assert view["priority_level"] == "LOW"  # alert level never rewrites Phase 5 priority


def test_missing_optional_and_malformed_records_safe():
    assert adapt_incident({"incident_id": "minimal"})["latitude"] is None
    assert adapt_incident("bad") ["adapter_status"] == "invalid"
    ds = build_dashboard_data(phase5_records=[{}, None, {"incident_id": "ok"}])
    assert len(ds["incidents"]) == 3


def test_no_fake_points_and_unresolved_stays_unresolved(phase5):
    phase5["location_text"] = "Unknown village"
    phase5["geocoding_status"] = "failed"
    phase5["latitude"], phase5["longitude"] = 12, 34
    data = build_dashboard_data(phase5_records=[phase5])
    assert data["incidents"][0]["latitude"] is None
    assert data["spatial_summary"]["valid_coordinate_incidents"] == 0
    assert data["spatial_summary"]["unresolved"][0]["location"] == "Unknown village"


def test_coordinate_range_validation(phase5):
    phase5.update(latitude=91, longitude=72, geocoding_status="success")
    assert adapt_incident(phase5)["latitude"] is None
    phase5.update(latitude=True, longitude=72)
    assert adapt_incident(phase5)["longitude"] is None


def test_priority_sort_filter_deterministically():
    rows = [{"incident_id": "a", "priority_score": 10, "priority_level": "LOW"},
            {"incident_id": "b", "priority_score": 90, "priority_level": "HIGH"}]
    assert [x["incident_id"] for x in sort_incidents(rows)] == ["b", "a"]
    assert [x["incident_id"] for x in filter_incidents(rows, filters={"priority_level": "HIGH"})] == ["b"]


def test_event_spatial_hotspot_and_flag_filters():
    rows = [{"incident_id": "x", "event_state": "NEW", "geocoding_status": "success",
             "spatial_cluster_id": "c1", "hotspot": True, "decision_flags": ["RESCUE"]},
            {"incident_id": "y", "event_state": "DUPLICATE", "geocoding_status": "failed",
             "spatial_cluster_id": None, "hotspot": False, "decision_flags": []}]
    assert [r["incident_id"] for r in filter_incidents(rows, {"event_state": "DUPLICATE"})] == ["y"]
    assert [r["incident_id"] for r in filter_incidents(rows, {"geocoding_status": "success", "hotspot": True,
                                                               "spatial_cluster_id": "c1", "decision_flag": "RESCUE"})] == ["x"]


def test_alert_levels_from_phase9_not_recalculated():
    row = {"incident_intelligence": {"incident_id": "I", "priority_level": "LOW", "priority_score": 3},
           "alert_decision": {"alert_level": "CRITICAL", "priority_score": 3}, "alert_status": "CREATED"}
    view = adapt_incident(row)
    assert view["alert_level"] == "CRITICAL" and view["priority_level"] == "LOW"


def test_live_phase8_artifact_is_labeled_live_and_source_facts_are_available(tmp_path):
    from dashboard.data import load_dashboard_input
    record = {"event_id": "evt-live", "source": "usgs", "source_event_id": "usgs-live-1",
              "event_state": "NEW", "processing_status": "PROCESSED",
              "provenance": {"source": "usgs", "source_event_id": "usgs-live-1"},
              "event": {"title": "M 4.5 - Example", "url": "https://example.test/event",
                        "published_at": "2026-09-25T00:00:00Z", "metadata": {"magnitude": 4.5},
                        "latitude": 11.0, "longitude": 22.0},
              "intelligence": {"phase5": {"priority_score": 3, "priority_level": "LOW"}},
              "alert_decision": {"alert_level": "LOW", "should_alert": False},
              "alert_status": "NO_ALERT"}
    input_path = tmp_path / "live.json"
    input_path.write_text(json.dumps({"run_mode": "live", "events": [record], "alerts": [], "sources": {}}),
                          encoding="utf-8")
    data = load_dashboard_input(input_path)
    assert data["dataset_mode"] == "live_data" and data["dataset_label"].startswith("LIVE DATA")
    view = data["incidents"][0]
    assert view["source_title"] == "M 4.5 - Example" and view["source_event_id"] == "usgs-live-1"
    assert view["source_latitude"] == 11.0 and view["latitude"] is None


def test_demo_synthetic_and_summary_deterministic():
    a, b = build_demo_dashboard_data(), build_demo_dashboard_data()
    assert a["dataset_mode"] == "synthetic_demo" and "SYNTHETIC" in a["dataset_label"]
    assert dashboard_summary(a) == dashboard_summary(b)
    assert a["summary"]["total_incidents"] > 0
    assert a["system_health"]["phase3"]["status"] in {"failed", "degraded", "unavailable"}
    direct = build_dashboard_data(phase5_records=[{"incident_id": "synthetic", "synthetic_data": True}])
    assert direct["dataset_mode"] == "synthetic_demo"


def test_credentials_stripped_and_upstream_unchanged(phase5):
    record = copy.deepcopy(phase5)
    record["api_token"] = "secret-value"
    record["source_url"] = "https://user:password@example.test/path?api_key=hidden&x=1"
    original = copy.deepcopy(record)
    data = build_dashboard_data(phase5_records=[record])
    assert "secret-value" not in json.dumps(data)
    assert "password" not in json.dumps(data) and "hidden" not in json.dumps(data)
    assert record == original


def test_empty_data_json_serializable_and_ui_renders():
    data = build_dashboard_data()
    json.dumps(data, allow_nan=False)
    page = render_dashboard(data)
    assert "Situation overview" in page and "human review" in page
    assert "No successfully geocoded" in page


def test_http_app_serves_dashboard_and_health():
    data = build_dashboard_data(phase5_records=[{"incident_id": "I"}])
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(data))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
            assert response.status == 200 and b"Disaster Information Intelligence" in response.read()
        with urlopen(f"http://127.0.0.1:{server.server_port}/healthz") as response:
            assert json.loads(response.read())["status"] == "ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
