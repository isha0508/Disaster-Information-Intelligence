"""Offline tests for the Phase 11.6 service over the Phase 8–11.3 APIs."""

from concurrent.futures import ThreadPoolExecutor
import io
import logging
import os
import subprocess
import sys
from threading import Lock
from time import sleep

import pytest

from api.app import create_app
from database import DatabaseRepository
from monitoring.config import MonitoringConfig
from monitoring.identity import event_id_for
from monitoring.live import LiveMonitoringSession, main
from monitoring.normalization import normalize_event
from monitoring.real_sources import MultiSource, RealSourceConfig, configured_sources
from monitoring.runner import MonitoringRunner
from monitoring.service import (ContinuousMonitoringService, MonitoringServiceConfig,
                                RetryingEventSource)


def source_config(**kwargs):
    return RealSourceConfig(usgs_enabled=False, gdacs_enabled=False, news_enabled=False,
                            max_events_per_source=5, **kwargs)


class EventSource:
    name = "test_feed"

    def __init__(self, events=None, reports=None):
        self.events = list(events or [])
        self.source_reports = reports or {self.name: {"status": "success",
            "records_received": len(self.events), "valid_records": len(self.events), "errors": []}}

    def fetch(self):
        return list(self.events)


class ScriptedReportSource:
    name = "scripted"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def fetch_with_report(self):
        self.calls += 1
        value = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(value, Exception):
            raise value
        return value


class StubPipeline:
    def process(self, event):
        return {"event_id": event_id_for(event), "source": event["source"],
            "source_event_id": event.get("source_event_id"), "provenance": event["provenance"],
            "processing_status": "PROCESSED", "processing_errors": [],
            "intelligence": {"phase5": {"incident_id": "inc-" + event["source_event_id"],
                "disaster_type": "flood", "severity_score": 70, "severity_level": "HIGH",
                "urgency_score": 75, "urgency_level": "HIGH", "priority_score": 72,
                "priority_level": "HIGH", "confidence_score": 80, "confidence_level": "HIGH",
                "decision_flags": ["RESCUE_REQUIRED"]},
                "phase6": {"incidents": [{"incident_id": "inc-" + event["source_event_id"],
                    "latitude": 19.1, "longitude": 72.8, "geocoding_status": "success",
                    "coordinate_source": "source_metadata"}]},
                "phase4": {"disaster_type": "flood", "rescue": True}},
            "phase_status": {"phase3": "completed", "phase4": "completed",
                "phase5": "completed", "phase6": "completed", "phase7": "completed"},
            "phases_completed": ["phase3", "phase4", "phase5", "phase6", "phase7"],
            "phases_failed": []}


def event(source_id="one", text="Flood rescue request at River District", **updates):
    value = {"source": "test_feed", "source_event_id": source_id,
        "title": "Flood response", "text": text, "location_text": "River District",
        "latitude": 19.1, "longitude": 72.8, "event_timestamp": "2026-09-25T08:00:00Z",
        "metadata": {"alert": "orange"}}
    value.update(updates)
    return value


def session_for(repository=None, source=None):
    config = source_config()
    runner = MonitoringRunner(config=MonitoringConfig(run_phase3=False), pipeline=StubPipeline())
    return LiveMonitoringSession(config, event_source=source or MultiSource([EventSource([event()])]),
        runner=runner, repository=repository)


def test_module_help_aliases_are_clean_and_do_not_import_target_early():
    for flag in ("--help", "-h"):
        result = subprocess.run([sys.executable, "-m", "monitoring.live", flag],
            text=True, capture_output=True, timeout=15)
        assert result.returncode == 0
        assert "--once" in result.stdout and "--retry-count" in result.stdout
        assert "RuntimeWarning" not in result.stderr


def test_service_configuration_from_env_and_bounds(monkeypatch):
    monkeypatch.setenv("DISASTER_POLL_INTERVAL", "120")
    monkeypatch.setenv("DISASTER_SOURCE_RETRY_COUNT", "3")
    monkeypatch.setenv("DISASTER_SOURCE_RETRY_BACKOFF", "2.5")
    cfg = MonitoringServiceConfig.from_env(source_config())
    assert (cfg.interval_seconds, cfg.retry_count, cfg.retry_backoff_seconds) == (120, 3, 2.5)
    with pytest.raises(ValueError):
        MonitoringServiceConfig(interval_seconds=20)
    with pytest.raises(ValueError):
        MonitoringServiceConfig(retry_count=11)


def test_cli_once_invokes_exactly_one_service_cycle(monkeypatch, tmp_path):
    monkeypatch.setenv("USGS_ENABLED", "false")
    monkeypatch.setenv("GDACS_ENABLED", "false")
    monkeypatch.setenv("NEWS_ENABLED", "false")
    monkeypatch.setenv("DISASTER_DB_PATH", str(tmp_path / "cli.sqlite3"))
    calls = []
    class FakeService:
        def __init__(self, **kwargs):
            pass
        def run(self, *, once=False):
            calls.append(once)
            return [{"status": "success"}]
        def request_stop(self):
            pass
    monkeypatch.setattr("monitoring.service.ContinuousMonitoringService", FakeService)
    assert main(["--once"]) == 0
    assert calls == [True]


def test_successful_cycle_persists_run_lifecycle_events_and_alerts(tmp_path):
    repo = DatabaseRepository(tmp_path / "cycle.sqlite3")
    source = EventSource([event()], {"test_feed": {"status": "success", "records_received": 1,
        "valid_records": 1, "errors": [], "retrieval_timestamp": "2026-09-25T08:01:00Z"}})
    session = session_for(repo, MultiSource([source]))
    result = session.run_cycle()
    run = repo.get_monitoring_run(result["monitoring_run_id"])
    assert run["status"] == "success" and run["started_at"] and run["ended_at"]
    assert run["run_mode"] == "poll"
    assert run["configuration_summary"]["phase11_6_continuous_service"] is True
    assert result["run_start_persisted"] is True and result["persistence_status"] == "ok"
    assert result["records_received"] == 1 and result["selected_records"] == 1
    assert result["summary"]["alerts_evaluated"] == 1
    assert len(repo.list_events()) == 1
    stored = repo.list_events()[0]
    assert stored["provenance"]["source"] == "test_feed"
    assert stored["intelligence"]["phase5"]["priority_level"] == "HIGH"
    assert stored["alerts"]


def test_partial_failure_preserves_successful_source_and_records_run(tmp_path):
    repo = DatabaseRepository(tmp_path / "partial.sqlite3")
    good = EventSource([event()], {"test_feed": {"status": "success", "records_received": 1,
        "valid_records": 1, "errors": []}})
    bad = ScriptedReportSource([([], {"source": "unavailable", "status": "error",
        "records_received": 0, "errors": ["TimeoutError: timed out"]})])
    bad.name = "unavailable"
    result = session_for(repo, MultiSource([good, bad])).run_cycle()
    assert result["status"] == "partial_failure"
    assert result["source_reports"]["test_feed"]["new_records"] == 1
    assert result["source_reports"]["unavailable"]["status"] == "error"
    assert len(repo.list_events()) == 1
    assert repo.get_monitoring_run(result["monitoring_run_id"])["status"] == "partial_failure"


def test_all_public_source_adapters_are_enabled_by_default(monkeypatch):
    for name in ("USGS_ENABLED", "GDACS_ENABLED", "NEWS_ENABLED", "DISASTER_MASTODON_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    config = RealSourceConfig.from_env()
    assert config.usgs_enabled and config.gdacs_enabled and config.news_enabled and config.mastodon_enabled
    assert [source.name for source in configured_sources(config)] == ["usgs", "gdacs", "news_rss", "mastodon"]


@pytest.mark.parametrize("failed_source", ["usgs", "gdacs", "news_rss", "mastodon"])
def test_each_source_failure_is_isolated_from_all_other_sources(failed_source):
    class Source:
        def __init__(self, name):
            self.name = name

        def fetch(self):
            if self.name == failed_source:
                raise RuntimeError("temporary source failure")
            return [{"source": self.name, "source_event_id": self.name, "text": "event"}]

    names = ["usgs", "gdacs", "news_rss", "mastodon"]
    group = MultiSource([Source(name) for name in names])
    events = group.fetch()
    assert {item["source"] for item in events} == set(names) - {failed_source}
    assert group.source_reports[failed_source]["status"] == "error"
    assert all(group.source_reports[name]["status"] == "success"
               for name in set(names) - {failed_source})


def test_all_sources_failed_is_persisted_as_failed_not_success(tmp_path):
    repo = DatabaseRepository(tmp_path / "failed.sqlite3")
    failing = ScriptedReportSource([([], {"source": "test_feed", "status": "error",
        "records_received": 0, "errors": ["HTTP status 400"]})])
    failing.name = "test_feed"
    result = session_for(repo, MultiSource([failing])).run_cycle()
    assert result["status"] == "failed" and result["selected_records"] == 0
    assert repo.get_monitoring_run(result["monitoring_run_id"])["status"] == "failed"


def test_retry_adapter_retries_transient_failures_with_bounded_exponential_backoff():
    source = ScriptedReportSource([
        ([], {"status": "error", "error": "SourceFetchError: HTTP status 503", "errors": ["HTTP status 503"]}),
        ([], {"status": "error", "error": "request timed out", "errors": ["request timed out"]}),
        ([{"id": "ok"}], {"status": "success", "records_received": 1, "errors": []}),
    ])
    waits = []
    adapter = RetryingEventSource(source, retry_count=2, backoff_seconds=1,
        sleep=waits.append, logger=logging.getLogger("test.retry"))
    events, report = adapter.fetch_with_report()
    assert events == [{"id": "ok"}] and source.calls == 3
    assert waits == [1, 2]
    assert report["attempt_count"] == 3 and len(report["retry_attempts"]) == 2


def test_retry_adapter_recognizes_connection_errors_as_transient():
    source = ScriptedReportSource([ConnectionError("connection refused"),
        ([{"id": "recovered"}], {"status": "success", "records_received": 1, "errors": []})])
    waits = []
    events, report = RetryingEventSource(source, retry_count=1, backoff_seconds=0,
        sleep=waits.append).fetch_with_report()
    assert events == [{"id": "recovered"}] and source.calls == 2
    assert report["attempt_count"] == 2 and waits == [0]


def test_retry_adapter_does_not_retry_permanent_configuration_failure():
    waits = []
    for message in ("ValueError: invalid feed URL", "network error: forbidden by its access permissions"):
        source = ScriptedReportSource([([], {"status": "error", "error": message,
            "errors": [message]})])
        _, report = RetryingEventSource(source, retry_count=5, sleep=waits.append).fetch_with_report()
        assert source.calls == 1 and report["attempt_count"] == 1
    assert waits == []


def test_continuous_service_applies_retry_wrapper_to_configured_sources(tmp_path):
    source = ScriptedReportSource([
        ([], {"status": "error", "error": "request timed out", "errors": ["request timed out"]}),
        ([event()], {"status": "success", "records_received": 1, "valid_records": 1, "errors": []})])
    source.name = "test_feed"
    session = session_for(DatabaseRepository(tmp_path / "retry-cycle.sqlite3"), MultiSource([source]))
    waits = []
    service = ContinuousMonitoringService(session=session,
        service_config=MonitoringServiceConfig(interval_seconds=60, retry_count=1,
            retry_backoff_seconds=0), sleep=waits.append)
    result = service.run_cycle()
    report = result["source_reports"]["test_feed"]
    assert source.calls == 2 and report["attempt_count"] == 2
    assert result["status"] == "success" and len(result["events"]) == 1


def test_database_deduplication_and_material_update_retain_revision_history(tmp_path):
    repo = DatabaseRepository(tmp_path / "revisions.sqlite3")
    original = event()
    source = EventSource([original], {"test_feed": {"status": "success",
        "records_received": 1, "valid_records": 1, "errors": []}})
    session = session_for(repo, MultiSource([source]))
    first = session.run_cycle()
    duplicate = session.run_cycle()
    assert first["events"][0]["database_event_state"] == "NEW"
    assert duplicate["events"][0]["database_event_state"] == "DUPLICATE"
    source.events = [event(text=original["text"] + " Updated official report.",
                           metadata={"alert": "red"})]
    updated = session.run_cycle()
    assert updated["events"][0]["database_event_state"] == "UPDATED"
    persisted = repo.get_event(first["events"][0]["event_id"])
    assert persisted["version"] == 2 and len(persisted["revisions"]) == 2


def test_phase3_failure_is_visible_in_run_while_source_evidence_survives(tmp_path):
    class Phase3FailurePipeline(StubPipeline):
        def process(self, normalized):
            record = super().process(normalized)
            record["processing_errors"] = [{"phase": "phase3", "error_type": "ModelUnavailable",
                                            "message": "local model checkpoint unavailable"}]
            record["phase_status"]["phase3"] = "failed"
            record["phases_failed"] = ["phase3"]
            return record
    repo = DatabaseRepository(tmp_path / "phase3-failure.sqlite3")
    cfg = source_config()
    runner = MonitoringRunner(config=MonitoringConfig(run_phase3=True), pipeline=Phase3FailurePipeline())
    session = LiveMonitoringSession(cfg, event_source=MultiSource([EventSource([event()])]),
        runner=runner, repository=repo)
    result = session.run_cycle()
    assert result["status"] == "partial_failure"
    assert any(error.get("phase") == "phase3" for error in result["errors"])
    persisted = repo.list_events()[0]
    assert persisted["type_evidence"] or persisted["provenance"]["source"] == "test_feed"


def test_retry_logs_and_run_reports_redact_secret_like_values(caplog):
    source = ScriptedReportSource([RuntimeError("api_key=DO_NOT_LOG")])
    with caplog.at_level(logging.WARNING):
        _, report = RetryingEventSource(source, retry_count=0).fetch_with_report()
    assert "DO_NOT_LOG" not in caplog.text
    assert "DO_NOT_LOG" not in str(report)


def test_service_serializes_cycles_when_called_concurrently():
    class SlowSession:
        source = EventSource()
        def __init__(self):
            self.guard = Lock()
            self.active = self.max_active = self.calls = 0
        def run_cycle(self):
            with self.guard:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.calls += 1
            sleep(.05)
            with self.guard:
                self.active -= 1
            return {"monitoring_run_id": str(self.calls), "status": "success", "summary": {}}
    session = SlowSession()
    service = ContinuousMonitoringService(session=session,
        service_config=MonitoringServiceConfig(interval_seconds=60))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.run_cycle) for _ in range(2)]
        [future.result(timeout=3) for future in futures]
    assert session.calls == 2 and session.max_active == 1


def test_continuous_service_waits_after_completed_cycles_and_honors_stop():
    class FakeSession:
        source = EventSource()
        def __init__(self): self.calls = 0
        def run_cycle(self):
            self.calls += 1
            return {"monitoring_run_id": str(self.calls), "status": "success", "summary": {}}
    session = FakeSession()
    service = ContinuousMonitoringService(session=session,
        service_config=MonitoringServiceConfig(interval_seconds=77))
    waits = []
    results = service.run(max_cycles=3, wait=waits.append)
    assert len(results) == 3 and session.calls == 3 and waits == [77, 77]
    def stop_after_cycle(seconds):
        waits.append(seconds)
        service.request_stop()
    session.calls = 0
    waits.clear()
    results = service.run(wait=stop_after_cycle)
    assert len(results) == 1 and waits == [77]


def test_no_sources_is_explicitly_failed_and_run_is_persisted(tmp_path):
    repo = DatabaseRepository(tmp_path / "none.sqlite3")
    result = session_for(repo, MultiSource([])).run_cycle()
    assert result["status"] == "failed"
    assert any("no configured source" in item["message"] for item in result["errors"])


def test_unexpected_processing_exception_finalizes_run_and_service_can_continue(tmp_path):
    repo = DatabaseRepository(tmp_path / "fatal-cycle.sqlite3")
    session = session_for(repo)

    def explode(_events):
        raise RuntimeError("unexpected downstream exception")

    session.runner.process_events = explode
    service = ContinuousMonitoringService(session=session,
        service_config=MonitoringServiceConfig(interval_seconds=60))
    result = service.run_cycle()
    persisted = repo.get_monitoring_run(result["monitoring_run_id"])
    assert result["status"] == persisted["status"] == "failed"
    assert persisted["ended_at"]
    assert any(error.get("phase") == "monitoring_cycle" for error in persisted["errors"])


def test_next_service_cycle_recovers_only_old_abandoned_runs(tmp_path, monkeypatch):
    repo = DatabaseRepository(tmp_path / "stale-cycle.sqlite3")
    repo.save_monitoring_run({"run_id": "abandoned", "source": "multi_source",
        "run_mode": "poll", "started_at": "2000-01-01T00:00:00Z", "status": "running"})
    repo.save_monitoring_run({"run_id": "still-active", "source": "multi_source",
        "run_mode": "poll", "started_at": "2099-01-01T00:00:00Z", "status": "running"})
    monkeypatch.setenv("DISASTER_STALE_RUN_SECONDS", "1800")
    service = ContinuousMonitoringService(session=session_for(repo),
        service_config=MonitoringServiceConfig(interval_seconds=60))
    result = service.run_cycle()
    assert repo.get_monitoring_run("abandoned")["status"] == "failed"
    assert repo.get_monitoring_run("abandoned")["processing_results"]["recovered_as_stale"] is True
    assert repo.get_monitoring_run("still-active")["status"] == "running"
    assert result["status"] == "success"


def test_dashboard_api_sees_records_saved_by_service(tmp_path):
    repo = DatabaseRepository(tmp_path / "dash.sqlite3")
    result = session_for(repo).run_cycle()
    response = create_app(repo)
    assert result["persistence_status"] == "ok"
    # The same persisted collection is served by the existing Phase 11 API contract.
    status, events = _app_call(response, "/api/events")
    assert status == 200 and events["total"] == 1
    status, runs = _app_call(response, "/api/monitoring/runs")
    assert status == 200 and runs["items"][0]["run_id"] == result["monitoring_run_id"]


def _app_call(app, path):
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": "",
        "wsgi.input": io.BytesIO(), "SERVER_NAME": "localhost", "SERVER_PORT": "80",
        "wsgi.url_scheme": "http"}
    status = []
    def start_response(value, _headers):
        status.append(int(value.split()[0]))
    body = b"".join(app(environ, start_response))
    import json
    return status[0], json.loads(body)


def test_cli_rejects_interval_below_public_source_minimum(monkeypatch):
    monkeypatch.setenv("USGS_ENABLED", "false")
    monkeypatch.setenv("GDACS_ENABLED", "false")
    monkeypatch.setenv("NEWS_ENABLED", "false")
    with pytest.raises(SystemExit) as exc:
        main(["--once", "--interval", "30"])
    assert exc.value.code == 2
