# Phase 10 — Operational Dashboard & Visualization

> The original artifact-backed Phase 10 dashboard remains documented for schema and compatibility context. The application entry point is now Phase 11.5 and reads operational data through the Phase 11 API only. See [Phase 11.5](phase11_5_database_api_dashboard.md) for startup and current data-state behavior.

## Objective and architecture

Phase 10 provides a local, read-only operational decision-support interface over existing Phase 5–9 outputs. `dashboard/adapters.py` maps upstream schemas into view models; `data.py` combines available records; `metrics.py` summarizes existing fields; `filters.py` provides deterministic filtering and sorting; `components.py` renders the interface; `app.py` serves it on loopback using Python's standard library. The dashboard does not re-run upstream scoring, geospatial analysis, generative intelligence, event identity, or alert logic.

## Views and interactions

The interface includes situation KPIs and distributions, a filterable/sortable incident register with drill-down, alert review, a coordinate plot and unresolved-location list, monitoring event state, grounded Phase 7 outputs, and observed Phase 3–9 processing health. Analysts can inspect recommendations, evidence, uncertainty, warnings, resource estimates and alert suppression/escalation. Upstream Phase 5 priority and Phase 9 alert levels remain authoritative.

The spatial plot is a coordinate plot rather than a basemap. It includes only range-valid coordinates explicitly marked successful by Phase 6. Mentioned location, geocoded location, and verified location are distinct; the dashboard cannot independently establish geographic accuracy. Synthetic points are labeled as synthetic.

## Data and trust boundaries

The default offline demonstration reuses existing Phase 7, 8 and 9 artifacts. It is deterministic rule/algorithm and interface demonstration data, not live monitoring or validated operational data. A caller may load a JSON array of incident records or an input bundle with `phase5_records`, `phase6_results`, `phase7_artifact`, `monitoring_artifact`, and `alert_artifact`.

Phase 5 numeric scores and levels are displayed as received. Confidence is evidence quality, not calibrated probability. Resource estimates retain their heuristic label. Phase 7 narrative is grounded in structured evidence but is not guaranteed hallucination-free; recommendations are for human review, not autonomous commands. Mock notification status does not mean an external party was reached. No autonomous dispatch, forecasting, real-time GIS, or live notification delivery is provided.

Phase 8 event summaries describe ingestion state separately from incident intelligence. Phase 9 history is limited to the currently available in-memory operational state; the dashboard does not invent missing timeline events. Pipeline health is inferred only from statuses present in loaded records; an unobserved phase is unavailable. Recorded Phase 3 failures remain visible, including the known local NumPy/Python C-extension compatibility problem.

Credential-like fields are removed by the adapter before records enter dashboard view data. The server binds to `127.0.0.1` by default and is intended for local review. It is not an authenticated or production deployment; Phase 11 owns persistence, API integration, access control, and deployment.

## Configuration and commands

No dashboard dependency is added. From the repository root:

```powershell
python -m dashboard.app --demo
```

Open `http://127.0.0.1:8765/`. To load supplied JSON:

```powershell
python -m dashboard.app --input path\to\dashboard-input.json --port 8765
```

`DashboardConfig` defines the loopback host, port, repository root, dataset mode, and optional input path. The demonstration artifact is generated with:

```powershell
python evaluation/run_phase10_demonstration.py
```

The local status endpoint is `/healthz`. Do not expose this unauthenticated development server to an untrusted network.

For manual verification from the repository root:

```powershell
python -m pytest tests/test_phase10_dashboard.py -v
python -m pytest tests/test_phase5_intelligence.py tests/test_phase6_gis.py tests/test_phase7_llm.py tests/test_phase8_monitoring.py tests/test_phase9_alerting.py -v
python evaluation/run_phase10_demonstration.py
python -m dashboard.app --demo
```

Confirm the browser labels the fixture `DEMONSTRATION / SYNTHETIC DATA`, shows available incident and alert views, keeps unresolved reports off the point plot, and reports Phase 3 failures from upstream status when present. Stop the local server with Ctrl+C.

## Testing and limitations

`pytest tests/test_phase10_dashboard.py -v` exercises adapters, preservation, sanitization, aggregation, filters, empty/malformed inputs, determinism, JSON serialization, and HTTP rendering. Phase 5–9 regression suites should also be run. The UI is a dependency-free local interface with a schematic coordinate plot, not a GIS basemap. Available history and health depend on upstream artifacts. It does not persist state, poll sources, send notifications, or offer access control.

## Integration path

Phase 7 can consume the same Phase 5/6 records independently; the dashboard displays the grounded summaries and their uncertainty without overriding deterministic fields. Phase 8 can later pass monitoring records through the adapter/data boundary. A future Phase 11 API/database integration can supply the same view-model inputs without moving business logic into the UI. Phase 10 itself has no live-ingestion or real-time guarantee.
