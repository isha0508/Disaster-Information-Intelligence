# Disaster Information Intelligence and Decision Support System

A local, multi-source system that turns public disaster reports into structured, explainable incident intelligence. It combines source metadata with NLP extraction, deterministic prioritization, conservative geospatial processing, alerts, SQLite persistence, a REST API, and an API-backed operations dashboard.

This is a local decision-support project, not an emergency dispatch service or a validated forecasting system. Social posts and model outputs require human review.

## Problem and objective

Disaster information is distributed across structured feeds, news, and public social posts. This project provides one pipeline to normalize those reports, preserve their provenance, extract operational evidence, prioritize incidents, and make persisted results reviewable through an API and dashboard.

## Capabilities

- Public USGS earthquake GeoJSON, GDACS event data, configurable RSS/Atom feeds, and optional public Mastodon hashtag timelines.
- Stable event identity, normalization, source-aware deduplication/update tracking, and isolated per-source retrieval.
- Phase 3 disaster classification with the canonical V2 checkpoint and four operational classes: `earthquake`, `fire`, `flood`, and `hurricane`. Structured source type evidence is retained and takes precedence where authoritative; the model is an additional signal.
- Phase 4 hybrid entity extraction for locations, casualties, displacement, requests, resources, rescue, disaster type, organizations, people, numbers, and infrastructure. Transformer NER is optional at runtime; rule and gazetteer extraction remains available when it is missing.
- Phase 5 deterministic severity, urgency, priority, confidence, resource estimates, flags, and incident grouping. Scores are interpretable heuristics, not calibrated probabilities.
- Phase 6 coordinate validation, source-coordinate precedence, geocoding, spatial summaries, and map-ready points. Evidence text remains separate from verified coordinates. No coordinate is invented; offset reports are not plotted at their reference town.
- Phase 7 grounded narrative generation. Monitoring currently uses the deterministic mock provider; a separate OpenAI-compatible provider is available for direct configuration.
- Phase 9 explainable alert rules, persistence, repeat suppression, and escalation state. Notification delivery is not configured as a production external channel.
- Phase 11 SQLite persistence, local REST API, monitoring-run lifecycle, and API-backed dashboard with source filters and refresh status.

## Architecture and pipeline

```text
USGS ─┐
GDACS ├─> adapters -> normalize/identify -> Phase 3 classification
RSS ──┤                                  -> Phase 4 extraction
Mastodon ┘                               -> Phase 5 deterministic intelligence
                                         -> Phase 6 spatial processing
                                         -> Phase 7 grounded narrative
                                         -> Phase 8 monitoring lifecycle
                                         -> Phase 9 alert evaluation
                                         -> SQLite -> REST API -> dashboard
```

Source adapter failures are reported independently so that one unavailable source does not prevent other configured adapters from being attempted. Source-level retries and the continuous polling interval are configurable.

## Implementation phases

| Phase | Implemented scope |
|---|---|
| 1–2 | Repository foundation, HumAID dataset utilities, text preprocessing, and exploration. |
| 3 | Relevance, humanitarian-category experiments, and the canonical V2 disaster-type classifier. |
| 4 | Hybrid NER and structured disaster evidence extraction. |
| 5 | Deterministic incident intelligence and decision support. |
| 6 | Geospatial normalization, configurable geocoding, spatial analysis, and coordinate provenance. |
| 7 | Grounded narrative provider interface, deterministic mock, and optional compatible HTTP provider. |
| 8 | Event normalization, identity/lifecycle, and public USGS/GDACS/RSS adapters. |
| 9 | Deterministic alert evaluation and alert history. |
| 10 | Dashboard view and visualization foundation. |
| 11 | SQLite persistence, REST API, live monitoring, Mastodon integration, end-to-end integration, and local deployment guidance. |

See [`docs/`](docs/) for phase-specific implementation and deployment notes. Phase 12 production-readiness evaluation is not complete.

## Location and geocoding behavior

Authoritative structured-source coordinate pairs are retained ahead of NLP-derived locations. When source coordinates are absent, Phase 6 may use the configured geocoder. Nominatim is the default provider; lookup is asynchronous, cached for the process lifetime, and limited to at most four requests per minute with an identifying User-Agent and timeout. A configured offline gazetteer can be used as fallback. Country and administrative-area matches are not treated as incident points, and distance/direction descriptions such as “20 km NW of Kozan” do not use Kozan as the epicenter.

When lookup is unavailable, broad, or ambiguous, extracted location evidence remains visible, coordinates remain unavailable, and the incident is not plotted. Online place resolution requires network access to the configured geocoder. Follow the [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/) when using its public service; a compatible provider URL can be configured with `DISASTER_GEOCODER_URL`.

## Persistence, API, and dashboard

SQLite defaults to `data/database/disaster_intelligence.db`; `DISASTER_DB_PATH` can override it. Relative paths are resolved from the repository root. The API and dashboard default to loopback ports 8770 and 8765 respectively. The dashboard reads persisted data only through the API. It distinguishes API failure from a successful empty response and retains the last good snapshot while showing a degraded state if a refresh fails.

Read endpoints include:

- `GET /health` and `GET /healthz`
- `GET /api/events` (supports filtering and pagination)
- `GET /api/intelligence/summary`
- `GET /api/alerts`
- `GET /api/monitoring/runs`
- Dashboard UI at `http://127.0.0.1:8765/`

The API has no authentication. Keep both services on loopback unless a separately reviewed security design is in place.

## Technology

- Python 3.10 (validated runtime: 3.10.9)
- SQLite and Python standard-library WSGI/HTTP modules for persistence and REST services
- NumPy, pandas, SciPy, scikit-learn, PyTorch, and Hugging Face Transformers for data/model workflows
- HTML, CSS, and JavaScript for the local dashboard
- Optional Nominatim-compatible geocoding over HTTP
- pytest for automated tests

`requirements.txt` is a partial/general dependency list, not a complete environment lock. The validated model runtime also uses NumPy 1.26.4, PyTorch 2.4.1+cu124, and Transformers 4.44.2. Follow [`docs/phase11_9_deployment.md`](docs/phase11_9_deployment.md) for environment setup; do not indiscriminately reinstall dependencies into the validated environment. The local Phase 3 V2 model checkpoint is not committed and must be supplied locally for model inference.

## Repository layout

```text
api/             Local REST API and routes
alerting/        Deterministic alert evaluation
dashboard/       API client, UI, filtering, rendering
database/        SQLite schema, repositories, serialization
data/            Runtime DB and local datasets (generated/ignored)
docs/            Phase and deployment documentation
evaluation/       Demonstrations, evaluation utilities, reports
gis/             Geocoding, spatial processing, schemas
intelligence/    Scoring, clustering, incident/resource logic
llm/             Grounding, providers, reports, configuration
models/          Prediction interfaces (checkpoint weights local)
monitoring/       Source adapters, normalization, orchestration
nlp/             Hybrid entity extraction
notebooks/       Research and experiment notebooks/artifacts
preprocessing/   Dataset and text-cleaning utilities
scripts/         Environment/configuration helpers
tests/           Unit and integration tests
training/        Model-training and evaluation code
```

## Setup and configuration

Use Windows PowerShell from the repository root. The existing validated `.venv` is expected for the commands below. New environments need a compatible Python 3.10 runtime, the machine-appropriate PyTorch wheel, and the project dependencies; see the deployment guide.

Create a local environment file once, edit it as needed, and import it separately in each service terminal. `.env` is not automatically loaded.

```powershell
Copy-Item .env.example .env
notepad .env
. .\scripts\import_project_env.ps1
Import-ProjectEnv
```

The example enables USGS, GDACS, RSS, and Mastodon. Set `DISASTER_RSS_FEEDS` to an empty value or `NEWS_ENABLED=false` to disable RSS, and use the corresponding `*_ENABLED` variables to configure other sources. Public source feeds need no credentials. Keep API/dashboard hosts on `127.0.0.1` by default. See [`.env.example`](.env.example) for supported settings, including database, source, polling, geocoder, and service configuration.

## Start the system

Run each service in its own PowerShell terminal from the repository root. In each terminal, activate the environment and import the local configuration:

```powershell
.\.venv\Scripts\Activate.ps1
. .\scripts\import_project_env.ps1
Import-ProjectEnv
```

### Terminal 1 — API

```powershell
python -m api.app
```

### Terminal 2 — continuous monitoring

```powershell
python -m monitoring.live --interval 360
```

This performs real configured source retrieval and persists results. It waits 360 seconds after each completed cycle; the minimum supported interval is 60 seconds. For one intentional live ingestion cycle, run `python -m monitoring.live --once` instead. Avoid starting extra monitor instances against the same database.

### Terminal 3 — dashboard

```powershell
python -m dashboard.app
```

Open `http://127.0.0.1:8765/`. Source filtering affects the dashboard query only; it does not change source ingestion. Stop services with Ctrl+C in their respective terminals.

## Health checks

With API and dashboard running, use a fourth PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8770/health
Invoke-RestMethod http://127.0.0.1:8770/api/events
Invoke-RestMethod http://127.0.0.1:8770/api/intelligence/summary
Invoke-RestMethod http://127.0.0.1:8770/api/alerts
Invoke-WebRequest http://127.0.0.1:8765/healthz
```

The database schema is initialized by API/repository startup. For explicit idempotent initialization without deleting records, run `python -m database.schema` (optionally pass `--db-path`).

## Tests and demonstrations

Run the full suite with an unused workspace-local pytest temporary directory:

```powershell
python -m pytest -q --basetemp=tmp\pytest_final
```

If that path already exists, choose another unused name under `tmp\`. The suite uses temporary databases for persistence tests. Demonstration scripts are in `evaluation/`; Phase 11.9’s `python evaluation\run_phase11_9_deployment_smoke.py` uses mocked source input and a temporary database, so it does not validate public-feed availability or write live events to the operational database. `python evaluation\run_phase11_8_full_integration_demonstration.py` exercises configured public sources and should be treated as live ingestion.

## Example workflow

1. Start the API, monitoring service, and dashboard in separate terminals.
2. Confirm the API health response and source status in monitoring-run reports.
3. View persisted events in the dashboard, review extracted evidence and provenance, and use source/type filters as needed.
4. Treat unresolved location evidence as text only. Use map points only where the record has valid source coordinates or a successful eligible geocoding result.
5. Review alert decisions and model-derived fields as decision support, not as autonomous instructions.

## Limitations and future work

- Public provider availability, network access, rate limits, RSS configuration, and local model artifacts affect source/model coverage.
- Online place resolution needs network access; ambiguous or broad evidence remains unresolved and is not plotted.
- Severity/urgency rules and classification/extraction outputs are not a substitute for expert validation. Reported confidence is not necessarily a calibrated probability.
- The monitoring Phase 7 path currently uses a grounded deterministic mock; external LLM use requires explicit separate configuration and is not enabled by setting `.env` alone.
- Local SQLite, unauthenticated API, no managed deployment, backups, retention policy, or production security controls are provided by this repository.
- Future work may evaluate model performance on reviewed annotations, improve provider observability, and complete security/backup/readiness reviews without weakening evidence provenance.
