# Phase 11.9 — Local Deployment & Operationalization

## Scope and status

Phase 11.9 documents and verifies a repeatable **local Windows deployment** of the existing system. It does not claim cloud or enterprise production readiness. Phase 12 remains a separate future end-to-end evaluation and production-readiness assessment.

This phase adds no alternative data path: monitoring owns ingestion and persistence, SQLite repositories own stored state, the REST API reads through those repositories, and the dashboard reads only through the API. Phase 3 V2 remains the configured production classifier. The Phase 7 provider used by the monitoring pipeline remains its grounded mock provider.

## Architecture

```text
USGS / GDACS / RSS / public Mastodon
               ↓
       monitoring.live
               ↓
        Existing Phase 3–9 pipeline
               ↓
       SQLite repositories
               ↓
          REST API :8770
               ↓
      Dashboard :8765 (API client)
```

Run API, monitoring, and dashboard as separate processes. This keeps logs, failures, and shutdown clear and avoids changing the existing component lifecycle.

## Prerequisites and validated runtime

- Windows PowerShell and the repository's `.venv`.
- Validated runtime: Python 3.10.9, NumPy 1.26.4, PyTorch 2.4.1+cu124, Transformers 4.44.2.
- The local canonical Phase 3 V2 checkpoint must be present at `models/phase_3_v2/best_checkpoint/`; the application does not fall back to a different classifier if it is missing.
- Phase 4 may use the cached `dslim/bert-base-NER` model; if unavailable it retains its existing rule-based fallback and records the result through the existing pipeline.

`requirements.txt` is a partial/general dependency list, not a complete environment lock. It pins the compatible NumPy version but intentionally does not select a PyTorch CUDA wheel. Do not run an indiscriminate requirements install over the validated environment. Create a new environment when reproducibility is needed, install the matching CUDA-specific PyTorch wheel using the official PyTorch installation instructions for that machine, then install the remaining project requirements and the pinned Transformers version. Verify all four versions above before running. The existing `.venv` is not modified by the deployment procedure.

## Environment configuration

The application reads process environment variables; it does **not** automatically load `.env`. Copy `.env.example` to `.env`, edit local values, and import it separately in each PowerShell process:

```powershell
Copy-Item .env.example .env
notepad .env
. .\scripts\import_project_env.ps1
Import-ProjectEnv
```

The helper sets variables in the current PowerShell process, prints no values, and performs no Git or process operations. `.env` is ignored by Git. Keep secrets out of `.env.example`, logs, screenshots, and source control. The public feeds do not require credentials. `LLM_API_KEY` is optional for direct Phase 7 provider use; monitoring explicitly configures the mock provider today, so setting remote-provider variables does not change its Phase 7 behavior.

Deployment-relevant variables are documented in `.env.example`:

| Group | Variables |
|---|---|
| Database | `DISASTER_DB_PATH` |
| API | `API_HOST`, `API_PORT`, `API_LOG_LEVEL` |
| Dashboard | `DASHBOARD_HOST`, `DASHBOARD_PORT`, `DASHBOARD_API_BASE_URL`, `DASHBOARD_API_TIMEOUT` |
| Sources | `USGS_ENABLED`, `USGS_FEED_URL`, `GDACS_ENABLED`, `GDACS_FEED_URL`, `GDACS_EVENT_TYPES`, `GDACS_DAYS`, `NEWS_ENABLED`, `DISASTER_RSS_FEEDS`, RSS filter settings, `DISASTER_MASTODON_*` |
| Polling and HTTP | `DISASTER_POLL_INTERVAL`, `DISASTER_MAX_EVENTS_PER_SOURCE`, `DISASTER_SOURCE_TIMEOUT`, `DISASTER_SOURCE_USER_AGENT`, `DISASTER_SOURCE_RETRY_COUNT`, `DISASTER_SOURCE_RETRY_BACKOFF`, `DISASTER_MONITORING_LOG_LEVEL` |
| Phase 6 geocoding | `DISASTER_GEOCODER_PROVIDER`, `DISASTER_GEOCODER_URL`, `DISASTER_GEOCODER_USER_AGENT`, `DISASTER_GEOCODER_TIMEOUT`, `DISASTER_GEOCODER_MIN_INTERVAL`, `DISASTER_GEOCODER_GAZETTEER_PATH` |
| Optional direct Phase 7 provider | `LLM_PROVIDER`, `LLM_MODEL`, `LLM_MAX_CONTEXT_CHARS`, `LLM_TEMPERATURE`, `LLM_MAX_OUTPUT_TOKENS`, grounding/recommendation/spatial toggles, `LLM_API_BASE_URL`, `LLM_API_KEY`, `LLM_TIMEOUT_SECONDS` |

Phase 6 uses the built-in Nominatim adapter by default; set
`DISASTER_GEOCODER_PROVIDER=offline` to use only the offline gazetteer. The
adapter sends an identifying application User-Agent. Lookups run asynchronously on one worker,
cache results for the process lifetime, and wait at least 15 seconds between
outbound requests. Do not run multiple copies against the public endpoint.
Country and administrative-area centroids are rejected, as are
distance-and-direction descriptions whose reference town is not the
epicenter. Unresolved evidence remains visible without map coordinates. See
the current [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)
before enabling it; a self-hosted or other compatible service can be selected
through `DISASTER_GEOCODER_URL`.

Bind local services to loopback (`127.0.0.1`) unless there is a separately reviewed network security design. The API has no authentication.

## Database initialization and preservation

From the repository root, with the project venv active and variables imported:

```powershell
python -m database.schema --db-path $env:DISASTER_DB_PATH
```

If `DISASTER_DB_PATH` is intentionally unset, omit `--db-path`; the default is `data/database/disaster_intelligence.db`. Initialization creates the database and schema when missing and applies the existing idempotent migrations when present. It does not reset or delete operational rows. API startup also creates/initializes the repository schema, so explicit initialization provides an observable preparation step rather than a destructive reset.

## Start the local stack

Activate and load configuration in **each** terminal. Keep one PowerShell window per service.

```powershell
Set-Location C:\Users\Isha\Downloads\Disaster-Information-Intelligence
.\.venv\Scripts\Activate.ps1
. .\scripts\import_project_env.ps1
Import-ProjectEnv
```

### Terminal 1 — API

```powershell
python -m api.app
```

Default: `http://127.0.0.1:8770`.

### Terminal 2 — continuous monitoring

```powershell
python -m monitoring.live --interval 360
```

This uses configured public adapters and the existing source-level bounded retry policy. The minimum interval is 60 seconds. To run just one genuine configured source cycle instead, use `python -m monitoring.live --once`. One-cycle mode exits after completion; continuous mode waits after each cycle. Monitoring stores run lifecycle and events in SQLite.

### Terminal 3 — dashboard

```powershell
python -m dashboard.app
```

Default: `http://127.0.0.1:8765/`. The dashboard uses the configured API URL and never opens SQLite directly.

## Health and availability checks

```powershell
Invoke-RestMethod http://127.0.0.1:8770/health
Invoke-RestMethod http://127.0.0.1:8770/healthz
Invoke-WebRequest http://127.0.0.1:8765/healthz
```

The API health endpoints return HTTP 200 with database `ok` when repository connectivity succeeds, and HTTP 503 on repository failure. The dashboard `/healthz` verifies its API and database dependency and returns HTTP 503 when unavailable. `GET /dashboard-data` reports `API OFFLINE` and an empty event list during API outages; it does not load demo fixtures. A healthy but empty database is reported as no operational data.

The dashboard client accepts bounded API JSON responses up to 12 MB. Full persisted event pages can be larger than small health/summary responses because they include Phase 3–7 evidence. Responses above the cap are still rejected and reported as partial API data.

The deterministic deployment smoke test exercises environment-independent database initialization, repository health, one mocked-source monitoring cycle, persistence, real local API HTTP routes, dashboard/API connectivity, offline dashboard state, and idempotent reinitialization. Run it after activation:

```powershell
python evaluation\run_phase11_9_deployment_smoke.py
```

Its event is explicitly marked synthetic and displayed as `DEMO DATA`. It is a deterministic integration check and **is not evidence of live public-source retrieval**. It uses a temporary database and removes it after completion.

For regression coverage, use a fresh workspace-local `--basetemp` path on each invocation (the example below assumes the directory does not already exist):

```powershell
python -m pytest -q --basetemp=tmp\pytest_phase11_9_regression `
  tests\test_phase8_monitoring.py tests\test_phase8_real_sources.py `
  tests\test_phase9_alerting.py tests\test_phase10_dashboard.py `
  tests\test_phase11_database.py tests\test_phase11_api.py `
  tests\test_phase11_integration.py tests\test_phase11_5_dashboard.py `
  tests\test_phase11_6_monitoring.py tests\test_phase11_7_mastodon.py `
  tests\test_phase11_8_integration.py tests\test_source_aware_evidence.py `
  tests\test_phase3_v2_production_compatibility.py
```

The full repository suite can be run with `python -m pytest -q --basetemp=tmp\pytest_phase11_9_full` (choose a fresh path if it already exists).

## Shutdown and restart

Press **Ctrl+C** in each service terminal. Monitoring handles SIGINT/SIGTERM by requesting stop and finishes the current cycle before exiting. API and dashboard close their HTTP servers on Ctrl+C. Avoid force-killing processes during database writes. Restart only the affected process using the same terminal setup and environment steps; never delete the database to recover a stopped process.

## Live-source verification (separate from smoke testing)

To verify external connectivity, first start API and dashboard as above, then run:

```powershell
python -m monitoring.live --once
```

Inspect the printed cycle/source reports, persisted monitoring run, API events, and dashboard. Label evidence explicitly as **LIVE EXTERNAL DATA** only when a real configured adapter reports a successful response and real source records are retained. The deterministic smoke fixture is **MOCKED / SYNTHETIC TEST DATA** and must never be reported as live. A source may fail independently: inspect its own status and error while other sources continue. Mastodon HTTP 403, 429, or provider/network errors remain failures/limitations, never successful ingestion. This procedure is optional and requires network access to the public providers; this documentation/smoke-test phase does not claim it was performed.

## Failure and recovery

| Failure | Expected behavior and recovery |
|---|---|
| API unavailable | Dashboard reports API offline and does not substitute demo/live records. Start/restart `python -m api.app`; check its terminal and configured port. |
| Database unavailable | API health reports 503; monitoring records persistence errors where possible. Check that `DISASTER_DB_PATH` points to a writable local path and that the database file is not locked by another process. Do not delete/reset the DB. |
| One source unavailable | Its source report records failure; transient network/408/425/429/5xx failures receive bounded configured retries. Other adapters remain isolated. Check source URL/network and retry settings. |
| Mastodon 403 or rate limit | Keep that source marked error/limited. Do not treat it as successful, fabricate posts, or use an unauthorized proxy/bypass. Recheck only the provider-supported endpoint/access policy and retry later within configured limits. |
| Phase 3 V2 checkpoint unavailable | The pipeline records model unavailability; it does not silently select V1 or another checkpoint. Restore the expected local checkpoint/runtime before relying on model outputs. |
| Monitoring stopped | Restart the monitoring command after loading the same environment. Existing database events and monitoring runs remain; do not initialize via a delete/reset operation. |
| Dashboard API target incorrect | Verify `DASHBOARD_API_BASE_URL` and `API_PORT` agree, import configuration in the dashboard process, then restart only the dashboard. |

## Security and operational limitations

- This is a local operational deployment, not a hardened internet-facing service or cloud deployment.
- API has no authentication; keep API/dashboard on loopback and do not expose ports publicly.
- SQLite is a single-host local persistence choice; backups, retention, multi-user concurrency, and disaster recovery policy remain operator responsibilities.
- Provider availability, rate limits, source coverage, and model cache/checkpoint availability are external dependencies.
- The system is operational decision support; its model outputs are not scientifically validated disaster forecasts or autonomous response instructions.
- No system service, Windows service, container, or process manager is mandatory. Separate terminals are chosen for transparent logs and graceful shutdown.

## Phase boundary

Phase 11.9 establishes local startup, health checking, graceful process operation, configuration guidance, failure recovery, and a deterministic smoke path. **Phase 12 — End-to-End Evaluation & Production Readiness — remains a separate future phase and is not complete.** Cloud deployment, security hardening, backup/restore validation, operational SLOs, and full end-to-end readiness claims belong to that later review.
