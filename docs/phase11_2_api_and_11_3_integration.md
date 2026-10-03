# Phase 11.2 API and Phase 11.3 Persistence Integration

## Objective and architecture

Phase 11.2 exposes operational data already stored by the Phase 11.1 SQLite repositories. Phase 11.3 connects the existing Phase 8 source adapters and Phase 3–7 processing pipeline, followed by Phase 9 alert evaluation, to those repositories. The API is a read-only projection over persisted records; it does not reimplement classification, extraction, scoring, GIS, or alert rules.

```text
Configured Phase 8 sources
  → normalization and identity/deduplication
  → existing Phase 3–7 intelligence pipeline
  → existing Phase 9 alert evaluator
  → Phase 11.1 SQLite repositories
  → Phase 11.2 read-only API
```

The API and CLI use the same database path resolution and repository layer. The dashboard has not been redesigned or switched to API-backed data in this phase.

## API implementation and endpoints

The API uses Python's standard-library WSGI server, avoiding a new runtime dependency. Start it from the repository root:

```powershell
python -m api.app
```

The default listener is `127.0.0.1:8770`. `--host`, `--port`, and `--db-path` override local settings. The handlers issue parameterized repository queries, enforce page and timestamp validation, return JSON, strip credential-like keys, and do not include stack traces or database paths in client error responses. Normal reads are GET-only.

| Endpoint | Purpose and parameters |
|---|---|
| `GET /health`, `GET /healthz` | Executes an actual SQLite connectivity check. Returns 503 if that check fails. |
| `GET /api/events` | Paginated persisted events. Filters: `source`, `disaster_type`, `operational_type`, `severity`, `priority`, `location`, `since`, `until`, `limit` (1–1000), `offset` (nonnegative). |
| `GET /api/events/{event_id}` | One persisted event, including source provenance/evidence, persisted Phase 3–7 outputs, Phase 5 intelligence, alert data, and revision/version information where available. |
| `GET /api/alerts` | Paginated alerts. Filters: `alert_level`, `active` (`true`/`false`), `source`, `event_id`, `since`, `until`, `limit`, `offset`. |
| `GET /api/alerts/{alert_id}` | Persisted decision, level, rules, explanation, escalation, suppression, notification, timestamps, and alert history. |
| `GET /api/alerts/{alert_id}/history` | Persisted lifecycle entries for an alert. |
| `GET /api/monitoring/runs` | Paginated monitoring runs, optionally filtered by `source`. |
| `GET /api/monitoring/runs/{run_id}` | Full persisted run summary, errors, counts, sanitized configuration summary, processing results, and latency. |
| `GET /api/intelligence/summary` | Aggregates events, operational types, severity/priority, alerts, unresolved locations, and latest monitoring status from SQLite. |

Collection responses have the shape `{"items": [], "total": 0, "limit": 50, "offset": 0}`. Details have an `item` property. Missing detail records return 404; malformed parameters return a structured 400 error. Unexpected server failures are logged locally and returned as a generic structured 500 response.

## Database role and schema migration

SQLite remains the source of truth for API reads. Schema version 2 migrates existing Phase 11.1 files additively: `events.processing_results_json` retains Phase 3–7 processing outputs, and `monitoring_runs` gains processing results and measured latency. Initialization does not discard or rewrite existing records. Existing revision, provenance, source-aware evidence, Phase 5, alert, and history repositories remain in use.

Event detail combines the event row and the Phase 11.1 evidence tables with the persisted Phase 3–7 result object. The API returns missing fields as absent/null/empty according to their stored representation; it does not synthesize evidence.

## Live ingestion and durable state

The live command constructs the existing `MultiSource`, `MonitoringRunner`, `IntelligencePipeline`, and `AlertEvaluator`, then supplies a `DatabaseRepository` to `LiveMonitoringSession`:

```powershell
python -m monitoring.real_runner --once
python -m monitoring.real_runner --poll --cycles 2
```

Phase 11.6 adds the service CLI with bounded transient retries, startup/final run lifecycle persistence, signal-aware continuous mode, and delay-after-completion scheduling. Use `python -m monitoring.live --once` for one cycle or `python -m monitoring.live --interval 360` for the continuous service. See [Phase 11.6 documentation](phase11_6_continuous_monitoring.md). The earlier `real_runner` commands remain available for the Phase 11.3 runner workflow.

The real-source adapters continue to use the configured USGS, GDACS, and RSS/news sources. Source-level failures remain isolated and appear in their run report; successfully fetched records continue through processing and persistence. Each session cycle has a monitoring run ID. The runner persists normalized events, provenance, type/location evidence, Phase 3–7 results, Phase 5 intelligence, Phase 9 decisions/alerts, and an alert-created history record when Phase 9 creates an alert. It writes a final monitoring-run record containing received/valid/invalid and NEW/DUPLICATE/UPDATED counts, structured failures, processing results, and elapsed seconds.

`NEW` creates an event identity and initial revision. `DUPLICATE` updates observation/last-seen data without adding an event or revision. A material source update keeps the same event identity, increments its version, and adds an `event_revisions` snapshot. Phase 9 retains its existing cooldown, suppression, and escalation behavior; the persistence integration does not re-evaluate alert rules.

Persistence errors are returned in the cycle result, attached to the affected processed event when applicable, counted as a partial failure, and reported by the CLI. A database write failure does not silently appear successful. Source failures are stored even when another source returns usable events.

The reusable `LiveMonitoringSession` accepts an optional repository so callers/tests can explicitly select a database. The operational CLI always supplies one, using the configured Phase 11 database by default.

## Configuration and verification

Database configuration remains `DISASTER_DB_PATH`, inherited from Phase 11.1. An unset value uses the ignored local default `data/database/disaster_intelligence.db`. API options are `API_HOST`, `API_PORT`, and `API_LOG_LEVEL`; command-line host/port/path options take precedence. `.env.example` contains placeholders/settings only. No `.env` file or real credentials are included.

To verify persisted live data after a real feed run, call:

```powershell
Invoke-RestMethod http://127.0.0.1:8770/health
Invoke-RestMethod http://127.0.0.1:8770/api/events
Invoke-RestMethod http://127.0.0.1:8770/api/alerts
Invoke-RestMethod http://127.0.0.1:8770/api/monitoring/runs
Invoke-RestMethod http://127.0.0.1:8770/api/intelligence/summary
```

These API reads use only SQLite, not Phase 8 demonstration artifacts. The integration demonstration and API tests use synthetic test data and temporary SQLite databases; they never label that data live or use the configured production/default database.

## Security limitations

The API currently has no authentication or authorization and is intended for local development on loopback. Do not expose it to an untrusted network. Before deployment, add an approved authentication/authorization design, TLS termination, request/rate limits, audit controls, retention policy, and deployment-specific secret management. Persisted source URLs and evidence are sanitized for credential-like fields, but this is not a substitute for production security review.

## Current limitations and future work

- SQLite is a local single-node persistence layer; PostGIS and production database operations remain future work.
- The API is read-only and does not provide a write/administration surface.
- Monitoring-run source is currently summarized as `multi_source`; per-source result/status/count detail is retained in its processing results.
- Persisted event records retain the latest source evidence and latest processed phase outputs; material raw source revisions are retained in the Phase 11.1 revision table.
- Alert history is appended when an alert is created. Existing Phase 9 state remains in-memory between runner process restarts; durable alert evaluator state/restart-safe cooldown reconstruction is future hardening.
- Phase 10 continues to load its existing artifact/input contract. A thin API-backed dashboard adapter can be added later without changing the UI.
- No social/public-information source, external notification service, live monitoring claim in the demonstration, or cloud deployment is added here.

Phase 7 can continue to consume persisted grounded evidence through the existing processing outputs. Phase 8 can add future sources by implementing the existing `EventSource` contract. Phase 10 can later switch its data adapter to these endpoints.
