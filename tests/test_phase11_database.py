"""Phase 11.1 SQLite persistence tests using temporary local databases only."""

import json
import sqlite3

import pytest

from database import DatabaseRepository, get_database_path, initialize_database
from monitoring.identity import event_id_for
from monitoring.normalization import normalize_event


def sample_type_evidence():
    return {
        "source_disaster_type": {"value": "earthquake", "canonical_value": "earthquake",
                                 "source": "usgs", "source_authority": "USGS",
                                 "provenance": "authoritative_structured_metadata", "status": "resolved"},
        "phase4_disaster_type": {"value": "flood", "canonical_value": "flood", "provenance": "phase4_nlp"},
        "ml_disaster_type": {"value": "hurricane", "canonical_value": "cyclone",
                             "confidence": .9987, "model": "distilbert_disaster_type",
                             "provenance": "phase3_ml"},
        "canonical_disaster_type": "earthquake", "type_agreement": False,
        "type_agreement_status": "DISAGREEMENT", "model_disagreement": True,
        "resolution_method": "authoritative_source",
        "resolution_rationale": "Authoritative USGS source type resolved to earthquake.",
        "warning": None,
    }


def sample_record():
    raw = {"source": "usgs", "source_type": "official_public_feed", "source_event_id": "demo-event-1",
           "title": "M 4.9 - Drake Passage", "text": "M 4.9 - Drake Passage",
           "location_text": "Drake Passage", "latitude": -58.4, "longitude": -63.2,
           "event_timestamp": "2026-09-25T10:00:00Z", "observed_at": "2026-09-25T10:01:00Z",
           "retrieved_at": "2026-09-25T10:02:00Z", "ingested_at": "2026-09-25T10:03:00Z",
           "source_url": "https://example.test/event?id=demo", "adapter": "USGSGeoJSONSource",
           "adapter_version": "1.0", "metadata": {"feature_type": "earthquake", "magnitude": 4.9}}
    event = normalize_event(raw)
    event_id = event_id_for(event)
    evidence = sample_type_evidence()
    phase4 = {"disaster_type": "flood", "location": [], "nlp_location_entities": []}
    phase5 = {"event_id": event_id, "incident_id": "incident-demo-1", "disaster_type": "earthquake",
              "canonical_disaster_type": "earthquake", "type_resolution": evidence,
              "severity_score": 64.0, "severity_level": "HIGH", "urgency_score": 42.0,
              "urgency_level": "MODERATE", "confidence_score": 82.0, "confidence_level": "HIGH",
              "priority_score": 54.1, "priority_level": "HIGH", "decision_flags": ["CASUALTY_REPORT"],
              "resource_priorities": {"medical": "high"}, "resource_estimates": {"medical": 12},
              "is_duplicate": False, "duplicate_of": None,
              "processing_metadata": {"phase5_version": "1.0"},
              "source_location": {"text": "Drake Passage", "latitude": -58.4, "longitude": -63.2,
                                  "provenance": "authoritative_structured_metadata"},
              "nlp_location_entities": []}
    phase6_row = {"incident_id": "incident-demo-1", "location_text": "Drake Passage",
                  "latitude": -58.4, "longitude": -63.2, "geocoding_status": "success",
                  "geocoding_source": "source_metadata", "coordinate_source": "source_metadata",
                  "normalized_location": "Drake Passage"}
    alert = {"alert_decision": {"should_alert": True, "alert_id": "alert-demo-1",
                                "alert_level": "HIGH", "alert_type": "OPERATIONAL",
                                "event_id": event_id, "incident_id": "incident-demo-1",
                                "priority_score": 54.1, "severity_score": 64.0, "urgency_score": 42.0,
                                "confidence_score": 82.0, "trigger_reasons": [{"rule": "high_priority"}],
                                "is_escalation": False, "suppressed": False},
             "alert_record": {"alert_id": "alert-demo-1", "event_id": event_id,
                              "incident_id": "incident-demo-1", "alert_level": "HIGH",
                              "alert_type": "OPERATIONAL", "status": "CREATED",
                              "created_at": "2026-09-25T10:04:00Z",
                              "priority_score": 54.1, "severity_score": 64.0, "urgency_score": 42.0,
                              "confidence_score": 82.0,
                              "trigger_reasons": [{"rule": "high_priority"}],
                              "escalation": {"is_escalation": False},
                              "suppression": {"suppressed": False},
                              "notification": {"status": "MOCKED"}},
             "alert_status": "CREATED", "alert_evaluated_at": "2026-09-25T10:04:00Z",
             "alert_suppression": {"suppressed": False}}
    record = {"event": event, "event_id": event_id, "source": "usgs", "event_state": "NEW",
              "processing_status": "PROCESSED", "provenance": event["provenance"],
              "type_resolution": evidence,
              "source_location": {"text": "Drake Passage", "latitude": -58.4, "longitude": -63.2,
                                  "source": "usgs", "provenance": "authoritative_structured_metadata",
                                  "coordinate_provenance": "source_metadata"},
              "intelligence": {"phase4": phase4, "phase5": phase5,
                               "phase6": {"incidents": [phase6_row]}},
              **alert}
    return raw, event, record


@pytest.fixture
def repository(tmp_path):
    return DatabaseRepository(tmp_path / "test.sqlite3")


def test_initialization_creates_schema_and_is_idempotent(tmp_path):
    path = tmp_path / "nested" / "disaster.db"
    initialize_database(path)
    repo = DatabaseRepository(path)
    repo.upsert_event(sample_record()[2])
    initialize_database(path)
    assert len(repo.list_events()) == 1
    with sqlite3.connect(path) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"events", "event_provenance", "disaster_type_evidence", "location_evidence",
            "incident_intelligence", "alerts", "alert_history", "monitoring_runs", "event_revisions"} <= names


def test_version_one_schema_is_migrated_additively_without_losing_rows(tmp_path):
    path = tmp_path / "phase11-v1.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE events (
            event_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_event_id TEXT,
            event_timestamp TEXT, canonical_disaster_type TEXT, processing_status TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}', raw_event_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        connection.execute("INSERT INTO events(event_id,source,created_at,updated_at) VALUES(?,?,?,?)",
                           ("preserve-me", "usgs", "now", "now"))
        connection.execute("PRAGMA user_version=1")
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
        assert "processing_results_json" in columns
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT event_id FROM events").fetchone()[0] == "preserve-me"


def test_event_insert_retrieval_and_deterministic_identity(repository):
    _, event, record = sample_record()
    result = repository.upsert_event(record)
    assert result.event_id == event_id_for(event)
    stored = repository.get_event(result.event_id)
    assert stored["source"] == "usgs"
    assert stored["source_event_id"] == "demo-event-1"
    assert stored["raw_event"]["metadata"]["feature_type"] == "earthquake"
    assert repository.get_event_by_source_id("usgs", "demo-event-1")["event_id"] == result.event_id


def test_duplicate_upsert_does_not_create_another_event_or_revision(repository):
    record = sample_record()[2]
    first = repository.upsert_event(record)
    second = repository.upsert_event(record)
    assert first.state == "NEW"
    assert second.state == "DUPLICATE"
    assert second.version == 1
    assert len(repository.list_events()) == 1
    assert len(repository.get_event(first.event_id)["revisions"]) == 1


def test_updated_source_event_changes_same_row_and_preserves_revision(repository):
    raw, _, record = sample_record()
    first = repository.upsert_event(record)
    changed = dict(raw, text="M 5.1 - Drake Passage updated", title="M 5.1 - Drake Passage updated",
                   metadata={"feature_type": "earthquake", "magnitude": 5.1})
    updated_event = normalize_event(changed)
    wrapper = dict(record, event=updated_event)
    result = repository.upsert_event(wrapper)
    stored = repository.get_event(first.event_id)
    assert result.event_id == first.event_id
    assert result.state == "UPDATED" and result.version == 2
    assert stored["text"] == "M 5.1 - Drake Passage updated"
    assert len(stored["revisions"]) == 2


def test_provenance_retains_source_and_distinct_timestamps(repository):
    result = repository.upsert_event(sample_record()[2])
    provenance = repository.get_event(result.event_id)["provenance"]
    assert provenance["source"] == "usgs"
    assert provenance["adapter"] == "USGSGeoJSONSource"
    assert provenance["event_timestamp"] == "2026-09-25T10:00:00Z"
    assert provenance["published_at"] is None
    assert provenance["details"]["retrieved_at"] == "2026-09-25T10:02:00Z"


def test_source_phase4_ml_and_operational_types_persist_separately(repository):
    result = repository.upsert_event(sample_record()[2])
    evidence = repository.get_event(result.event_id)["type_evidence"]
    assert evidence["operational_type"] == "earthquake"
    assert evidence["source_type"] == "earthquake"
    assert evidence["source_canonical_type"] == "earthquake"
    assert evidence["phase4_type"] == "flood"
    assert evidence["phase4_canonical_type"] == "flood"
    assert evidence["ml_type"] == "hurricane"
    assert evidence["ml_confidence"] == .9987
    assert evidence["agreement_status"] == "DISAGREEMENT"
    assert evidence["type_agreement"] == 0
    assert evidence["model_disagreement"] == 1
    assert evidence["resolution_method"] == "authoritative_source"


def test_source_coordinates_and_phase4_locations_remain_separate(repository):
    result = repository.upsert_event(sample_record()[2])
    location = repository.get_event(result.event_id)["location_evidence"]
    assert location["source_location_text"] == "Drake Passage"
    assert (location["source_latitude"], location["source_longitude"]) == (-58.4, -63.2)
    assert location["source_coordinate_provenance"] == "source_metadata"
    assert location["phase4_location_entities"] == []
    assert location["geocoded_latitude"] is None
    assert location["coordinate_source"] == "source_metadata"


def test_phase5_scores_resources_and_flags_persist(repository):
    result = repository.upsert_event(sample_record()[2])
    incident = repository.get_event(result.event_id)["incident_intelligence"][0]
    assert incident["severity_score"] == 64.0 and incident["severity_level"] == "HIGH"
    assert incident["urgency_score"] == 42.0 and incident["priority_score"] == 54.1
    assert incident["confidence_score"] == 82.0
    assert incident["decision_flags"] == ["CASUALTY_REPORT"]
    assert incident["resource_priorities"] == {"medical": "high"}
    assert incident["resource_estimates"] == {"medical": 12}


def test_alert_upsert_and_history_are_persisted_idempotently(repository):
    record = sample_record()[2]
    result = repository.upsert_event(record)
    stored = repository.get_event(result.event_id)
    assert len(stored["alerts"]) == 1
    assert stored["alerts"][0]["alert_id"] == "alert-demo-1"
    assert stored["alerts"][0]["alert_level"] == "HIGH"
    assert stored["alerts"][0]["triggering_rules"] == [{"rule": "high_priority"}]
    assert repository.save_alert_history({"alert_id": "alert-demo-1", "event_id": result.event_id,
        "incident_id": "incident-demo-1", "from_state": "HIGH", "to_state": "CRITICAL",
        "change_type": "ESCALATED", "occurred_at": "2026-09-25T10:05:00Z", "reason": "score increased"})
    assert not repository.save_alert_history({"alert_id": "alert-demo-1", "event_id": result.event_id,
        "incident_id": "incident-demo-1", "from_state": "HIGH", "to_state": "CRITICAL",
        "change_type": "ESCALATED", "occurred_at": "2026-09-25T10:05:00Z", "reason": "score increased"})
    assert repository.get_event(result.event_id)["alert_history"][0]["to_state"] == "CRITICAL"


def test_monitoring_run_persistence_and_secret_sanitization(repository):
    saved = repository.save_monitoring_run({"run_id": "run-1", "source": "usgs", "mode": "live",
        "started_at": "2026-09-25T10:00:00Z", "ended_at": "2026-09-25T10:01:00Z", "status": "success",
        "records_received": 4, "valid_records": 3, "invalid_records": 1, "new_records": 2,
        "duplicate_records": 1, "updated_records": 0,
        "configuration": {"timeout": 10, "api_key": "DO_NOT_STORE", "nested": {"password": "secret"}},
        "errors": []})
    run = repository.list_monitoring_runs()[0]
    assert saved.run_id == "run-1" and run["records_received"] == 4
    assert run["configuration_summary"] == {"nested": {}, "timeout": 10}
    assert "DO_NOT_STORE" not in json.dumps(run)


def test_optional_fields_missing_and_json_values_round_trip(repository):
    event = {"source": "rss", "source_event_id": "optional-1", "text": "A report",
             "metadata": {"tags": ["one", "two"], "n": float("nan")}}
    result = repository.upsert_event(event)
    stored = repository.get_event(result.event_id)
    assert stored["title"] is None and stored["latitude"] is None
    assert stored["metadata"] == {"n": None, "tags": ["one", "two"]}
    assert stored["type_evidence"] is None
    assert repository.list_events(source="rss")[0]["event_id"] == result.event_id


def test_malformed_optional_nested_location_evidence_is_preserved_safely(repository):
    record = sample_record()[2]
    record["source_location"] = {"text": {"raw": "Drake Passage"},
                                 "latitude": ["invalid"], "coordinate_provenance": {"kind": "source"}}
    record["intelligence"]["phase6"] = {"incidents": []}
    result = repository.upsert_event(record)
    location = repository.get_event(result.event_id)["location_evidence"]
    assert location["source_location_text"] == '{"raw":"Drake Passage"}'
    assert location["source_latitude"] is None
    assert location["source_coordinate_provenance"] == '{"kind":"source"}'


def test_transaction_rolls_back_prior_writes_on_failure(repository):
    with pytest.raises(sqlite3.IntegrityError):
        with repository.transaction() as connection:
            connection.execute("INSERT INTO monitoring_runs(run_id, created_at) VALUES(?,?)", ("rollback-me", "now"))
            connection.execute("INSERT INTO incident_intelligence(incident_id,event_id,created_at,updated_at) VALUES(?,?,?,?)",
                               ("bad", "missing-event", "now", "now"))
    assert repository.list_monitoring_runs() == []


def test_database_path_environment_override(monkeypatch, tmp_path):
    path = tmp_path / "configured" / "local.sqlite"
    monkeypatch.setenv("DISASTER_DB_PATH", str(path))
    assert get_database_path() == path
    repo = DatabaseRepository()
    assert path.exists()
    assert repo.db_path == path


def test_relative_database_override_is_resolved_from_project_root(monkeypatch, tmp_path):
    from database.config import PROJECT_ROOT

    monkeypatch.setenv("DISASTER_DB_PATH", "data/database/disaster_intelligence.db")
    monkeypatch.chdir(tmp_path)
    assert get_database_path() == PROJECT_ROOT / "data" / "database" / "disaster_intelligence.db"


def test_provenance_and_raw_json_never_store_secret_fields(repository):
    record = sample_record()[2]
    record["event"]["metadata"]["api_token"] = "SECRET123"
    record["provenance"]["authorization"] = "Bearer SECRET123"
    result = repository.upsert_event(record)
    serialized = json.dumps(repository.get_event(result.event_id), ensure_ascii=False)
    assert "SECRET123" not in serialized
    assert "authorization" not in serialized.casefold()


def test_standalone_repository_methods_require_existing_event(repository):
    with pytest.raises(ValueError, match="event not found"):
        repository.save_type_evidence("missing", sample_type_evidence())


def test_integration_event_source_type_incident_and_alert_survive_round_trip(repository):
    record = sample_record()[2]
    result = repository.upsert_event(record)
    loaded = repository.get_event(result.event_id)
    assert loaded["provenance"]["source"] == "usgs"
    assert loaded["type_evidence"]["source_type"] == "earthquake"
    assert loaded["type_evidence"]["phase4_type"] == "flood"
    assert loaded["type_evidence"]["ml_type"] == "hurricane"
    assert loaded["type_evidence"]["operational_type"] == "earthquake"
    assert loaded["location_evidence"]["source_latitude"] == -58.4
    assert loaded["incident_intelligence"][0]["priority_score"] == 54.1
    assert loaded["alerts"][0]["alert_id"] == "alert-demo-1"
