"""Run one configured-source Phase 11.6 cycle and verify its API/dashboard path.

By default persistence uses a temporary SQLite file and is deleted at exit.
Source access is live only when configured public sources are enabled; this
script never substitutes synthetic records.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import argparse
import json
import logging
from pathlib import Path
import sys
import tempfile
from threading import Thread
from urllib.request import urlopen
from wsgiref.simple_server import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.app import create_app
from dashboard.app import make_live_handler
from dashboard.config import DashboardConfig
from database import DatabaseRepository
from monitoring.real_sources import RealSourceConfig, configured_sources
from monitoring.service import ContinuousMonitoringService, MonitoringServiceConfig


@contextmanager
def _serve_api(app):
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
def _serve_dashboard(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _public_report(name, report):
    return {key: report.get(key) for key in (
        "status", "records_received", "valid_records", "invalid_records",
        "selected_for_processing", "new_records", "duplicate_records", "updated_records",
        "attempt_count", "retry_attempts", "errors", "retrieval_timestamp", "latency_seconds")
        if key in report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", default=True,
                        help="run one cycle (the demonstration never loops indefinitely)")
    parser.add_argument("--database", default=None,
                        help="optional persistent database path; default is a temporary database")
    parser.add_argument("--log-level", default="INFO",
                        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    source_config = RealSourceConfig.from_env()
    service_config = MonitoringServiceConfig.from_env(source_config)
    configured = configured_sources(source_config)
    output = Path(__file__).with_name("phase11_6_monitoring_results.json")

    temporary = tempfile.TemporaryDirectory(prefix="phase11_6_monitoring_") if not args.database else None
    try:
        database_path = Path(args.database) if args.database else Path(temporary.name) / "monitoring.sqlite3"
        repository = DatabaseRepository(database_path)
        service = ContinuousMonitoringService(source_config=source_config,
            service_config=service_config, repository=repository, run_phase3=True)
        result = service.run(once=True)[0]
        with _serve_api(create_app(repository)) as api_url:
            config = DashboardConfig(api_base_url=api_url)
            with _serve_dashboard(make_live_handler(config)) as dashboard_url:
                html = urlopen(dashboard_url + "/", timeout=5).read().decode("utf-8")
                dashboard_data = json.loads(urlopen(dashboard_url + "/dashboard-data", timeout=30).read())

        source_reports = {name: _public_report(name, report)
                          for name, report in result.get("source_reports", {}).items()}
        attempted = any(report.get("status") != "not_configured"
                        for report in result.get("source_reports", {}).values())
        successful = any(report.get("status") in {"success", "empty", "partial"}
                         and report.get("attempt_count", 1) >= 1
                         for report in source_reports.values())
        artifact = {
            "artifact_kind": "phase11_6_continuous_monitoring_demonstration",
            "synthetic_fallback": False,
            "synthetic_fallback_explanation": "No synthetic events are inserted; only configured sources are queried.",
            "run_mode": "live" if attempted else "not_configured",
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "monitoring_configuration": {
                "sources_configured": [getattr(source, "name", source.__class__.__name__) for source in configured],
                "poll_interval_seconds": service_config.interval_seconds,
                "max_events_per_source": source_config.max_events_per_source,
                "request_timeout_seconds": source_config.timeout_seconds,
                "retry_count": service_config.retry_count,
                "retry_backoff_seconds": service_config.retry_backoff_seconds,
                "phase3_enabled": True,
                "database_scope": "explicit_path" if args.database else "temporary_isolated_database"},
            "run_id": result.get("monitoring_run_id"),
            "started_at": result.get("started_at"), "completed_at": result.get("completed_at"),
            "duration_seconds": result.get("duration_seconds"), "run_status": result.get("status"),
            "sources_attempted": sum(report.get("status") != "not_configured"
                                      for report in result.get("source_reports", {}).values()),
            "source_statuses": source_reports,
            "records_received": result.get("records_received", 0),
            "valid_records": result.get("valid_records", 0),
            "selected_processed_records": result.get("selected_records", 0),
            "new_records": result.get("summary", {}).get("database_persistence_states", {}).get("NEW", 0),
            "duplicate_records": result.get("summary", {}).get("database_persistence_states", {}).get("DUPLICATE", 0),
            "updated_records": result.get("summary", {}).get("database_persistence_states", {}).get("UPDATED", 0),
            "processing_failures": result.get("summary", {}).get("processing_failures", 0),
            "persistence_status": result.get("persistence_status"),
            "persistence_errors": result.get("persistence_errors", []),
            "alerts_evaluated": result.get("summary", {}).get("alerts_evaluated", 0),
            "alerts_generated": result.get("summary", {}).get("alerts_generated", 0),
            "errors": result.get("errors", []),
            "warnings": [],
            "live_source_retrieval_succeeded": successful,
            "dashboard_page_served": "DISASTER INTELLIGENCE CENTER" in html,
            "dashboard_api_connection_status": dashboard_data.get("connection_status"),
            "dashboard_data_status": dashboard_data.get("data_status"),
            "dashboard_event_total": dashboard_data.get("event_total"),
            "dashboard_visible_persisted_events": len(dashboard_data.get("events", [])),
            "dashboard_map_points": len(dashboard_data.get("map_points", [])),
            "dashboard_reads_same_database_via_api": dashboard_data.get("connection_status") == "online"}
        output.write_text(json.dumps(artifact, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                          encoding="utf-8")
        print(json.dumps(artifact, indent=2, ensure_ascii=False))
        print(f"Wrote {output}")
        return 0 if result.get("persistence_status") == "ok" else 1
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
