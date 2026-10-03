"""Exercise the Phase 11.5 dashboard through a temporary API and SQLite DB.

All inserted records are synthetic and marked as demonstration data. Nothing
is written to the project's configured operational database.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
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
from dashboard.app import make_live_handler
from dashboard.config import DashboardConfig
from dashboard.live_data import load_live_snapshot
from database import DatabaseRepository


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


def _populate(repo):
    now = datetime.now(timezone.utc).isoformat()
    records = [
        ("demo-flood-1", "Flood response at River District", "River District", 19.1, 72.8, True),
        ("demo-flood-2", "Rescue operation near River District", "River District", 19.12, 72.82, True),
        ("demo-unresolved", "Emergency reported from an unnamed village", "Unknown village", None, None, False),
    ]
    for source_id, title, location, latitude, longitude, resolved in records:
        phase5 = {"incident_id": f"incident-{source_id}", "disaster_type": "flood",
            "canonical_disaster_type": "flood", "severity_score": 72 if resolved else 28,
            "severity_level": "HIGH" if resolved else "LOW", "urgency_score": 68 if resolved else 20,
            "urgency_level": "HIGH" if resolved else "LOW", "priority_score": 70 if resolved else 22,
            "priority_level": "HIGH" if resolved else "LOW", "confidence_score": 80,
            "confidence_level": "HIGH", "decision_flags": ["RESCUE_REQUIRED"] if "rescue" in title.casefold() else []}
        phase6 = {"incidents": [{"incident_id": phase5["incident_id"],
            "location_text": location, "normalized_location": location,
            "latitude": latitude, "longitude": longitude,
            "geocoding_status": "success" if resolved else "failed",
            "geocoding_source": "synthetic_demo" if resolved else None,
            "coordinate_source": "synthetic_demo" if resolved else None,
            "spatial_cluster_id": "demo-cluster-1" if resolved else None}]}
        alert = ({"alert_id": f"alert-{source_id}", "event_id": f"evt_{source_id}",
            "incident_id": phase5["incident_id"], "alert_level": "HIGH", "alert_type": "OPERATIONAL",
            "status": "ACTIVE", "created_at": now, "trigger_reasons": ["elevated priority"]}
            if source_id == "demo-flood-2" else None)
        repo.upsert_event({"source": "synthetic_demo", "source_event_id": source_id,
            "title": title, "text": title + ". Synthetic dashboard integration example.",
            "original_text": title + ". Synthetic dashboard integration example.",
            "location_text": location, "latitude": latitude, "longitude": longitude,
            "event_timestamp": now, "observed_at": now, "retrieved_at": now,
            "disaster_type": "flood", "canonical_disaster_type": "flood",
            "metadata": {"demonstration": True, "synthetic_data": True},
            "intelligence": {"phase5": phase5, "phase6": phase6,
                "phase4": {"disaster_type": "flood", "location": [location]},
                "phase7": {"summary": "Synthetic demonstration record."}},
            "incident_intelligence": phase5,
            "type_resolution": {"canonical_disaster_type": "flood", "type_agreement_status": "AGREEMENT"},
            "location_evidence": {"source_location_text": location,
                "geocoded_latitude": latitude, "geocoded_longitude": longitude,
                "geocoding_status": "success" if resolved else "failed",
                "coordinate_source": "synthetic_demo" if resolved else None},
            "alert_decision": ({"should_alert": True, "alert_level": "HIGH", "alert_type": "OPERATIONAL",
                "event_id": f"evt_{source_id}", "incident_id": phase5["incident_id"],
                "priority_score": phase5["priority_score"], "severity_score": phase5["severity_score"],
                "trigger_reasons": ["elevated priority"]} if alert else None),
            "alert_record": alert})
    repo.save_monitoring_run({"run_id": "phase11-5-demo-run", "source": "synthetic_demo",
        "status": "success", "run_mode": "demonstration", "started_at": now, "ended_at": now,
        "records_received": 3, "valid_records": 3, "new_records": 3,
        "processing_results": {"source_reports": {"synthetic_demo": {
            "status": "success", "records_received": 3, "selected_for_processing": 3,
            "new_records": 3, "duplicate_records": 0, "updated_records": 0,
            "retrieval_timestamp": now}}}})


def main():
    results_path = Path(__file__).with_name("phase11_5_dashboard_results.json")
    with tempfile.TemporaryDirectory(prefix="phase11_5_dashboard_") as directory:
        repo = DatabaseRepository(Path(directory) / "demo.sqlite3")
        _populate(repo)
        with _serve_api(create_app(repo)) as api_url:
            config = DashboardConfig(api_base_url=api_url)
            with _serve_dashboard(make_live_handler(config)) as dashboard_url:
                client = DashboardAPIClient(api_url)
                html = urlopen(dashboard_url + "/", timeout=3).read().decode("utf-8")
                normal = json.loads(urlopen(dashboard_url + "/dashboard-data", timeout=3).read())
                filtered = json.loads(urlopen(dashboard_url +
                    "/dashboard-data?priority=HIGH&location=River", timeout=3).read())
                detail_id = normal["events"][0]["event_id"]
                detail_status = json.loads(urlopen(dashboard_url + "/dashboard-event/" + detail_id, timeout=3).read())
                offline = load_live_snapshot(DashboardAPIClient("http://127.0.0.1:1", .2))
        empty_repo = DatabaseRepository(Path(directory) / "empty.sqlite3")
        with _serve_api(create_app(empty_repo)) as empty_url:
            empty = load_live_snapshot(DashboardAPIClient(empty_url))
        result = {"artifact_kind": "synthetic_test_demonstration", "not_live_data": True,
            "architecture": "temporary SQLite database -> Phase 11 REST API -> Phase 11.5 dashboard adapter",
            "dashboard_page_served": "DISASTER INTELLIGENCE CENTER" in html,
            "api_connected": normal["connection_status"] == "online",
            "data_status": normal["data_status"], "persisted_event_count": normal["event_total"],
            "active_alert_count": normal["summary"].get("active_alerts"),
            "monitoring_status": normal["monitoring_status"],
            "source_status": [row["status"] for row in normal["source_status"]],
            "summary_total_events": normal["summary"].get("total_events"),
            "filter_result_count": filtered["event_total"],
            "detail_api_success": "item" in detail_status,
            "verified_map_points": len(normal["map_points"]),
            "unresolved_locations_not_plotted": all(not item.get("latitude") for item in normal["unresolved_locations"]),
            "offline_state": offline["data_status"], "empty_database_state": empty["data_status"],
            "real_geographic_accuracy_evaluated": False}
    results_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Wrote {results_path}")


if __name__ == "__main__":
    main()
