"""Temporary synthetic-data demonstration of the Phase 11.1–11.3 flow."""

import io
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.app import create_app
from database import DatabaseRepository
from monitoring.config import MonitoringConfig
from monitoring.live import LiveMonitoringSession
from monitoring.pipeline import IntelligencePipeline
from monitoring.real_sources import MultiSource, RealSourceConfig
from monitoring.runner import MonitoringRunner


class ControlledSource:
    name = "synthetic_test_feed"

    def __init__(self):
        self.record = {"source": self.name, "source_event_id": "phase11-synthetic-001",
            "title": "Synthetic flood rescue report", "text":
            "Severe flooding trapped 12 people. Four people were injured and urgent rescue teams are needed.",
            "location_text": "Example River District", "event_timestamp": "2026-09-25T08:00:00Z",
            "observed_at": "2026-09-25T08:05:00Z", "retrieved_at": "2026-09-25T08:06:00Z",
            "metadata": {"demonstration": True}}

    def fetch(self):
        return [dict(self.record)]


class ControlledFailure:
    name = "synthetic_failing_feed"

    def fetch(self):
        raise RuntimeError("demonstration source failure")


def _get(app, path):
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": "",
        "wsgi.input": io.BytesIO(), "SERVER_NAME": "localhost", "SERVER_PORT": "80",
        "wsgi.url_scheme": "http"}
    captured = {}
    def start_response(status, headers):
        captured["status"] = int(status.split()[0])
    body = b"".join(app(environ, start_response))
    captured["body"] = json.loads(body)
    return captured


def main():
    result = {"artifact_type": "PHASE 11 INTEGRATION DEMONSTRATION",
        "data_classification": "SYNTHETIC TEST DATA; NOT LIVE DATA",
        "database_classification": "TEMPORARY DEMONSTRATION DATABASE",
        "live_data_used": False, "checks": {}, "errors": []}
    with tempfile.TemporaryDirectory(prefix="phase11-integration-") as folder:
        db_path = Path(folder) / "demo.sqlite3"
        repository = DatabaseRepository(db_path)
        result["checks"]["database_initialized"] = repository.health_check()
        controlled, failed = ControlledSource(), ControlledFailure()
        source_config = RealSourceConfig(usgs_enabled=False, gdacs_enabled=False,
            news_enabled=False, max_events_per_source=5)
        runner = MonitoringRunner(config=MonitoringConfig(run_phase3=False),
            pipeline=IntelligencePipeline(run_phase3=False))
        session = LiveMonitoringSession(source_config,
            event_source=MultiSource([controlled, failed]), runner=runner,
            repository=repository)

        first = session.run_cycle()
        event_id = first["events"][0].get("event_id")
        stored = repository.get_event(event_id) if event_id else None
        result["inserted_events"] = len(repository.list_events())
        result["monitoring_runs"] = len(repository.list_monitoring_runs())
        result["alerts"] = len(repository.query_alerts()[0])
        result["first_state"] = first["events"][0].get("database_event_state") if first["events"] else None
        result["partial_source_failure"] = first["source_reports"].get("synthetic_failing_feed", {}).get("status")
        result["first_run_id"] = first.get("monitoring_run_id")
        result["checks"].update({
            "initial_event_persisted": bool(stored),
            "source_failure_retained": result["partial_source_failure"] == "error",
            "monitoring_run_persisted": repository.get_monitoring_run(first["monitoring_run_id"]) is not None,
            "phase_results_persisted": bool(stored and stored.get("intelligence", {}).get("phase4")),
            "phase5_persisted": bool(stored and stored.get("incident_intelligence")),
            "alert_decision_persisted": bool(stored and stored.get("alerts")),
        })

        app = create_app(repository)
        endpoint_paths = ("/health", "/api/events", "/api/alerts", "/api/monitoring/runs",
                          "/api/intelligence/summary")
        api_results = {path: _get(app, path) for path in endpoint_paths}
        result["api_endpoint_checks"] = {path: response["status"] == 200
            for path, response in api_results.items()}
        result["api_event_total"] = api_results["/api/events"]["body"].get("total")
        result["api_alert_total"] = api_results["/api/alerts"]["body"].get("total")
        result["api_run_total"] = api_results["/api/monitoring/runs"]["body"].get("total")
        result["checks"]["api_matches_persisted_data"] = (
            result["api_event_total"] == len(repository.list_events()) and
            result["api_run_total"] == len(repository.list_monitoring_runs()))

        duplicate = session.run_cycle()
        result["duplicate_state"] = duplicate["events"][0].get("database_event_state") if duplicate["events"] else None
        result["revision_count_after_duplicate"] = len(repository.get_event(event_id)["revisions"])
        result["checks"]["duplicate_idempotent"] = (
            result["duplicate_state"] == "DUPLICATE" and len(repository.list_events()) == 1 and
            result["revision_count_after_duplicate"] == 1)

        controlled.record["text"] += " Updated official report: six people injured."
        controlled.record["title"] = "Updated synthetic flood rescue report"
        controlled.record["updated_at"] = "2026-09-25T08:10:00Z"
        updated = session.run_cycle()
        final = repository.get_event(event_id)
        result["update_state"] = updated["events"][0].get("database_event_state") if updated["events"] else None
        result["updated_version"] = final.get("version") if final else None
        result["revision_count_after_update"] = len(final.get("revisions", [])) if final else 0
        result["checks"]["material_update_revisioned"] = (
            result["update_state"] == "UPDATED" and result["updated_version"] == 2 and
            result["revision_count_after_update"] == 2)
        result["run_count_after_all_cycles"] = len(repository.list_monitoring_runs())
        result["validation_status"] = "passed" if all(result["checks"].values()) else "failed"

    output = Path(__file__).with_name("phase11_integration_results.json")
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("PHASE 11.1-11.3 INTEGRATION DEMONSTRATION")
    print("Data: SYNTHETIC TEST DATA | DB: TEMPORARY DEMONSTRATION DATABASE | Live: no")
    print(f"Event: {result.get('first_state')} -> {result.get('duplicate_state')} -> {result.get('update_state')}; "
          f"events={result['inserted_events']} alerts={result['alerts']} runs={result['run_count_after_all_cycles']}")
    print(f"Checks: {sum(result['checks'].values())}/{len(result['checks'])} | {result['validation_status']}")
    print(f"Artifact: {output.resolve()}")
    if result["validation_status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
