"""Phase 11.2 WSGI API tests with isolated SQLite repositories."""

import io
import json

import pytest

from api.app import create_app
from database import DatabaseRepository


@pytest.fixture
def repo(tmp_path):
    value = DatabaseRepository(tmp_path / "api.sqlite3")
    for index in range(3):
        event = {"source": "usgs" if index < 2 else "gdacs", "source_event_id": f"api-{index}",
            "title": f"Flood report {index}", "text": "Severe flood and rescue activity",
            "location_text": "River District", "disaster_type": "flood",
            "event_timestamp": f"2026-09-2{index+1}T10:00:00Z",
            "metadata": {"api_key": "NEVER_EXPOSE", "safe": True}}
        incident = {"incident_id": f"incident-{index}", "severity_score": 80,
            "severity_level": "HIGH", "urgency_score": 75, "urgency_level": "HIGH",
            "confidence_score": 90, "confidence_level": "HIGH", "priority_score": 78,
            "priority_level": "HIGH", "canonical_disaster_type": "flood",
            "decision_flags": ["RESCUE_REQUIRED"],
            "resource_estimates": {"rescue": 2}}
        alert_id = f"alert-{index}"
        alert = {"alert_decision": {"should_alert": True, "alert_id": alert_id,
                    "alert_level": "HIGH", "alert_type": "OPERATIONAL", "event_id": None,
                    "incident_id": incident["incident_id"], "trigger_reasons": ["high priority"],
                    "is_escalation": False},
                "alert_record": {"alert_id": alert_id, "incident_id": incident["incident_id"],
                    "alert_level": "HIGH", "alert_type": "OPERATIONAL", "status": "ACTIVE",
                    "created_at": "2026-09-25T10:00:00Z", "notification": {"status": "not_attempted"}},
                "alert_status": "CREATED", "alert_evaluated_at": "2026-09-25T10:00:00Z"}
        value.upsert_event({**event, **alert, "incident_intelligence": incident,
            "intelligence": {"phase3": {"label": "flood"}, "phase4": {"disaster_type": "flood"},
                             "phase5": incident, "phase6": {"spatial_priority_score": 75},
                             "phase7": {"summary": "grounded"}}})
        stored = value.get_event_by_source_id(event["source"], event["source_event_id"])
        # The alert fixture gets the persisted event ID from Phase 11.1 in normal integrations;
        # attach it here to exercise event-linked alert retrieval.
        if stored["alerts"][0]["event_id"] is None:
            value.save_alert({**alert, "event_id": stored["event_id"],
                             "alert_decision": {**alert["alert_decision"], "event_id": stored["event_id"]}},
                            event_id=stored["event_id"], incident_id=incident["incident_id"])
        value.save_alert_history({"alert_id": alert_id, "event_id": stored["event_id"],
            "incident_id": incident["incident_id"], "from_state": None, "to_state": "HIGH",
            "change_type": "CREATED", "occurred_at": "2026-09-25T10:00:00Z"})
    value.save_monitoring_run({"run_id": "run-api", "source": "usgs", "status": "partial_failure",
        "started_at": "2026-09-25T10:00:00Z", "ended_at": "2026-09-25T10:00:02Z",
        "records_received": 2, "valid_records": 2, "new_records": 2, "errors": [{"source": "gdacs"}],
        "latency_seconds": 2.0, "processing_results": {"records_processed": 2}})
    return value


def call(app, path, query="", method="GET"):
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
               "wsgi.input": io.BytesIO(), "SERVER_NAME": "localhost", "SERVER_PORT": "80",
               "wsgi.url_scheme": "http"}
    captured = {}
    def start(status, headers):
        captured["status"] = int(status.split()[0])
        captured["headers"] = dict(headers)
    body = b"".join(app(environ, start))
    captured["json"] = json.loads(body)
    return captured


def test_health_checks_database_and_healthz_alias(repo):
    app = create_app(repo)
    assert call(app, "/health")["json"] == {"status": "ok", "phase": "11.2", "database": "ok"}
    assert call(app, "/healthz")["status"] == 200


def test_events_filter_pagination_and_detail_include_processing_phases(repo):
    app = create_app(repo)
    response = call(app, "/api/events", "source=usgs&severity=HIGH&priority=HIGH&limit=1&offset=1")
    assert response["status"] == 200
    assert response["json"]["total"] == 2 and len(response["json"]["items"]) == 1
    item = response["json"]["items"][0]
    detail = call(app, "/api/events/" + item["event_id"])["json"]["item"]
    assert {"phase3", "phase4", "phase5", "phase6", "phase7"} <= set(detail["intelligence"])
    assert detail["provenance"] is not None


def test_events_can_omit_expensive_history_while_detail_keeps_it(repo):
    app = create_app(repo)
    compact = call(app, "/api/events", "source=usgs&limit=1&include_history=false")["json"]["items"][0]
    assert compact["revisions"] == []
    assert compact["alerts"] == []
    assert compact["alert_history"] == []
    assert "phase3" in compact["intelligence"]
    full = call(app, "/api/events", "source=usgs&limit=1")["json"]["items"][0]
    assert full["revisions"]
    assert full["alerts"]


def test_event_operational_type_location_and_time_filters(repo):
    app = create_app(repo)
    result = call(app, "/api/events", "operational_type=flood&location=River&since=2026-09-22T00%3A00%3A00Z")
    assert result["json"]["total"] == 2


def test_alert_listing_detail_history_and_monitoring_runs(repo):
    app = create_app(repo)
    listed = call(app, "/api/alerts", "alert_level=HIGH&active=true&source=usgs")
    assert listed["json"]["total"] == 2
    alert_id = listed["json"]["items"][0]["alert_id"]
    detail = call(app, f"/api/alerts/{alert_id}")["json"]["item"]
    assert detail["event_id"] and detail["history"]
    assert call(app, f"/api/alerts/{alert_id}/history")["json"]["items"]
    runs = call(app, "/api/monitoring/runs", "limit=1")
    assert runs["json"]["total"] == 1 and runs["json"]["items"][0]["status"] == "partial_failure"
    assert call(app, "/api/monitoring/runs/run-api")["json"]["item"]["latency_seconds"] == 2.0


def test_summary_is_derived_from_persisted_records(repo):
    summary = call(create_app(repo), "/api/intelligence/summary")["json"]
    assert summary["total_events"] == 3
    assert summary["events_by_source"] == {"gdacs": 1, "usgs": 2}
    assert summary["active_alerts"] == 3
    assert summary["recent_monitoring_status"]["status"] == "partial_failure"


def test_summary_counts_authoritative_source_coordinate_pairs_as_resolved(tmp_path):
    repository = DatabaseRepository(tmp_path / "coordinates.sqlite3")
    repository.upsert_event({"source": "usgs", "source_event_id": "coordinate-event",
        "title": "Earthquake", "text": "Earthquake report", "latitude": 12.5,
        "longitude": 45.6, "metadata": {},
        "source_location": {"latitude": 12.5, "longitude": 45.6,
                             "provenance": "authoritative_structured_metadata",
                             "coordinate_source": "source_metadata"},
        "intelligence": {"phase6": {"incidents": [{"incident_id": "i-1",
            "latitude": 12.5, "longitude": 45.6, "geocoding_status": "success",
            "coordinate_source": "source_metadata"}]}}})
    summary = call(create_app(repository), "/api/intelligence/summary")["json"]
    assert summary["unresolved_locations"] == 0


def test_missing_items_bad_parameters_methods_and_secrets(repo):
    app = create_app(repo)
    assert call(app, "/api/events/missing")["status"] == 404
    assert call(app, "/api/alerts/missing")["status"] == 404
    assert call(app, "/api/monitoring/runs/missing")["status"] == 404
    assert call(app, "/api/events", "limit=0")["status"] == 400
    assert call(app, "/api/events", "since=not-a-time")["status"] == 400
    assert call(app, "/api/alerts", "active=maybe")["status"] == 400
    assert call(app, "/api/events", method="POST")["status"] == 405
    body = json.dumps(call(app, "/api/events")["json"])
    assert "NEVER_EXPOSE" not in body and "api_key" not in body
