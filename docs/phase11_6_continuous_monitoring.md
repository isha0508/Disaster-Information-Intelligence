# Phase 11.6 — Continuous Monitoring Service

## Purpose and architecture

Phase 11.6 provides a local service and CLI around the existing Phase 8 `LiveMonitoringSession`. It reuses configured USGS GeoJSON, GDACS, and optional RSS adapters; the Phase 8 event normalizer, identity, deduplication, and update detection; the existing Phase 3–7 pipeline; Phase 9 alert evaluation; and Phase 11 SQLite repositories. It does not introduce a second event-processing pipeline.

```text
Configured sources → bounded fetch/retry → Phase 8 normalization and identity
                   → existing Phase 3–7 pipeline → Phase 9 alerts
                   → Phase 11 event/run persistence → REST API → Phase 11.5 dashboard
```

The session writes a `running` monitoring-run row before retrieving feeds, then updates that same run ID with final status, timestamps, duration, source reports, counts, and errors. Event upserts use the repository's stable identity and revision behavior. Source coordinates and provenance pass through the existing GIS pipeline and persistence records. The API and dashboard continue to read persisted data without importing this service.

## CLI

From the repository root:

```powershell
python -m monitoring.live --help
python -m monitoring.live --once
python -m monitoring.live --interval 360
```

The default is continuous mode. `--once` performs one complete configured cycle and exits. Continuous mode measures its interval as a delay **after a cycle completes**; a cycle taking 90 seconds with `--interval 60` is followed by 60 seconds of delay. Cycles execute serially, and both the service and session guard against concurrent calls. No cycle starts while the previous one is still processing or persisting.

Optional flags include `--max-events-per-source N`, `--retry-count N`, `--retry-backoff SECONDS`, `--database PATH` (also `--db-path`), and `--log-level LEVEL`. Feed polling is constrained by the existing public-source minimum of 60 seconds. A database override changes only this process; otherwise `DISASTER_DB_PATH` and the existing local default apply.

## Configuration

Configuration is read from process environment; `.env` is not loaded automatically. See `.env.example`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `USGS_ENABLED` / `USGS_FEED_URL` | enabled / official all-day GeoJSON | USGS source adapter selection and endpoint |
| `GDACS_ENABLED` / `GDACS_FEED_URL` | enabled / official events API | GDACS source selection and endpoint |
| `GDACS_EVENT_TYPES` / `GDACS_DAYS` | existing adapter defaults | GDACS query scope |
| `NEWS_ENABLED` / `DISASTER_RSS_FEEDS` | enabled / blank feeds | RSS adapter selection; blank feeds produce `not_configured` |
| `DISASTER_SOURCE_TIMEOUT` | 20 seconds | Per-request timeout |
| `DISASTER_POLL_INTERVAL` | 360 seconds | Delay between completed cycles; minimum 60 seconds |
| `DISASTER_MAX_EVENTS_PER_SOURCE` | 10 | Events selected per source per cycle |
| `DISASTER_SOURCE_RETRY_COUNT` | 2 | Retries after the initial request, maximum 10 |
| `DISASTER_SOURCE_RETRY_BACKOFF` | 1 second | Exponential backoff base, bounded to 30 seconds |
| `DISASTER_MONITORING_LOG_LEVEL` | `INFO` | Default service log level |
| `DISASTER_DB_PATH` | ignored local SQLite file | Existing Phase 11 database path |

Equivalent command-line values override interval, event cap, retries, backoff, database path, and log level. Phase 3 remains enabled by the service; model errors are retained as phase failures and are not replaced by fabricated predictions. If downstream phases can proceed with source evidence, they still run and the processing failure remains visible.

## Retry, failure, and status behavior

Existing provider adapters retain their parsing and provenance semantics. The service retries only adapter reports marked error/partial when their messages indicate transient network/timeout or HTTP 408, 425, 429, or 5xx errors. It does not retry malformed data, invalid feed configuration, or other permanent errors. Retry count is bounded; backoff doubles after each attempt and is capped at 30 seconds. Each source report records attempt count and retry failures. Other sources continue independently.

The persisted run status uses the existing lowercase conventions:

- `running` while the cycle is active.
- `success` when configured retrieval/processing/persistence completed without recorded failures (an empty successful feed response is valid).
- `partial_failure` when one or more sources, events, phases, or persistence operations fail while usable data was still processed.
- `failed` when the cycle has no usable records because retrieval/processing failed or no source is configured.

The run record includes received/valid/invalid counts, database NEW/DUPLICATE/UPDATED counts, per-source selected counts, processing and alert counts, source reports, errors, configuration summary, and elapsed seconds. A source disabled by configuration is absent; an enabled RSS adapter with no feeds reports `not_configured`. No records are invented when every source fails or is unconfigured.

## Shutdown and concurrency

`Ctrl+C` (SIGINT) and SIGTERM request shutdown. The signal handler stops future scheduling; the current synchronous source/pipeline/database cycle is allowed to return and finalize its run record, then the loop exits. A signal during an HTTP request may therefore wait for that source's configured request timeout and any already-started bounded retry delay. Repository operations use their normal scoped SQLite connections/transactions.

## API and dashboard

The service persists through `DatabaseRepository` and does not write SQL directly. Each cycle uses a stable run ID for its initial `running` row and final update. Event, revision, alert and alert-history writes keep the existing repository contracts. `GET /api/monitoring/runs`, event/alert endpoints, and the intelligence summary already expose the fields consumed by Phase 11.5; no API contract changes are needed.

The dashboard remains an API client. On its next bounded refresh it reads newly persisted events, source statuses, alerts, and summaries. The monitoring process does not call dashboard code, and the dashboard does not import monitoring internals. To observe the normal local database, start the API and dashboard with the same `DISASTER_DB_PATH`, then run the monitoring service against that path.

Example terminals:

```powershell
python -m api.app
python -m dashboard.app
python -m monitoring.live --once
```

For continuous polling:

```powershell
python -m monitoring.live --interval 360 --retry-count 2 --retry-backoff 1
```

## Demonstration and tests

`python evaluation/run_phase11_6_monitoring_demonstration.py` executes one cycle using the enabled/configured sources and the same service architecture. It uses a temporary SQLite database by default, performs an API/dashboard read against that same database, and never inserts synthetic records. `--database PATH` opts into persistence at a specified path. The artifact reports source status, retries, cycle counts, persistence, errors, and dashboard-visible persisted data. A `live` run mode means enabled public source adapters were actually attempted; `live_source_retrieval_succeeded` separately confirms whether an adapter returned a successful/empty/partial response. Do not treat a failed network attempt as live-source validation.

Offline unit tests use deterministic source responses and temporary databases. They cover CLI help/one-shot dispatch, settings, successful/partial/failed cycles, retry limits, serial cycles, shutdown requests, run/event persistence, Phase 3 error visibility, deduplication and revision updates, alert storage, API visibility, and credential redaction. They do not require public feed availability.

## Troubleshooting and limitations

- `not_configured`: check source enablement and, for RSS, `DISASTER_RSS_FEEDS`.
- `error` with retries: inspect the persisted run's per-source errors, request timeout, connectivity, and endpoint configuration.
- A run stuck at `running` indicates a process interruption or database failure before finalization; inspect logs and the SQLite file before starting another operator action.
- `python -m monitoring.live --help` is warning-free after removing the eager `monitoring.live` package import; `LiveMonitoringSession` stays available through a lazy package export.
- Phase 3 availability depends on installed ML packages and local model artifacts. Failures are reported; no fixed prediction is substituted.
- The service is a single-process local runner, not a distributed scheduler or authenticated deployment. SIGTERM waits for a synchronous cycle to reach a safe boundary. Network calls can be delayed by timeout and bounded retries.
- RSS sources remain optional and subject to publisher terms. Public-source retrieval does not establish the accuracy of source content or geolocation. Dashboard visibility is limited to persisted fields and its API page sizes.
