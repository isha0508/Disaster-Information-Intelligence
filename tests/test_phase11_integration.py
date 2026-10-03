"""Phase 11.3 live pipeline persistence integration tests; all databases are temporary."""

import io
import json

from api.app import create_app
from database import DatabaseRepository
from monitoring.config import MonitoringConfig
from monitoring.live import LiveMonitoringSession
from monitoring.pipeline import IntelligencePipeline
from monitoring.real_sources import MultiSource, RealSourceConfig
from monitoring.runner import MonitoringRunner


class GoodSource:
    name = "controlled_feed"

    def fetch(self):
        return [{"source": "controlled_feed", "source_event_id": "stable-1",
            "title": "Flood rescue operation", "text": "Severe flood trapped 12 people; 4 injured and urgent rescue teams are needed.",
            "location_text": "River District", "event_timestamp": "2026-09-25T08:00:00Z",
            "observed_at": "2026-09-25T08:05:00Z", "retrieved_at": "2026-09-25T08:06:00Z",
            "metadata": {"source_kind": "controlled_test"}}]


class BrokenSource:
    name = "broken_feed"

    def fetch(self):
        raise RuntimeError("controlled source unavailable")


def app_call(app, path):
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": "",
               "wsgi.input": io.BytesIO(), "SERVER_NAME": "localhost", "SERVER_PORT": "80",
               "wsgi.url_scheme": "http"}
    status = []
    def start_response(value, headers):
        status.append(int(value.split()[0]))
    body = b"".join(app(environ, start_response))
    return status[0], json.loads(body)


def make_session(repository):
    config = RealSourceConfig(usgs_enabled=False, gdacs_enabled=False, news_enabled=False,
                              max_events_per_source=5)
    runner = MonitoringRunner(config=MonitoringConfig(run_phase3=False),
                              pipeline=IntelligencePipeline(run_phase3=False))
    sources = MultiSource([GoodSource(), BrokenSource()])
    return LiveMonitoringSession(config, event_source=sources, runner=runner,
                                 repository=repository)


def test_live_to_database_to_api_duplicate_update_and_partial_failure(tmp_path):
    repository = DatabaseRepository(tmp_path / "integration.sqlite3")
    session = make_session(repository)

    first = session.run_cycle()
    assert first["persistence_status"] == "ok"
    assert first["source_reports"]["broken_feed"]["status"] == "error"
    assert first["events"][0]["database_event_state"] == "NEW"
    event_id = first["events"][0]["event_id"]
    assert first["alerts"] and first["alerts"][0].get("alert_record")
    assert len(repository.list_events()) == 1
    stored = repository.get_event(event_id)
    assert stored["provenance"] and stored["type_evidence"]
    assert stored["incident_intelligence"] and stored["alerts"]
    first_alert_ids = {alert["alert_id"] for alert in stored["alerts"] if alert.get("alert_id")}
    assert first_alert_ids and repository.get_alert_history(next(iter(first_alert_ids)))
    assert stored["intelligence"].get("phase4") and stored["intelligence"].get("phase7")
    run = repository.get_monitoring_run(first["monitoring_run_id"])
    assert run["status"] == "partial_failure" and run["latency_seconds"] is not None

    status, response = app_call(create_app(repository), "/api/events")
    assert status == 200 and response["total"] == 1
    assert app_call(create_app(repository), "/api/alerts")[1]["total"] >= 1
    assert app_call(create_app(repository), "/api/monitoring/runs")[1]["total"] == 1
    assert app_call(create_app(repository), "/api/intelligence/summary")[1]["total_events"] == 1

    duplicate = session.run_cycle()
    assert duplicate["events"][0]["database_event_state"] == "DUPLICATE"
    assert len(repository.list_events()) == 1
    assert len(repository.get_event(event_id)["revisions"]) == 1
    duplicate_alert_ids = {alert["alert_id"] for alert in repository.get_event(event_id)["alerts"]
                           if alert.get("alert_id")}
    assert duplicate_alert_ids == first_alert_ids

    original_fetch = session.source.sources[0].fetch
    def updated_fetch():
        record = original_fetch()[0]
        record["text"] += " Updated official casualty count: 6 injured."
        record["title"] = "Updated flood rescue operation"
        record["updated_at"] = "2026-09-25T08:10:00Z"
        return [record]
    session.source.sources[0].fetch = updated_fetch
    updated = session.run_cycle()
    assert updated["events"][0]["database_event_state"] == "UPDATED"
    latest = repository.get_event(event_id)
    assert latest["version"] == 2 and len(latest["revisions"]) == 2
    assert len(repository.list_events()) == 1
