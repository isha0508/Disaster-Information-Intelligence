"""Deterministic local deployment smoke test (synthetic fixture; never a live-source check)."""

import json
import os
from pathlib import Path
import sys
import sqlite3
import socket
import tempfile
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.request import urlopen
from wsgiref.simple_server import make_server
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from api.app import create_app
from dashboard.app import make_live_handler
from dashboard.config import DashboardConfig
from database import DatabaseRepository, initialize_database
from monitoring.live import LiveMonitoringSession
from monitoring.real_sources import RealSourceConfig, MultiSource, configured_sources


class SmokeFixtureSource:
    name = "synthetic_phase11_9_smoke"

    def fetch(self):
        return [{
            "source": self.name,
            "source_type": "synthetic",
            "source_event_id": "phase11-9-smoke-1",
            "title": "SMOKE FIXTURE: local synthetic flood report",
            "text": "SMOKE FIXTURE ONLY: synthetic flood report; no real incident data.",
            "observed_at": "2026-09-30T00:00:00Z",
            "metadata": {"synthetic_data": True, "smoke_test": True},
        }]


def _json(url):
    with urlopen(url, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _reserve_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(server):
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _serve_wsgi(server):
    """Use bounded handle_request calls for clean, deterministic Windows shutdown."""
    stopping = Event()
    server.timeout = 0.2

    def serve_requests():
        while not stopping.is_set():
            server.handle_request()

    thread = Thread(target=serve_requests, daemon=True)
    thread.start()
    return thread, stopping


def main():
    configured = configured_sources(RealSourceConfig(
        usgs_enabled=True, gdacs_enabled=True, news_enabled=True,
        rss_feeds=("https://www.gdacs.org/xml/rss.xml",), mastodon_enabled=True))
    configured_names = sorted(source.name for source in configured)
    assert configured_names == ["gdacs", "mastodon", "news_rss", "usgs"], configured_names

    temp_root = ROOT / "tmp" / "phase11_9_deployment_smoke"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=temp_root) as temporary:
        db_path = Path(temporary) / "smoke.sqlite3"
        initialize_database(db_path)
        repository = DatabaseRepository(db_path)
        assert repository.health_check(), "SQLite repository health check failed"
        connection = sqlite3.connect(db_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        assert {"events", "monitoring_runs"}.issubset(tables), tables

        # All configured public sources are disabled; one clearly marked local fixture
        # enters the exact existing monitoring lifecycle and Phase 3–9 pipeline.
        source_config = RealSourceConfig(
            usgs_enabled=False, gdacs_enabled=False, news_enabled=False,
            polling_interval_seconds=360, max_events_per_source=1)
        session = LiveMonitoringSession(source_config, event_source=MultiSource([SmokeFixtureSource()]),
                                        repository=repository)
        cycle = session.run_cycle()
        assert cycle["status"] in {"success", "partial_failure"}, cycle
        assert cycle["persistence_status"] == "ok", cycle
        assert len(cycle["events"]) == 1, cycle
        event = cycle["events"][0]
        event_id = event["event_id"]
        assert event["event_state"] == "NEW", event
        stored = repository.get_event(event_id)
        assert stored and stored.get("metadata", {}).get("synthetic_data") is True
        assert repository.get_monitoring_run(cycle["monitoring_run_id"])
        before = len(repository.list_events(limit=10))
        initialize_database(db_path)
        assert len(repository.list_events(limit=10)) == before, "schema initialization removed existing data"

        phases = event.get("phase_status", {})
        for phase in ("phase3", "phase4", "phase5", "phase6", "phase7"):
            assert str(phases.get(phase, "")).startswith("completed"), (phase, phases)
            assert phase in stored.get("intelligence", {}), (phase, stored.keys())
        phase3 = stored["intelligence"]["phase3"]
        assert not _has_model_error(phase3), phase3

        api_server = make_server("127.0.0.1", 0, create_app(repository))
        api_thread, stop_api = _serve_wsgi(api_server)
        api_url = f"http://127.0.0.1:{api_server.server_port}"
        dashboard_server = ThreadingHTTPServer(("127.0.0.1", 0), make_live_handler(
            DashboardConfig(host="127.0.0.1", port=0, api_base_url=api_url)))
        dashboard_thread = _serve(dashboard_server)
        try:
            for path in ("/health", "/healthz"):
                status, health = _json(api_url + path)
                assert status == 200 and health.get("database") == "ok", (path, health)
            _, listing = _json(api_url + "/api/events?limit=10")
            assert any(item.get("event_id") == event_id for item in listing.get("items", [])), listing

            dashboard_url = f"http://127.0.0.1:{dashboard_server.server_port}"
            status, health = _json(dashboard_url + "/healthz")
            assert status == 200 and health.get("status") == "ok", health
            _, snapshot = _json(dashboard_url + "/dashboard-data")
            assert snapshot.get("connection_status") == "online", snapshot
            assert snapshot.get("data_status") == "DEMO DATA", snapshot
            assert any(item.get("event_id") == event_id for item in snapshot.get("events", [])), snapshot
        finally:
            dashboard_server.shutdown()
            dashboard_thread.join(timeout=5)
            dashboard_server.server_close()
            stop_api.set()
            api_thread.join(timeout=5)
            api_server.server_close()

        class UnhealthyRepository:
            def health_check(self):
                return False

        unhealthy_api = make_server("127.0.0.1", 0, create_app(UnhealthyRepository()))
        unhealthy_thread, stop_unhealthy_api = _serve_wsgi(unhealthy_api)
        try:
            for path in ("/health", "/healthz"):
                try:
                    _json(f"http://127.0.0.1:{unhealthy_api.server_port}{path}")
                    raise AssertionError(f"{path} unexpectedly returned HTTP 200")
                except HTTPError as exc:
                    assert exc.code == 503, (path, exc.code)
        finally:
            stop_unhealthy_api.set()
            unhealthy_thread.join(timeout=5)
            unhealthy_api.server_close()

        # Confirm an API outage remains explicit and does not load fallback records.
        dead_api = f"http://127.0.0.1:{_reserve_port()}"
        offline_server = ThreadingHTTPServer(("127.0.0.1", 0), make_live_handler(
            DashboardConfig(host="127.0.0.1", port=0, api_base_url=dead_api, api_timeout_seconds=0.2)))
        offline_thread = _serve(offline_server)
        try:
            offline_url = f"http://127.0.0.1:{offline_server.server_port}"
            try:
                _json(offline_url + "/healthz")
                raise AssertionError("offline dashboard health unexpectedly returned HTTP 200")
            except HTTPError as exc:
                assert exc.code == 503
            _, offline = _json(offline_url + "/dashboard-data")
            assert offline.get("data_status") == "API OFFLINE" and not offline.get("events"), offline
        finally:
            offline_server.shutdown()
            offline_thread.join(timeout=5)
            offline_server.server_close()

        result = {
            "result": "PASS",
            "data_classification": "MOCKED / SYNTHETIC TEST DATA ONLY; no live-source check performed",
            "database_health": "PASS",
            "configured_public_adapters": configured_names,
            "monitoring_cycle": cycle["status"],
            "monitoring_run_persisted": True,
            "event_persisted": True,
            "pipeline_phases_persisted": ["phase3", "phase4", "phase5", "phase6", "phase7"],
            "api_health_and_event_visibility": "PASS",
            "api_database_failure_health": "PASS (HTTP 503)",
            "dashboard_api_connectivity_and_event_visibility": "PASS",
            "dashboard_offline_state_without_fallback": "PASS",
            "existing_database_content_preserved_by_reinitialization": "PASS",
            "event_id": event_id,
            "database_path": "temporary isolated database (removed after test)",
        }
        print(json.dumps(result, indent=2))


def _has_model_error(value):
    if isinstance(value, dict):
        return any(key in {"CHECKPOINT_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE"} or
                   (key == "error" and item in {"CHECKPOINT_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE"})
                   for key, item in value.items()) or any(_has_model_error(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_model_error(item) for item in value)
    return False


if __name__ == "__main__":
    main()
