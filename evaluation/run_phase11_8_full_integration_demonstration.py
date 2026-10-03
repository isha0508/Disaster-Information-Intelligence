"""Run one real-source Phase 11.6 cycle and verify SQLite, API, and dashboard.

This is an operational demonstration, not a deterministic test. Public providers
may return no data or fail; their actual status is retained in the JSON report.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from threading import Thread
from wsgiref.simple_server import make_server

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.app import create_app
from dashboard.api_client import DashboardAPIClient
from dashboard.live_data import load_live_snapshot
from database import DatabaseRepository
from monitoring.real_sources import RealSourceConfig
from monitoring.service import ContinuousMonitoringService, MonitoringServiceConfig


def run(output_path=None):
    """Run the configured public feeds through the existing canonical path."""
    destination = Path(output_path or Path(__file__).with_name(
        "phase11_8_full_integration_results.json")).resolve()
    source_config = RealSourceConfig.from_env()
    cycle_start = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with tempfile.TemporaryDirectory(prefix="disaster-phase11-8-") as workdir:
        database_path = Path(workdir) / "integration.sqlite3"
        repository = DatabaseRepository(database_path)
        service = ContinuousMonitoringService(
            source_config=source_config,
            service_config=MonitoringServiceConfig(
                interval_seconds=source_config.polling_interval_seconds,
                retry_count=0,
                retry_backoff_seconds=0,
            ),
            repository=repository,
            run_phase3=True,
        )
        cycle = service.run(once=True)[0]
        run_id = cycle.get("monitoring_run_id")
        persisted_run = repository.get_monitoring_run(run_id) if run_id else None
        persisted_events = repository.list_events(limit=1000)
        persisted_alerts, alert_total = repository.query_alerts(limit=1000)

        api_report = {"status": "not_checked", "checks": {}, "errors": []}
        dashboard_report = {"status": "not_checked", "data_status": None,
                            "event_total": None, "events_visible": None, "errors": []}
        server = make_server("127.0.0.1", 0, create_app(repository))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = DashboardAPIClient(f"http://127.0.0.1:{server.server_port}")
            checks = {
                "health": client.get_json("/health"),
                "healthz": client.get_json("/healthz"),
                "events": client.get_json("/api/events", {"limit": 1000, "offset": 0}),
                "alerts": client.get_json("/api/alerts", {"limit": 1000, "offset": 0}),
                "alert_history": [],
                "monitoring_runs": client.get_json("/api/monitoring/runs", {"limit": 20, "offset": 0}),
                "monitoring_run": client.get_json(f"/api/monitoring/runs/{run_id}") if run_id else None,
                "intelligence_summary": client.get_json("/api/intelligence/summary"),
            }
            event_detail_results = []
            for event in persisted_events:
                event_id = event.get("event_id")
                if event_id:
                    event_detail_results.append(client.get_json(f"/api/events/{event_id}").get("item") is not None)
            alert_detail_results = []
            for alert in checks["alerts"].get("items", []):
                alert_id = alert.get("alert_id")
                if alert_id:
                    alert_detail_results.append(client.get_json(f"/api/alerts/{alert_id}").get("item") is not None)
                    checks["alert_history"].append({
                        "alert_id": alert_id,
                        "response": client.get_json(f"/api/alerts/{alert_id}/history"),
                    })
            api_report["checks"] = {
                "health": checks["health"].get("status") == "ok",
                "healthz": checks["healthz"].get("status") == "ok",
                "events": checks["events"].get("total") == len(persisted_events),
                "event_detail": all(event_detail_results) if event_detail_results else None,
                "alerts": checks["alerts"].get("total") == alert_total,
                "alert_detail": all(alert_detail_results) if alert_detail_results else None,
                "alert_history": all(bool(item.get("response")) for item in checks["alert_history"])
                    if checks["alert_history"] else None,
                "monitoring_runs": checks["monitoring_runs"].get("total", 0) >= 1,
                "monitoring_run": bool(checks["monitoring_run"].get("item")) if checks["monitoring_run"] else False,
                "intelligence_summary": checks["intelligence_summary"].get("total_events") == len(persisted_events),
            }
            required_checks = [passed for passed in api_report["checks"].values() if passed is not None]
            api_report["status"] = "verified" if all(required_checks) else "partial"
            snapshot = load_live_snapshot(client)
            dashboard_report.update({
                "status": "verified" if snapshot.get("connection_status") == "online" else "failed",
                "data_status": snapshot.get("data_status"),
                "event_total": snapshot.get("event_total"),
                "events_visible": len(snapshot.get("events", [])),
                "source_health": snapshot.get("source_status", []),
                "errors": snapshot.get("errors", {}),
            })
        except Exception as exc:
            api_report["status"] = "failed"
            api_report["errors"].append(f"{type(exc).__name__}: {str(exc)[:400]}")
            dashboard_report["status"] = "failed"
            dashboard_report["errors"].append("Dashboard/API verification failed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        phase3 = Counter()
        downstream = {f"phase{number}": Counter() for number in range(4, 8)}
        for event in cycle.get("events", []):
            statuses = event.get("phase_status") or (event.get("processing_results") or {}).get("phase_status", {})
            if isinstance(statuses, dict):
                phase3[statuses.get("phase3", "not_reported")] += 1
                for number in range(4, 8):
                    downstream[f"phase{number}"][statuses.get(f"phase{number}", "not_reported")] += 1
        state_counts = Counter(str(event.get("event_state", "UNKNOWN")).upper()
                               for event in cycle.get("events", []))
        reports = cycle.get("source_reports", {})
        source_results = {name: dict(report) for name, report in reports.items()}
        for name, enabled in (("usgs", source_config.usgs_enabled),
                              ("gdacs", source_config.gdacs_enabled),
                              ("news_rss", source_config.news_enabled and bool(source_config.rss_feeds)),
                              ("mastodon", source_config.mastodon_enabled)):
            source_results.setdefault(name, {
                "source": name,
                "status": "not_configured" if not enabled else "not_reported",
                "records_received": 0,
                "valid_records": 0,
                "errors": [],
            })
        any_attempted = any(report.get("status") != "not_configured" for report in reports.values())
        successful_external_records = any(
            report.get("status") in {"success", "partial", "partial_failure"} and
            report.get("valid_records", 0) > 0 for report in reports.values())
        artifact = {
            "demonstration": "Phase 11.8 full-system integration",
            "run_mode": "one_cycle",
            "data_designation": ("LIVE EXTERNAL DATA" if successful_external_records else
                "LIVE SOURCE ATTEMPT — NO RECORDS VERIFIED" if any_attempted else "NOT CONFIGURED"),
            "retrieval_timestamp": cycle.get("retrieval_timestamp") or cycle_start,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_configuration": {
                "usgs_enabled": source_config.usgs_enabled,
                "gdacs_enabled": source_config.gdacs_enabled,
                "rss_enabled": source_config.news_enabled,
                "rss_feed_count": len(source_config.rss_feeds),
                "mastodon_enabled": source_config.mastodon_enabled,
                "max_events_per_source": source_config.max_events_per_source,
            },
            "source_results": source_results,
            "monitoring_run_id": run_id,
            "monitoring_run_status": (persisted_run or {}).get("status"),
            "event_counts": {
                "received": cycle.get("records_received", 0),
                "selected": cycle.get("selected_records", 0),
                "persisted": len(persisted_events),
                "NEW": state_counts.get("NEW", 0),
                "DUPLICATE": state_counts.get("DUPLICATE", 0),
                "UPDATED": state_counts.get("UPDATED", 0),
                "by_source": dict(Counter(item.get("source", "unknown") for item in persisted_events)),
            },
            "intelligence_processing": {
                "phase3": dict(phase3),
                "phase3_success": sum(count for status, count in phase3.items()
                                       if status in {"completed", "completed_with_unavailable_models"}),
                "phase3_failure": phase3.get("failed", 0),
                **{phase: dict(counts) for phase, counts in downstream.items()},
            },
            "alerts": {"evaluated": cycle.get("summary", {}).get("alerts_evaluated", 0),
                       "generated": cycle.get("summary", {}).get("alerts_generated", 0),
                       "persisted": alert_total},
            "database": {"persistence_status": cycle.get("persistence_status"),
                         "monitoring_run_finalized": bool((persisted_run or {}).get("ended_at")),
                         "event_count": len(persisted_events)},
            "api": api_report,
            "dashboard": dashboard_report,
            "errors": cycle.get("errors", []) + cycle.get("persistence_errors", []),
            "latency_seconds": cycle.get("duration_seconds"),
            "evaluation_notice": (
                "This demonstration used the configured public adapters and records their actual outcomes. "
                "A successful HTTP/feed retrieval does not validate geographic accuracy, source completeness, "
                "or operational fitness. No synthetic records are substituted for failed feeds."
            ),
        }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(artifact, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                           encoding="utf-8")
    print(f"Phase 11.8 integration status: {artifact['monitoring_run_status']}")
    print(f"Data designation: {artifact['data_designation']}")
    print(f"Run ID: {artifact['monitoring_run_id']}")
    print(f"Received / persisted: {artifact['event_counts']['received']} / {artifact['event_counts']['persisted']}")
    print(f"API: {api_report['status']} | dashboard: {dashboard_report['status']} ({dashboard_report['data_status']})")
    print(f"Artifact: {destination}")
    return artifact


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="JSON artifact path (default evaluation/phase11_8_full_integration_results.json)")
    args = parser.parse_args(argv)
    artifact = run(args.output)
    return 0 if (artifact["api"]["status"] == "verified" and
                 artifact["database"]["persistence_status"] == "ok" and
                 artifact["monitoring_run_status"] in {"success", "partial_failure"}) else 1


if __name__ == "__main__":
    raise SystemExit(main())
