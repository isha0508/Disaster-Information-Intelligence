# Phase 11.8 — Full System Integration

## Objective and architecture

Phase 11.8 verifies the existing operational path from public source adapters to persisted intelligence and API-backed dashboard data. It reuses the Phase 11.6 monitoring service and the existing Phase 8–11 components; it does not introduce a second orchestration path.

```text
USGS / GDACS / configured RSS / Mastodon
  → existing source adapters and per-source reports
  → MonitoringRunner normalization, identity, deduplication, update detection
  → Phase 3 classification → Phase 4 extraction → Phase 5 enrichment
  → Phase 6 spatial enrichment → Phase 7 grounded generation
  → Phase 9 alert evaluation
  → SQLite event/revision/intelligence/alert/run repositories
  → read-only REST API
  → API client → refreshed live dashboard
```

`LiveMonitoringSession.run_cycle()` is the canonical cycle. `ContinuousMonitoringService` adds serialized scheduling and bounded retries around that same session. `python -m monitoring.live --once` runs one cycle; `python -m monitoring.live --interval 360` schedules sequential cycles with a delay after completion.

## Sources, normalization, and lifecycle

`RealSourceConfig.from_env()` reads source enablement, endpoint/feed settings, limits, and timeouts from environment variables. `configured_sources()` creates only enabled adapters. RSS needs configured feed URLs; Mastodon uses configurable public hashtag timelines and requires no authentication. Adapters report independent success, empty, not-configured, or error states. `MultiSource` isolates retrieval failures, so one provider does not discard records from other providers. The cycle stores each adapter report, including received/retained/processed/new/duplicate/updated counts and errors.

`MonitoringRunner` normalizes adapter records and uses the existing stable identity and content fingerprints. First-seen events are NEW, unchanged polls are DUPLICATE, and material changes retain event identity and create a revision as UPDATED. Source URL, timestamps, original text, source metadata, source type/location evidence, and provenance are carried through the pipeline and persistence. Source-authoritative type resolution remains separate from Phase 3 predictions and records disagreements instead of overwriting either evidence source.

## Intelligence, alerts, and persistence

The operational pipeline invokes the existing Phase 3–7 interfaces. Structured source metadata complements NLP/ML evidence. Phase 3 exceptions are recorded per event; available source-authoritative type evidence and downstream processing continue where possible. Model dependency errors remain explicit in phase status. Phase 5 scoring, Phase 6 spatial logic, Phase 7 generation, and Phase 9 alert thresholds are unchanged by this integration work.

SQLite stores event identity and current state, material revisions, source-aware type/location evidence, intelligence, alerts and alert history, and monitoring-run lifecycle and source reports. Database writes use the existing repository and transaction boundary. API GET handlers query those repositories; they do not read demonstration artifacts or invoke inference.

## API and dashboard

The WSGI API exposes `/health`, `/healthz`, `/api/events`, `/api/events/{event_id}`, `/api/alerts`, `/api/alerts/{alert_id}`, `/api/alerts/{alert_id}/history`, `/api/monitoring/runs`, `/api/monitoring/runs/{run_id}`, and `/api/intelligence/summary`. The dashboard obtains its view from these API endpoints through `DashboardAPIClient`, then periodically reloads it. It does not query repositories directly or load demonstration JSON. Source health includes received, retained, processed, NEW/duplicate, updated, and error details from the latest persisted run.

Run `python -m api.app` and `python -m dashboard.app` in separate terminals for the interactive local services. Configure `API_HOST`, `API_PORT`, `DASHBOARD_API_BASE_URL`, and `DASHBOARD_API_TIMEOUT` as needed. The local API has no authentication and is not suitable for exposure to an untrusted network without a separate security boundary.

## Configuration and operation

Copy settings into the process environment; the application does not implicitly load `.env`. Refer to `.env.example` and `monitoring/real_sources.py` for the current source-specific names. Main settings include `USGS_ENABLED`, `GDACS_ENABLED`, `NEWS_ENABLED`, `DISASTER_RSS_FEEDS`, `DISASTER_RSS_KEYWORDS`, `DISASTER_MASTODON_ENABLED`, `DISASTER_MASTODON_BASE_URL`, `DISASTER_MASTODON_HASHTAGS`, `DISASTER_MASTODON_LIMIT`, `DISASTER_SOURCE_TIMEOUT`, `DISASTER_MAX_EVENTS_PER_SOURCE`, `DISASTER_POLL_INTERVAL`, retry settings, and `DISASTER_DB_PATH`. Do not place credentials in reports or source code.

One production-path cycle can be run with `python -m monitoring.live --once`. Continuous monitoring can be run with `python -m monitoring.live --interval 360`. The integration demonstration is `python evaluation/run_phase11_8_full_integration_demonstration.py`; it creates a temporary SQLite database, runs one cycle with configured public adapters, starts a local API server over that database, queries the API and dashboard data path, and writes `evaluation/phase11_8_full_integration_results.json`. It records source errors and zero-result feeds as observed and never inserts synthetic data. A failing provider does not get relabeled as successful.

## Validation and limitations

`tests/test_phase11_8_integration.py` exercises mocked USGS/GDACS/RSS/Mastodon source boundaries through the shared persistence, HTTP API, and dashboard path. It is deterministic integration coverage and is **not a live-source test**. Existing Phase 5 and Phases 8–11 tests remain regression coverage.

The live demonstration depends on the current host's network, source availability, and provider access policies. Source access failures are retained as failures. RSS returns no events until valid feeds are configured. Phase 3 may report model or dependency unavailability on hosts missing its configured runtime artifacts; this is surfaced in per-event phase results and is not hidden with generated labels. A live feed response does not validate geographic accuracy, completeness, or alert usefulness. The dashboard is an operational local view, not a deployed production service. Authentication, production hosting, durable server database operations, and real operational calibration remain outside this phase.

No Git commit or push is part of this implementation.
