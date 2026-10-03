# Phase 11.5 — Database/API-Driven Operational Dashboard

## Objective and architecture

Phase 11.5 makes the local operations dashboard a read-only consumer of persisted Phase 11 data. `dashboard/api_client.py` performs bounded JSON HTTP requests; `dashboard/live_data.py` validates API payloads and adapts persisted Phase 3–9 event, intelligence, spatial, alert, and monitoring structures for display; `dashboard/live_view.py` renders the responsive operations interface; and `dashboard/app.py` serves the page and same-origin API proxy routes. The dashboard does not import database repositories, issue SQL, rescore incidents, geocode locations, or create alerts.

```text
Phase 8/9 pipeline → Phase 11 SQLite persistence → Phase 11 API
                                               → dashboard adapter → browser
```

The runtime entry point never loads earlier demonstration artifacts or synthetic fixtures. `make_handler(dataset)` remains available for compatibility tests; `python -m dashboard.app` uses only the API-backed handler.

## Dashboard interface

The Operations Overview contains API/database/monitoring status, persisted event and alert KPIs, a country-outline map, alert feed, server-filtered event table, event detail drawer, intelligence distributions, source/run statistics, and health information. Country boundaries come from the bundled Natural Earth 1:110m public-domain country dataset ([dataset details](https://www.naturalearthdata.com/downloads/110m-cultural-vectors/)) and are served locally, so rendering does not depend on a live tile service. Incident markers are placed only for persisted records with successful geocoding status and valid WGS84 coordinate ranges. Unresolved or invalid coordinates remain listed as unresolved and never receive invented locations. The map is a geographic context view, not a claim of coordinate or geocoding accuracy.

The dashboard presents operational capabilities and evidence without exposing internal project phase numbering in its user-facing labels. Internal API field names remain unchanged.

The alert feed uses persisted active Phase 9 alert records, ordered by alert level, priority evidence, and time. It preserves escalation, trigger reasons, source, and linked event data where available. The event table uses API filters and bounded 50-record pages; alert-level filtering is applied to the corresponding bounded page after joining persisted alerts. Quick search is explicitly limited to the loaded page.

## API and data flow

The dashboard API client consumes:

- `GET /health` for API and SQLite availability.
- `GET /api/events` for bounded event pages and server-side filters.
- `GET /api/alerts` for active alerts and critical-alert totals.
- `GET /api/monitoring/runs` for latest run and source reports.
- `GET /api/intelligence/summary` for persisted aggregate distributions.
- `GET /api/events/{event_id}` for event detail.

The browser requests `/dashboard-data` and `/dashboard-event/{event_id}` from the dashboard server; those routes proxy and adapt Phase 11 API data. `/healthz` reports dashboard/API/database readiness. API credentials and database paths are not sent to the browser. The API itself currently has no authentication and is intended for local development only.

## Configuration and startup

Settings are read from process environment (the project does not automatically load `.env`). The example values are in `.env.example`:

| Setting | Default | Purpose |
| --- | --- | --- |
| `API_HOST` | `127.0.0.1` | API bind address |
| `API_PORT` | `8770` | API port |
| `DISASTER_DB_PATH` | ignored local SQLite path | Persisted operational database |
| `DASHBOARD_HOST` | `127.0.0.1` | Dashboard bind address |
| `DASHBOARD_PORT` | `8765` | Dashboard port |
| `DASHBOARD_API_BASE_URL` | `http://127.0.0.1:<API_PORT>` | API origin read by dashboard server |
| `DASHBOARD_API_TIMEOUT` | `3` seconds | Per-request timeout (0.1–30 seconds) |

From PowerShell, in separate terminals at repository root:

```powershell
python -m api.app
python -m dashboard.app
```

Then open `http://127.0.0.1:8765/`. A one-shot ingestion cycle can be started separately with `python -m monitoring.real_runner --once`; recurring polling remains a Phase 8/11 operation. Do not expose the unauthenticated local API to an untrusted network.

## Data states and failure behavior

- `LIVE DATA` is shown only for a successful or partial-success monitoring run within 15 minutes, with source records received and non-synthetic event data.
- `DATABASE DATA` means persisted records are available but a recent successful source ingestion is not evidenced.
- `DEMO DATA` is used if all returned records are marked synthetic/demo; synthetic records are not called live.
- `NO OPERATIONAL DATA AVAILABLE` is shown for a healthy API connected to an empty database, with instructions to start monitoring/ingestion.
- `API OFFLINE` is shown when the health/API boundary cannot confirm service and database connectivity. There is no fallback to demonstration data.
- Partial resource failures are reported per component; panels with available data remain usable.

The UI uses bounded polling (30 seconds, 60 seconds, or 5 minutes; default 30 seconds) and retains filter selections when refreshing. Summary KPIs come from persisted API aggregate/query results, not fabricated comparisons. The event count is labeled as persisted events because a general active/closed incident-resolution lifecycle is not stored by the current API schema.

## Demonstration and testing

Run `python evaluation/run_phase11_5_dashboard_demonstration.py` to create an isolated temporary SQLite database, serve the actual Phase 11 API and dashboard handlers locally, and exercise filters, detail, valid/unresolved locations, monitoring and summaries, API-offline state, and an empty database. The generated `evaluation/phase11_5_dashboard_results.json` is explicitly synthetic test evidence and does not evaluate geographic accuracy or claim live data.

Run the Phase 11 database, API, integration, and Phase 11.5 dashboard suites, followed by Phase 5–10 regression suites. The Phase 11.5 dashboard tests use temporary databases and local mocked/test API servers; they do not require network access to a geocoder or public source.

## Limitations and later integration

This is a local read-only dashboard, not an authenticated or production deployment. Polling is bounded and uses full summary/event-page refreshes; it does not provide WebSockets, continuous ingestion, automatic external notifications, or incident resolution workflows. The country outlines provide geographic context only and do not establish real-world coordinate or geocoding accuracy. Detail fields are only shown when persisted upstream evidence exists. Authentication, role-based access, deployment hardening, social feeds, and final end-to-end production validation are intentionally deferred. The API boundary allows ingestion and future dashboard workflows to populate updated records without reimplementing the dashboard data model.
