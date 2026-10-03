"""Phase 11.8 end-to-end integration checks with deterministic mocked sources."""

import json
import sys
from threading import Thread
from types import ModuleType
from wsgiref.simple_server import make_server

from api.app import create_app
from dashboard.api_client import DashboardAPIClient
from dashboard.live_data import load_live_snapshot
from database import DatabaseRepository
from monitoring.live import LiveMonitoringSession
from monitoring.pipeline import IntelligencePipeline
from monitoring.real_sources import MultiSource, RealSourceConfig
from monitoring.runner import MonitoringRunner
from monitoring.config import MonitoringConfig


class MockPublicSource:
    def __init__(self, name):
        self.name = name

    def fetch_with_report(self):
        event_id = f"{self.name}-event-1"
        event = {
            "source": self.name,
            "source_event_id": event_id,
            "title": f"{self.name} flood report",
            "text": "Flooding reported with damaged roads; response teams requested.",
            "disaster_type": "flood",
            "location_text": "Test River District",
            "latitude": 19.0760,
            "longitude": 72.8777,
            "event_timestamp": "2026-09-25T08:00:00Z",
            "source_url": f"https://example.invalid/{event_id}",
            "metadata": {"source_kind": "mocked_integration_test", "demonstration": True},
        }
        return [event], {
            "source": self.name,
            "status": "success",
            "records_received": 1,
            "valid_records": 1,
            "invalid_records": 0,
            "errors": [],
            "retrieval_timestamp": "2026-09-25T08:01:00Z",
        }


def test_phase11_8_mock_sources_persist_through_api_and_dashboard(tmp_path):
    """All four source identities traverse the shared operational path."""
    repository = DatabaseRepository(tmp_path / "phase11_8.sqlite3")
    sources = [MockPublicSource(name) for name in ("usgs", "gdacs", "rss", "mastodon")]
    source_config = RealSourceConfig(
        usgs_enabled=False,
        gdacs_enabled=False,
        news_enabled=False,
        max_events_per_source=5,
    )
    runner = MonitoringRunner(
        config=MonitoringConfig(run_phase3=False),
        pipeline=IntelligencePipeline(run_phase3=False),
    )
    session = LiveMonitoringSession(
        source_config,
        event_source=MultiSource(sources),
        runner=runner,
        repository=repository,
    )

    cycle = session.run_cycle()
    assert cycle["persistence_status"] == "ok"
    assert cycle["status"] == "success"
    assert cycle["summary"]["event_states"] == {"NEW": 4}
    assert len(repository.list_events()) == 4
    run = repository.get_monitoring_run(cycle["monitoring_run_id"])
    assert run["status"] == "success" and run["ended_at"]
    assert {event["source"] for event in repository.list_events()} == {
        "usgs", "gdacs", "rss", "mastodon"
    }

    # Exercise a real local HTTP API server and the same API client used by the
    # dashboard; the records are mocked at source boundaries, never labelled live.
    server = make_server("127.0.0.1", 0, create_app(repository))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = DashboardAPIClient(f"http://127.0.0.1:{server.server_port}")
        events_response = client.get_json("/api/events", {"limit": 50, "offset": 0})
        run_response = client.get_json(f"/api/monitoring/runs/{cycle['monitoring_run_id']}")
        assert events_response["total"] == 4
        assert run_response["item"]["status"] == "success"
        snapshot = load_live_snapshot(client)
        assert snapshot["connection_status"] == "online"
        assert snapshot["data_status"] == "DEMO DATA"
        assert snapshot["event_total"] == 4
        assert {event["source"] for event in snapshot["events"]} == {
            "usgs", "gdacs", "rss", "mastodon"
        }
        assert all(event["latitude"] == 19.0760 for event in snapshot["events"])
        json.dumps(snapshot, allow_nan=False)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_phase3_exception_keeps_source_event_and_continues_downstream(tmp_path, monkeypatch):
    """A classifier exception is recorded without losing authoritative input."""
    def fail_classification(_text):
        raise RuntimeError("controlled Phase 3 failure")

    fake_predict = ModuleType("models.predict")
    fake_predict.predict_all = fail_classification
    monkeypatch.setitem(sys.modules, "models.predict", fake_predict)

    repository = DatabaseRepository(tmp_path / "phase3_failure.sqlite3")
    config = RealSourceConfig(usgs_enabled=False, gdacs_enabled=False, news_enabled=False)
    runner = MonitoringRunner(
        config=MonitoringConfig(run_phase3=True),
        pipeline=IntelligencePipeline(run_phase3=True),
    )
    session = LiveMonitoringSession(
        config,
        event_source=MultiSource([MockPublicSource("usgs")]),
        runner=runner,
        repository=repository,
    )

    cycle = session.run_cycle()
    assert cycle["persistence_status"] == "ok"
    assert cycle["status"] == "partial_failure"
    event = cycle["events"][0]
    assert event["phase_status"]["phase3"] == "failed"
    assert event["phase_status"]["phase4"] == "completed"
    assert event["phase_status"]["phase5"] == "completed"
    assert event["phase_status"]["phase6"] == "completed"
    assert event["phase_status"]["phase7"] == "completed"
    assert event["type_resolution"]["canonical_disaster_type"] == "flood"
    assert len(repository.list_events()) == 1
    assert repository.get_monitoring_run(cycle["monitoring_run_id"])["status"] == "partial_failure"
