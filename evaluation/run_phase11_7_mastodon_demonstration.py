"""One real Mastodon ingestion cycle through the existing application path."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from threading import Thread
from urllib.request import urlopen
from wsgiref.simple_server import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.app import create_app
from dashboard.api_client import DashboardAPIClient
from dashboard.live_data import load_live_snapshot
from database import DatabaseRepository
from monitoring.live import LiveMonitoringSession
from monitoring.real_sources import RealSourceConfig

OUTPUT = Path(__file__).with_name("phase11_7_mastodon_demonstration_results.json")


def run_demonstration():
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    artifact = {"phase": "11.7", "run_mode": "live", "source": "mastodon",
        "synthetic_fallback": False, "status": "failed", "started_at": started,
        "endpoint": None, "hashtags": [], "records_received": 0, "records_retained": 0,
        "duplicates_removed": 0, "records_processed": 0, "events_persisted": 0,
        "events_exposed_through_api": 0, "phases_reached": {}, "sample_events": [],
        "dashboard_source": None, "errors": [], "source_report": {}}
    try:
        base = RealSourceConfig.from_env()
        config = RealSourceConfig(usgs_enabled=False, gdacs_enabled=False, news_enabled=False,
            mastodon_enabled=True, mastodon_base_url=base.mastodon_base_url,
            mastodon_hashtags=base.mastodon_hashtags, mastodon_limit=base.mastodon_limit,
            max_events_per_source=min(10, base.max_events_per_source),
            timeout_seconds=base.timeout_seconds, user_agent=base.user_agent,
            polling_interval_seconds=base.polling_interval_seconds)
        artifact["endpoint"] = config.mastodon_base_url + "/api/v1/timelines/tag/{hashtag}"
        artifact["hashtags"] = list(config.mastodon_hashtags)
        with tempfile.TemporaryDirectory(prefix="phase11_7_mastodon_") as directory:
            repo = DatabaseRepository(Path(directory) / "phase11_7.sqlite3")
            session = LiveMonitoringSession(config, repository=repo, run_phase3=True)
            server = make_server("127.0.0.1", 0, create_app(repo))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = session.run_cycle()
                report = result.get("source_reports", {}).get("mastodon", {})
                artifact["source_report"] = report
                artifact["records_received"] = report.get("records_received", 0)
                artifact["records_retained"] = report.get("valid_records", 0)
                artifact["duplicates_removed"] = report.get("duplicates_removed", 0)
                artifact["hashtag_results"] = report.get("hashtag_results", [])
                artifact["records_processed"] = report.get("selected_for_processing", 0)
                artifact["errors"] = list(report.get("errors", [])) + list(result.get("errors", []))
                events = result.get("events", [])
                phases = {}
                for event in events:
                    statuses = event.get("phase_status") or {}
                    for phase, state in statuses.items():
                        phases.setdefault(phase, {}).setdefault(state, 0)
                        phases[phase][state] += 1
                artifact["phases_reached"] = phases
                artifact["sample_events"] = [{"event_id": event.get("event_id"),
                    "source_event_id": event.get("source_event_id"), "source": event.get("source"),
                    "source_type": (event.get("event") or {}).get("provenance", {}).get("source_type"),
                    "original_text": (event.get("event") or {}).get("original_text"),
                    "source_metadata": (event.get("event") or {}).get("metadata"),
                    "phase_payloads": event.get("intelligence"), "phase_status": event.get("phase_status"),
                    "phases_completed": event.get("phases_completed", []),
                    "processing_errors": event.get("processing_errors", [])} for event in events[:3]]
                for sample in artifact["sample_events"]:
                    persisted = repo.get_event(sample["event_id"])
                    stored = (persisted or {}).get("intelligence", {})
                    sample["sqlite_persistence"] = {"found": persisted is not None,
                        "source": (persisted or {}).get("source"),
                        "source_event_id": (persisted or {}).get("source_event_id"),
                        "original_text_preserved": bool((persisted or {}).get("raw_event", {}).get("text")),
                        "phase_payloads": {f"phase{number}": f"phase{number}" in stored
                                           for number in range(3, 8)}}
                endpoint = f"http://127.0.0.1:{server.server_port}"
                with urlopen(endpoint + "/health", timeout=5) as response:
                    artifact["health_status"] = response.status
                with urlopen(endpoint + "/api/events?source=mastodon&limit=100", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                artifact["events_exposed_through_api"] = payload.get("total", 0)
                checks = {"health": artifact["health_status"], "events": 200}
                sample_id = payload.get("items", [{}])[0].get("event_id") if payload.get("items") else None
                if sample_id:
                    with urlopen(endpoint + f"/api/events/{sample_id}", timeout=5) as response:
                        detail = json.loads(response.read().decode("utf-8"))
                        checks["event_detail"] = response.status
                        api_event = detail.get("item") or {}
                        artifact["api_event_payload"] = {"source": api_event.get("source"),
                            "source_event_id": api_event.get("source_event_id"),
                            "has_mastodon_metadata": bool(api_event.get("metadata", {}).get("mastodon_id")),
                            "phase_payloads": {f"phase{number}": f"phase{number}" in api_event.get("intelligence", {})
                                               for number in range(3, 8)}}
                with urlopen(endpoint + "/api/monitoring/runs?limit=20", timeout=5) as response:
                    run_payload = json.loads(response.read().decode("utf-8"))
                    checks["monitoring_runs"] = response.status
                run_id = result.get("monitoring_run_id")
                if run_id:
                    with urlopen(endpoint + f"/api/monitoring/runs/{run_id}", timeout=5) as response:
                        checks["monitoring_run_detail"] = response.status
                with urlopen(endpoint + "/api/intelligence/summary", timeout=5) as response:
                    checks["intelligence_summary"] = response.status
                artifact["api_checks"] = checks
                artifact["events_persisted"] = repo.query_events(source="mastodon", limit=100, offset=0)[1]
                snapshot = load_live_snapshot(DashboardAPIClient(endpoint))
                artifact["dashboard_source"] = next((row for row in snapshot.get("source_status", [])
                    if row.get("name") == "Mastodon"), None)
                artifact["dashboard_event_count"] = len([row for row in snapshot.get("events", [])
                    if row.get("source") == "mastodon"])
                artifact["monitoring_run_status"] = result.get("status")
                artifact["status"] = "success" if report.get("status") in {"success", "partial"} and artifact["records_received"] > 0 and artifact["events_persisted"] > 0 else "failed"
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
    except Exception as exc:
        artifact["errors"].append(f"{type(exc).__name__}: {str(exc)[:500]}")
    artifact["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    OUTPUT.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: artifact.get(key) for key in ("status", "endpoint", "hashtags",
        "records_received", "records_retained", "duplicates_removed", "records_processed",
        "events_persisted", "events_exposed_through_api", "phases_reached", "errors")}, indent=2))
    print(f"Artifact: {OUTPUT}")
    return 0 if artifact["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(run_demonstration())
