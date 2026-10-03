# Phase 11.1 — Database and Persistence Foundation

## Objective and scope

Phase 11.1 established a local persistence foundation for records produced by the existing Phases 5–10. Phase 11.2/11.3 now expose persisted records through a read-only API and connect the operational CLI to the repositories. The Phase 8 deduplication state and Phase 9 escalation state remain in-memory; restart-safe reconstruction remains future work.

SQLite is used because it ships with Python, works offline, requires no server, and provides transactions and relational constraints. The schema uses standard tables, foreign keys, stable text identities, and JSON text for extensible evidence, keeping a later PostgreSQL migration practical without adding an ORM or database dependency.

## Location and configuration

The default file is `data/database/disaster_intelligence.db`, outside tracked source files. The directory and database extensions are ignored by Git. Override the location using the `DISASTER_DB_PATH` environment variable or pass an explicit path to `DatabaseRepository`/`initialize_database`. Relative overrides resolve from the current working directory. No user-specific absolute paths are embedded in code.

Initialize the default database with:

```powershell
python -m database.schema
```

Initialization uses idempotent table creation and additive migrations, currently schema version 2, and never deletes existing data. Version 2 adds persisted Phase 3–7 processing results and monitoring-run processing details/latency. Constructing `DatabaseRepository(path)` initializes that selected database by default.

## Schema overview

- `events` stores the latest normalized source event, the existing deterministic `event_id`, source/source-event identity, event and ingestion timestamps, coordinates, lifecycle state/version, fingerprints, metadata, Phase 3–7 processing outputs, and a sanitized source snapshot.
- `event_revisions` stores one source payload snapshot for the initial event and each material source revision.
- `event_provenance` stores the latest adapter, source URL, timestamps, and processing provenance. Event time and publication time remain separate fields.
- `disaster_type_evidence` stores operational type, authoritative source type, Phase 4 type, Phase 3 type/confidence, agreement status, disagreement flag, resolution method/rationale, warnings, and retained evidence JSON separately.
- `location_evidence` stores source text/coordinates/provenance and Phase 4 NLP entities separately from Phase 6 geocoded text/coordinates/status. Source coordinates are never overwritten by geocoder output.
- `incident_intelligence` stores Phase 5 scores/levels, flags, resource priorities/estimates, duplicate data, processing metadata, and the Phase 5 record.
- `alerts` stores alert decisions/records, trigger rules, score evidence, escalation/suppression state, and notification status. `alert_history` can store explicit state-change observations supplied by the caller.
- `monitoring_runs` stores source/run state, lifecycle counts, structured errors, sanitized configuration summaries, processing results, and latency when supplied.

## Identity, idempotency, and updates

`upsert_event` uses the project `monitoring.identity.event_id_for` identity when it is not already present. Source plus source event ID is also unique where a source ID exists. It compares the existing Phase 8 revision fingerprint (or a deterministic fallback for raw records): a first observation returns `NEW`, an unchanged observation returns `DUPLICATE`, and a changed revision returns `UPDATED`. Duplicate observations update last-seen/retrieval metadata without adding an event or revision. An update modifies the existing event identity and appends a revision snapshot rather than discarding the prior source payload.

The latest provenance and evidence rows are upserted with the event in one transaction. Incident/alert repositories are idempotent by incident/alert identity; alert history uses a deterministic key so an identical supplied history entry is not inserted twice. History records only data explicitly passed to it; the persistence layer does not infer transitions.

## Source-aware type and location data

The type table deliberately does not flatten the evidence into a single disaster-type column. It retains the authoritative source value and canonical mapping, Phase 4 extraction, Phase 3 label/confidence, final operational type, agreement/disagreement state, and resolution rationale. A source/ML disagreement therefore remains queryable after persistence.

The location table keeps structured source text/coordinates and Phase 4 NLP entities separately. Phase 6 output with `coordinate_source=source_metadata` remains source-coordinate evidence; coordinates attributed to the Phase 6 geocoder use separate geocoded columns. JSON evidence fields preserve additional source/phase details.

## Transactions, JSON, and secrets

Connections are short-lived, enable SQLite foreign-key checks, use a busy timeout and WAL for local concurrent reads/writes, and close in `finally` blocks. Repository writes run in explicit transactions and roll back on any exception. JSON uses stable key ordering and compact encoding; malformed optional JSON reads return documented defaults. Non-finite numbers become JSON null. Recursive serialization removes credential-like fields and strips secret query parameters from URL fields; do not provide credentials to persistence APIs in the first place.

## Repository API

`DatabaseRepository` provides `upsert_event`, event filters/details, alert/run queries and details, intelligence summaries, evidence/intelligence/alert/history/run persistence, health checks, and bounded transactions. Methods use parameterized SQL; associated writes made by `upsert_event` are atomic with the event update. `connect` and `transaction` are also available for bounded lower-level work.

## Validation and demonstration

```powershell
pytest tests\test_phase11_database.py -v
python evaluation\run_phase11_1_database_demonstration.py
```

The demonstration uses one bounded synthetic source-aware record in a temporary SQLite file, validates duplicate and updated-event behavior, and writes `evaluation/phase11_1_database_results.json`. It does not touch the default database or fetch live records.

## Limitations and later phases

This remains a local persistence foundation, not a production database service. The API is read-only and unauthenticated for local use. SQLite is single-node; PostgreSQL/PostGIS deployment, authentication, multi-host coordination, backup automation, durable Phase 8/9 state recovery, social/public information sources, dashboard/API integration, and production high-availability guarantees remain future work. Existing Phase 1–10 scoring and processing responsibilities remain authoritative.
