"""Bounded temporary SQLite demonstration using one synthetic source-aware event."""

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import DatabaseRepository
from monitoring.identity import event_id_for
from monitoring.normalization import normalize_event


OUTPUT = ROOT / "evaluation" / "phase11_1_database_results.json"


def _make_record(raw):
    event = normalize_event(raw)
    event_id = event_id_for(event)
    type_resolution = {
        "source_disaster_type": {"value": "earthquake", "canonical_value": "earthquake",
                                 "source": "usgs", "source_authority": "USGS",
                                 "provenance": "authoritative_structured_metadata"},
        "phase4_disaster_type": {"value": "flood", "canonical_value": "flood", "provenance": "phase4_nlp"},
        "ml_disaster_type": {"value": "hurricane", "canonical_value": "cyclone",
                             "confidence": .9987, "model": "distilbert_disaster_type",
                             "provenance": "phase3_ml"},
        "canonical_disaster_type": "earthquake", "type_agreement": False,
        "type_agreement_status": "DISAGREEMENT", "model_disagreement": True,
        "resolution_method": "authoritative_source",
        "resolution_rationale": "Authoritative USGS earthquake type used; the Phase 3 prediction is retained as disagreement evidence.",
        "warning": None,
    }
    source_location = {"text": raw["location_text"], "latitude": raw["latitude"],
                       "longitude": raw["longitude"], "source": "usgs",
                       "provenance": "authoritative_structured_metadata",
                       "coordinate_provenance": "source_metadata"}
    phase5 = {"event_id": event_id, "incident_id": "incident-phase11-demo",
              "disaster_type": "earthquake", "canonical_disaster_type": "earthquake",
              "severity_score": 64.0, "severity_level": "HIGH", "urgency_score": 42.0,
              "urgency_level": "MODERATE", "confidence_score": 82.0, "confidence_level": "HIGH",
              "priority_score": 54.1, "priority_level": "HIGH",
              "decision_flags": ["SOURCE_TYPE_DISAGREEMENT"],
              "resource_priorities": {"medical": "high"}, "resource_estimates": {"medical": 12},
              "is_duplicate": False, "duplicate_of": None,
              "processing_metadata": {"phase5_version": "1.0"},
              "type_resolution": type_resolution, "source_location": source_location,
              "nlp_location_entities": []}
    phase6 = {"incident_id": phase5["incident_id"], "location_text": raw["location_text"],
              "normalized_location": raw["location_text"], "latitude": raw["latitude"],
              "longitude": raw["longitude"], "geocoding_status": "success",
              "geocoding_source": "source_metadata", "coordinate_source": "source_metadata"}
    phase9 = {"alert_decision": {"alert_id": "alert-phase11-demo", "should_alert": True,
                                  "alert_level": "HIGH", "alert_type": "OPERATIONAL",
                                  "event_id": event_id, "incident_id": phase5["incident_id"],
                                  "priority_score": 54.1, "severity_score": 64.0,
                                  "urgency_score": 42.0, "confidence_score": 82.0,
                                  "trigger_reasons": [{"rule": "high_priority"}],
                                  "suppressed": False, "is_escalation": False},
              "alert_record": {"alert_id": "alert-phase11-demo", "event_id": event_id,
                               "incident_id": phase5["incident_id"], "alert_level": "HIGH",
                               "alert_type": "OPERATIONAL", "status": "CREATED",
                               "created_at": "2026-09-25T10:04:00Z",
                               "trigger_reasons": [{"rule": "high_priority"}],
                               "notification": {"status": "MOCKED"},
                               "escalation": {"is_escalation": False},
                               "suppression": {"suppressed": False}},
              "alert_status": "CREATED", "alert_evaluated_at": "2026-09-25T10:04:00Z",
              "alert_suppression": {"suppressed": False}}
    return {"event": event, "event_id": event_id, "source": "usgs",
            "processing_status": "PROCESSED", "provenance": event["provenance"],
            "type_resolution": type_resolution, "source_location": source_location,
            "intelligence": {"phase4": {"disaster_type": "flood", "location": [],
                                          "nlp_location_entities": []},
                             "phase5": phase5, "phase6": {"incidents": [phase6]}},
            **phase9}


def main():
    raw = {"source": "usgs", "source_type": "official_public_feed",
           "source_event_id": "phase11-synthetic-usgs-1",
           "title": "M 4.9 - Drake Passage", "text": "M 4.9 - Drake Passage",
           "original_text": "M 4.9 - Drake Passage", "location_text": "Drake Passage",
           "latitude": -58.4, "longitude": -63.2,
           "event_timestamp": "2026-09-25T10:00:00Z", "observed_at": "2026-09-25T10:01:00Z",
           "retrieved_at": "2026-09-25T10:02:00Z", "ingested_at": "2026-09-25T10:03:00Z",
           "source_url": "https://example.invalid/synthetic-event",
           "adapter": "synthetic_fixture", "adapter_version": "1.0",
           "metadata": {"feature_type": "earthquake", "magnitude": 4.9}}
    with tempfile.TemporaryDirectory(prefix="phase11_1_") as directory:
        repository = DatabaseRepository(Path(directory) / "demo.sqlite3")
        first_record = _make_record(raw)
        first = repository.upsert_event(first_record)
        duplicate = repository.upsert_event(first_record)
        updated_raw = dict(raw, title="M 5.0 - Drake Passage", text="M 5.0 - Drake Passage",
                           metadata={"feature_type": "earthquake", "magnitude": 5.0})
        update = repository.upsert_event(_make_record(updated_raw))
        history_saved = repository.save_alert_history({"alert_id": "alert-phase11-demo",
            "event_id": first.event_id, "incident_id": "incident-phase11-demo",
            "from_state": "HIGH", "to_state": "CRITICAL", "change_type": "ESCALATED",
            "reason": "Synthetic demonstration history row", "occurred_at": "2026-09-25T10:05:00Z"})
        run = repository.save_monitoring_run({"run_id": "phase11-demo-run", "source": "usgs",
            "run_mode": "synthetic_database_demo", "started_at": "2026-09-25T10:00:00Z",
            "ended_at": "2026-09-25T10:05:00Z", "status": "completed",
            "records_received": 1, "valid_records": 1, "new_records": 1,
            "duplicate_records": 1, "updated_records": 1,
            "configuration_summary": {"database": "temporary SQLite"}, "errors": []})
        stored = repository.get_event(first.event_id)
        evidence = stored["type_evidence"]
        location = stored["location_evidence"]
        incident = stored["incident_intelligence"][0]
        alert = stored["alerts"][0]
        checks = {
            "single_event_identity": len(repository.list_events()) == 1,
            "idempotent_duplicate": duplicate.state == "DUPLICATE",
            "updated_same_event": update.state == "UPDATED" and update.event_id == first.event_id,
            "revision_history": len(stored["revisions"]) == 2,
            "source_type_retained": evidence["source_type"] == "earthquake",
            "phase4_and_ml_retained": evidence["phase4_type"] == "flood" and evidence["ml_type"] == "hurricane",
            "operational_type_retained": evidence["operational_type"] == "earthquake",
            "disagreement_retained": evidence["model_disagreement"] == 1 and evidence["ml_confidence"] == .9987,
            "source_coordinates_retained": location["source_latitude"] == -58.4 and location["coordinate_source"] == "source_metadata",
            "phase5_priority_retained": incident["priority_score"] == 54.1,
            "alert_retained": alert["alert_id"] == "alert-phase11-demo",
            "history_retained": history_saved and len(stored["alert_history"]) == 1,
            "monitoring_run_retained": run.run_id == "phase11-demo-run" and len(repository.list_monitoring_runs()) == 1,
        }
        if not all(checks.values()):
            raise RuntimeError(f"Database demonstration failed checks: {checks}")
        artifact = {"artifact_type": "DATABASE FOUNDATION DEMONSTRATION",
                    "evaluation_type": "synthetic_local_persistence_sanity_demo",
                    "temporary_database": True, "live_data": False,
                    "disclaimer": "One synthetic source-aware record; not a live-data evaluation or production deployment.",
                    "checks": checks,
                    "event_id": first.event_id,
                    "upsert_states": [first.state, duplicate.state, update.state],
                    "event_version": stored["version"],
                    "persisted_evidence": {"source_type": evidence["source_type"],
                        "phase4_type": evidence["phase4_type"], "ml_type": evidence["ml_type"],
                        "ml_confidence": evidence["ml_confidence"],
                        "agreement_status": evidence["agreement_status"],
                        "operational_type": evidence["operational_type"],
                        "source_coordinates": [location["source_latitude"], location["source_longitude"]],
                        "coordinate_source": location["coordinate_source"],
                        "phase5_priority_score": incident["priority_score"],
                        "alert_id": alert["alert_id"],
                        "alert_history_count": len(stored["alert_history"]),
                        "event_revision_count": len(stored["revisions"])},
                    "limitations": ["The demonstration uses a temporary database and leaves no production database state.",
                                   "Phase 11.1 provides persistence structures only; it does not run Phase 9 alert rules."]}
    OUTPUT.write_text(json.dumps(artifact, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print("DATABASE FOUNDATION DEMONSTRATION")
    print(f"Event: {artifact['event_id']} | upserts: {' -> '.join(artifact['upsert_states'])} | version: {artifact['event_version']}")
    print(f"Type: source={evidence['source_type']} / Phase 4={evidence['phase4_type']} / ML={evidence['ml_type']} "
          f"({evidence['ml_confidence']}) / operational={evidence['operational_type']}")
    print(f"Persistence checks passed: {sum(checks.values())}/{len(checks)}")
    print(f"Artifact: {OUTPUT}")
    return artifact


if __name__ == "__main__":
    main()
