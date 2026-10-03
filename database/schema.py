"""Idempotent SQLite schema initialization for Phase 11.1."""

import argparse

from database.config import get_database_path
from database.connection import transaction


SCHEMA_VERSION = 2

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        source_event_id TEXT,
        title TEXT,
        text TEXT NOT NULL,
        original_text TEXT,
        source_url TEXT,
        location_text TEXT,
        latitude REAL,
        longitude REAL,
        event_timestamp TEXT,
        published_at TEXT,
        observed_at TEXT,
        source_updated_at TEXT,
        retrieved_at TEXT,
        ingested_at TEXT,
        disaster_type TEXT,
        canonical_disaster_type TEXT,
        content_fingerprint TEXT,
        revision_fingerprint TEXT,
        event_state TEXT NOT NULL DEFAULT 'NEW',
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        first_seen TEXT,
        last_seen TEXT,
        processing_status TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        processing_results_json TEXT NOT NULL DEFAULT '{}',
        raw_event_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(source, source_event_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_events_source_time ON events(source, event_timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_events_type_priority ON events(canonical_disaster_type, event_id)",
    """CREATE TABLE IF NOT EXISTS event_revisions (
        revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        revision_fingerprint TEXT,
        observed_at TEXT,
        recorded_at TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(event_id, version)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_event_revisions_event ON event_revisions(event_id, version DESC)",
    """CREATE TABLE IF NOT EXISTS event_provenance (
        event_id TEXT PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
        source TEXT NOT NULL,
        source_type TEXT,
        source_event_id TEXT,
        source_url TEXT,
        adapter TEXT,
        adapter_version TEXT,
        observed_at TEXT,
        event_timestamp TEXT,
        published_at TEXT,
        source_updated_at TEXT,
        retrieved_at TEXT,
        ingested_at TEXT,
        processing_status TEXT,
        provenance_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS disaster_type_evidence (
        event_id TEXT PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
        operational_type TEXT,
        source_type_json TEXT,
        source_canonical_type TEXT,
        phase4_type_json TEXT,
        phase4_canonical_type TEXT,
        ml_type TEXT,
        ml_canonical_type TEXT,
        ml_confidence REAL,
        agreement_status TEXT,
        type_agreement INTEGER CHECK(type_agreement IN (0,1) OR type_agreement IS NULL),
        model_disagreement INTEGER NOT NULL DEFAULT 0 CHECK(model_disagreement IN (0,1)),
        resolution_method TEXT,
        resolution_rationale TEXT,
        warnings_json TEXT NOT NULL DEFAULT '[]',
        evidence_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_type_evidence_operational ON disaster_type_evidence(operational_type)",
    "CREATE INDEX IF NOT EXISTS idx_type_evidence_agreement ON disaster_type_evidence(agreement_status)",
    """CREATE TABLE IF NOT EXISTS location_evidence (
        event_id TEXT PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
        source_location_text TEXT,
        source_latitude REAL,
        source_longitude REAL,
        source_coordinate_provenance TEXT,
        phase4_location_entities_json TEXT NOT NULL DEFAULT '[]',
        geocoded_location_text TEXT,
        geocoded_latitude REAL,
        geocoded_longitude REAL,
        coordinate_source TEXT,
        geocoding_status TEXT,
        evidence_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_location_coordinates ON location_evidence(geocoded_latitude, geocoded_longitude)",
    """CREATE TABLE IF NOT EXISTS incident_intelligence (
        incident_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
        severity_score REAL,
        severity_level TEXT,
        urgency_score REAL,
        urgency_level TEXT,
        confidence_score REAL,
        confidence_level TEXT,
        priority_score REAL,
        priority_level TEXT,
        decision_flags_json TEXT NOT NULL DEFAULT '[]',
        resource_priorities_json TEXT NOT NULL DEFAULT '{}',
        resource_estimates_json TEXT NOT NULL DEFAULT '{}',
        duplicate_info_json TEXT NOT NULL DEFAULT '{}',
        phase_version TEXT,
        processing_metadata_json TEXT NOT NULL DEFAULT '{}',
        intelligence_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_incidents_event ON incident_intelligence(event_id)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_priority ON incident_intelligence(priority_level, priority_score DESC)",
    """CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_id TEXT UNIQUE,
        evaluation_key TEXT NOT NULL UNIQUE,
        event_id TEXT REFERENCES events(event_id) ON DELETE SET NULL,
        incident_id TEXT,
        alert_level TEXT,
        alert_status TEXT,
        alert_type TEXT,
        alert_decision_json TEXT NOT NULL DEFAULT '{}',
        triggering_rules_json TEXT NOT NULL DEFAULT '[]',
        score_evidence_json TEXT NOT NULL DEFAULT '{}',
        escalation_state_json TEXT NOT NULL DEFAULT '{}',
        suppressed INTEGER CHECK(suppressed IN (0,1) OR suppressed IS NULL),
        suppression_reason TEXT,
        notification_status TEXT,
        created_at TEXT,
        updated_at TEXT NOT NULL,
        raw_alert_json TEXT NOT NULL DEFAULT '{}'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_alerts_event_created ON alerts(event_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_level_status ON alerts(alert_level, alert_status)",
    """CREATE TABLE IF NOT EXISTS alert_history (
        history_id INTEGER PRIMARY KEY AUTOINCREMENT,
        history_key TEXT NOT NULL UNIQUE,
        alert_id TEXT,
        event_id TEXT REFERENCES events(event_id) ON DELETE SET NULL,
        incident_id TEXT,
        from_state TEXT,
        to_state TEXT,
        change_type TEXT,
        reason TEXT,
        occurred_at TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_alert_history_event_time ON alert_history(event_id, occurred_at DESC)",
    """CREATE TABLE IF NOT EXISTS monitoring_runs (
        run_id TEXT PRIMARY KEY,
        source TEXT,
        run_mode TEXT,
        started_at TEXT,
        ended_at TEXT,
        status TEXT,
        records_received INTEGER,
        valid_records INTEGER,
        invalid_records INTEGER,
        new_records INTEGER,
        duplicate_records INTEGER,
        updated_records INTEGER,
        errors_json TEXT NOT NULL DEFAULT '[]',
        configuration_json TEXT NOT NULL DEFAULT '{}',
        processing_results_json TEXT NOT NULL DEFAULT '{}',
        latency_seconds REAL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_monitoring_runs_source_time ON monitoring_runs(source, started_at DESC)",
)


def initialize_database(path=None):
    """Create schema and indexes safely without deleting persisted records."""
    with transaction(path) as connection:
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise RuntimeError(f"Database schema version {current} is newer than supported {SCHEMA_VERSION}")
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        # Phase 11.2/11.3 additive migration: preserve existing Phase 11.1 rows.
        _ensure_column(connection, "events", "processing_results_json", "TEXT NOT NULL DEFAULT '{}'" )
        _ensure_column(connection, "monitoring_runs", "processing_results_json", "TEXT NOT NULL DEFAULT '{}'" )
        _ensure_column(connection, "monitoring_runs", "latency_seconds", "REAL")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return get_database_path(path)


def _ensure_column(connection, table, column, declaration):
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def main():
    parser = argparse.ArgumentParser(description="Initialize the local disaster intelligence SQLite database.")
    parser.add_argument("--db-path", help="Override the configured SQLite file path")
    args = parser.parse_args()
    path = initialize_database(args.db_path)
    print(f"Database initialized: {path}")


if __name__ == "__main__":
    main()
