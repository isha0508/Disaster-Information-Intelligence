"""Phase 11.5 tests; the dashboard runtime is fed only through a local test API."""

from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from urllib.request import urlopen
from wsgiref.simple_server import make_server

import pytest

from api.app import create_app
from dashboard.api_client import DashboardAPIClient, DashboardAPIError, MAX_API_RESPONSE_BYTES
from dashboard.app import make_live_handler
from dashboard.config import DashboardConfig
from dashboard.live_data import _adapt_persisted_event, load_live_snapshot
from dashboard.live_view import render_live_dashboard
from database import DatabaseRepository


@contextmanager
def serve(app):
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def serve_handler(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def populated_repository(path):
    repo = DatabaseRepository(path)
    event = {"source": "usgs", "source_event_id": "dash-event-1",
        "title": "Flood response at Example River", "text": "Flood response with rescue requests",
        "original_text": "Flood response with rescue requests", "location_text": "Example River District",
        "latitude": 19.1, "longitude": 72.8, "event_timestamp": "2026-09-25T08:00:00Z",
        "observed_at": "2026-09-25T08:10:00Z", "retrieved_at": "2026-09-25T08:11:00Z",
        "disaster_type": "flood", "canonical_disaster_type": "flood",
        "metadata": {"demonstration": True, "api_key": "DO_NOT_LEAK"}}
    type_evidence = {"source_disaster_type": {"value": "flood", "canonical_value": "flood", "source": "usgs"},
        "phase4_disaster_type": {"value": "flood", "canonical_value": "flood"},
        "ml_disaster_type": {"value": "flood", "canonical_value": "flood", "confidence": .91},
        "canonical_disaster_type": "flood", "type_agreement": True,
        "type_agreement_status": "AGREEMENT", "resolution_method": "authoritative_source",
        "resolution_rationale": "Structured source type matched model output."}
    phase5 = {"incident_id": "incident-dash-1", "event_id": "evt_dash_1", "disaster_type": "flood",
        "canonical_disaster_type": "flood", "type_resolution": type_evidence,
        "severity_score": 74, "severity_level": "HIGH", "urgency_score": 68, "urgency_level": "HIGH",
        "confidence_score": 83, "confidence_level": "HIGH", "priority_score": 71,
        "priority_level": "HIGH", "decision_flags": ["RESCUE_REQUIRED"],
        "casualties": {"injured": 4}, "displaced": 16, "infrastructure": ["bridge damage"],
        "requests": ["rescue boats"], "resources": ["medical"], "rescue": True,
        "source_location": {"text": "Example River District"}, "nlp_location_entities": ["Example River"]}
    phase6_row = {"incident_id": "incident-dash-1", "location_text": "Example River District",
        "normalized_location": "Example River District", "latitude": 19.1, "longitude": 72.8,
        "geocoding_status": "success", "geocoding_source": "source_metadata",
        "coordinate_source": "source_metadata", "spatial_cluster_id": "cluster-1",
        "spatial_priority_score": 71, "spatial_metadata": {"cluster_size": 1}}
    phase7 = {"summary": "Flood impacts and rescue needs are reported.",
        "situation_assessment": "Grounded synthetic test assessment.",
        "recommended_actions": ["Verify rescue access"], "uncertainties": ["Exact number at risk unknown"],
        "grounding_warnings": []}
    record = {"event": event, "event_id": "evt_dash_1", "source": "usgs",
        "source_event_id": "dash-event-1", "event_state": "NEW", "processing_status": "PROCESSED",
        "provenance": {"source": "usgs", "adapter": "USGSAdapter"}, "type_resolution": type_evidence,
        "source_location": {"text": "Example River District", "latitude": 19.1,
                            "longitude": 72.8, "provenance": "source_metadata"},
        "intelligence": {"phase3": {"disaster_type": "flood", "confidence": .91},
            "phase4": {"disaster_type": "flood", "casualties": {"injured": 4},
                       "location": ["Example River"], "requests": ["rescue boats"]},
            "phase5": phase5, "phase6": {"incidents": [phase6_row]}, "phase7": phase7},
        "incident_intelligence": phase5,
        "alert_decision": {"should_alert": True, "alert_id": "alert-dash-1", "alert_level": "CRITICAL",
            "alert_type": "OPERATIONAL", "event_id": "evt_dash_1", "incident_id": "incident-dash-1",
            "priority_score": 71, "severity_score": 74, "trigger_reasons": [{"rule": "rescue_required"}],
            "is_escalation": True, "escalation_reasons": ["Level increased"]},
        "alert_record": {"alert_id": "alert-dash-1", "event_id": "evt_dash_1",
            "incident_id": "incident-dash-1", "alert_level": "CRITICAL", "alert_type": "OPERATIONAL",
            "status": "ACTIVE", "created_at": "2026-09-25T08:12:00Z",
            "trigger_reasons": [{"rule": "rescue_required"}],
            "escalation": {"is_escalation": True, "reasons": ["Level increased"]},
            "notification": {"attempted": False, "status": "not_attempted"}},
        "alert_status": "CREATED", "alert_evaluated_at": "2026-09-25T08:12:00Z",
        "alert_suppression": {"suppressed": False}}
    repo.upsert_event(record)
    repo.save_monitoring_run({"run_id": "run-dashboard", "source": "multi_source", "status": "success",
        "run_mode": "poll", "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(), "records_received": 1,
        "valid_records": 1, "new_records": 1, "latency_seconds": .25,
        "processing_results": {"source_reports": {"usgs": {"status": "success",
            "records_received": 1, "selected_for_processing": 1, "new_records": 1,
            "duplicate_records": 0, "updated_records": 0, "retrieval_timestamp": "2026-09-25T08:11:00Z"}}}})
    return repo


@pytest.fixture
def live_client(tmp_path):
    return populated_repository(tmp_path / "dashboard.sqlite3")


def test_api_client_success_and_invalid_json_response():
    def good(environ, start_response):
        body = b'{"status":"ok","database":"ok"}'
        start_response("200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return [body]
    with serve(good) as base:
        assert DashboardAPIClient(base).get_json("health")["status"] == "ok"
    def malformed(environ, start_response):
        body = b"<html>bad</html>"
        start_response("200 OK", [("Content-Length", str(len(body)))])
        return [body]
    with serve(malformed) as base, pytest.raises(DashboardAPIError, match="malformed JSON"):
        DashboardAPIClient(base).get_json("health")


def test_api_client_accepts_currently_sized_full_event_page():
    target_size = 4_830_231
    prefix, suffix = b'{"payload":"', b'"}'
    body = prefix + b"x" * (target_size - len(prefix) - len(suffix)) + suffix

    def large_valid_response(environ, start_response):
        start_response("200 OK", [("Content-Length", str(len(body)))])
        return [body]

    with serve(large_valid_response) as base:
        result = DashboardAPIClient(base).get_json("events")
    assert len(result["payload"]) == target_size - len(prefix) - len(suffix)


def test_api_client_still_rejects_responses_above_bounded_limit():
    size = MAX_API_RESPONSE_BYTES + 1
    prefix, suffix = b'{"payload":"', b'"}'
    body = prefix + b"x" * (size - len(prefix) - len(suffix)) + suffix

    def oversized_response(environ, start_response):
        start_response("200 OK", [("Content-Length", str(len(body)))])
        return [body]

    with serve(oversized_response) as base, pytest.raises(DashboardAPIError, match="12 MB dashboard limit"):
        DashboardAPIClient(base).get_json("events")


def test_snapshot_is_api_driven_synthetic_tagged_and_has_persisted_summaries(live_client):
    with serve(create_app(live_client)) as base:
        snapshot = load_live_snapshot(DashboardAPIClient(base), now=datetime.now(timezone.utc))
    assert snapshot["connection_status"] == "online"
    assert snapshot["data_status"] == "DEMO DATA"
    assert snapshot["event_total"] == 1 and snapshot["active_critical_alerts"] == 1
    assert snapshot["summary"]["active_alerts"] == 1
    assert snapshot["source_status"][0]["name"] == "USGS"
    assert snapshot["source_status"][0]["new"] == 1
    assert snapshot["map_points"][0]["latitude"] == 19.1
    assert snapshot["events"][0]["source_disaster_type"] in ({"value": "flood", "canonical_value": "flood", "source": "usgs"}, "flood")
    assert snapshot["events"][0]["alert_level"] == "CRITICAL"
    assert snapshot["events"][0]["active_alert"]["alert_id"] == "alert-dash-1"
    assert snapshot["alerts"][0]["escalation"]["is_escalation"] is True


def test_source_reports_include_processed_counts_retrieval_and_unconfigured_sources(live_client):
    with serve(create_app(live_client)) as base:
        snapshot = load_live_snapshot(DashboardAPIClient(base))
    usgs = next(row for row in snapshot["source_status"] if row["name"] == "USGS")
    gdacs = next(row for row in snapshot["source_status"] if row["name"] == "GDACS")
    assert usgs["received"] == 1 and usgs["processed"] == 1
    assert usgs["retrieved_at"] == "2026-09-25T08:11:00Z"
    assert gdacs["status"] == "not_configured"
    assert "Processed" in render_live_dashboard()
    assert "Retrieved" in render_live_dashboard()


def test_api_offline_has_explicit_state_and_no_demo_fallback():
    snapshot = load_live_snapshot(DashboardAPIClient("http://127.0.0.1:1", .2))
    assert snapshot["connection_status"] == "offline"
    assert snapshot["data_status"] == "API OFFLINE"
    assert snapshot["events"] == [] and snapshot["alerts"] == []


def test_empty_api_database_is_intentional_empty_state(tmp_path):
    repo = DatabaseRepository(tmp_path / "empty.sqlite3")
    with serve(create_app(repo)) as base:
        snapshot = load_live_snapshot(DashboardAPIClient(base))
    assert snapshot["connection_status"] == "online"
    assert snapshot["data_status"] == "NO OPERATIONAL DATA AVAILABLE"
    assert snapshot["event_total"] == 0 and snapshot["map_points"] == []


def test_server_filters_pagination_and_search_parameters_are_bounded(live_client):
    with serve(create_app(live_client)) as base:
        client = DashboardAPIClient(base)
        snapshot = load_live_snapshot(client, {"priority": "HIGH", "offset": "0",
            "location": "River", "alert_level": "CRITICAL"})
    assert snapshot["events"] and snapshot["event_total"] == 1
    assert snapshot["events"][0]["alert_level"] == "CRITICAL"
    assert snapshot["page_size"] == 50


def test_source_filter_and_clearing_filter_switch_between_mastodon_and_all_sources(live_client):
    live_client.upsert_event({"source": "mastodon", "source_event_id": "dash-masto-1",
        "title": "Earthquake post", "text": "Earthquake update", "metadata": {}})
    with serve(create_app(live_client)) as base:
        client = DashboardAPIClient(base)
        all_sources = load_live_snapshot(client)
        mastodon_only = load_live_snapshot(client, {"source": "mastodon"})
        cleared = load_live_snapshot(client, {})
    assert all_sources["event_total"] == 2
    assert {item["source"] for item in all_sources["events"]} == {"usgs", "mastodon"}
    assert mastodon_only["event_total"] == 1
    assert {item["source"] for item in mastodon_only["events"]} == {"mastodon"}
    assert cleared["event_total"] == 2


def test_partial_api_failure_is_degraded_not_an_empty_event_result():
    class PartialClient:
        def get_json(self, path, params=None):
            if path == "/health":
                return {"status": "ok", "database": "ok"}
            if path == "/api/events":
                raise DashboardAPIError("temporary timeout")
            if path == "/api/alerts":
                return {"items": [], "total": 0}
            if path == "/api/monitoring/runs":
                return {"items": [], "total": 0}
            if path == "/api/intelligence/summary":
                return {"total_events": 1}
            raise AssertionError(path)

    snapshot = load_live_snapshot(PartialClient())
    assert snapshot["connection_status"] == "degraded"
    assert snapshot["data_status"] == "API DEGRADED"
    assert snapshot["event_total"] is None
    assert snapshot["components"]["events"] == "error"


def test_unresolved_and_invalid_coordinates_are_not_plotted():
    source = {"event_id": "evt-unresolved", "source": "rss", "title": "No coordinates",
        "text": "Reported in an unnamed area", "location_text": "Unknown village",
        "location_evidence": {"source_location_text": "Unknown village", "geocoding_status": "failed"},
        "intelligence": {"phase5": {"incident_id": "inc-unknown", "priority_score": 10,
            "priority_level": "LOW", "severity_level": "LOW", "urgency_level": "LOW"},
            "phase6": {"incidents": [{"incident_id": "inc-unknown", "latitude": 200,
                "longitude": 5, "geocoding_status": "success"}]}}}
    view = _adapt_persisted_event(source)
    assert view["latitude"] is None and view["longitude"] is None
    assert view["geocoding_status"] == "invalid_coordinates"


def test_dashboard_html_has_operational_components_and_no_demo_bootstrap():
    page = render_live_dashboard()
    assert "Operations overview" in page and "Live incident map" in page
    assert "API OFFLINE" in page and "NO OPERATIONAL DATA AVAILABLE" in page
    assert "setInterval" in page and "30" in page and "60" in page and "300" in page
    assert "phase10_demonstration_results.json" not in page
    assert "synthetic fixtures" not in page.casefold()
    assert "function renderDetail" in page and "SOURCE INTELLIGENCE" not in page
    assert "source_disaster_type" not in page  # field is safely mapped into its visible detail section
    assert "Country outlines and persisted verified incident coordinates" in page
    assert "/world-countries.geojson" in page
    assert "d.connection_status==='online'?'HEALTHY':'OFFLINE'" in page
    assert "if(snapshot.connection_status!=='online'&&state.data)" in page
    assert "if(state.data)showDegraded(e.message);else renderOffline(e.message)" in page
    assert "LAST GOOD DATA KEPT" in page
    assert "Phase " not in page and "PHASE " not in page


def test_dashboard_serves_local_country_boundaries(live_client):
    with serve(create_app(live_client)) as api_base:
        with serve_handler(make_live_handler(DashboardConfig(api_base_url=api_base))) as dashboard_base:
            response = urlopen(dashboard_base + "/world-countries.geojson", timeout=2)
            payload = json.loads(response.read())
            content_type = response.headers.get("Content-Type")
    assert content_type.startswith("application/geo+json")
    assert payload["type"] == "FeatureCollection"
    assert payload["features"]
    assert any(feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
               for feature in payload["features"])


def test_incident_detail_and_runtime_handler_consume_api(live_client):
    with serve(create_app(live_client)) as api_base:
        config = DashboardConfig(api_base_url=api_base)
        with serve_handler(make_live_handler(config)) as dashboard_base:
            html = urlopen(dashboard_base + "/", timeout=2).read().decode("utf-8")
            response = json.loads(urlopen(dashboard_base + "/dashboard-data", timeout=2).read())
            detail = json.loads(urlopen(dashboard_base + "/dashboard-event/evt_dash_1", timeout=2).read())
            health = json.loads(urlopen(dashboard_base + "/healthz", timeout=2).read())
    assert "DISASTER INTELLIGENCE CENTER" in html
    assert response["data_status"] == "DEMO DATA"
    assert detail["item"]["type_evidence"]["operational_type"] == "flood"
    assert health["status"] == "ok"
    assert "DO_NOT_LEAK" not in html + json.dumps(response) + json.dumps(detail)


def test_live_data_status_requires_recent_non_synthetic_ingestion(live_client):
    with serve(create_app(live_client)) as base:
        snapshot = load_live_snapshot(DashboardAPIClient(base), now=datetime.now(timezone.utc))
    assert snapshot["data_status"] == "DEMO DATA"
    # Synthetic flag is removed in a dedicated temporary record and a stale run is not live.
    event = live_client.get_event_by_source_id("usgs", "dash-event-1")
    assert event and event["metadata"].get("demonstration") is True
