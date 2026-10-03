# Phase 9 — Real-Time Alerting & Early Warning

## Objective and boundaries

Phase 9 evaluates Phase 8 operational events and their structured Phase 5/6 intelligence to decide whether an operational alert should be created. It adds deterministic rule explanations, stable alert IDs, repeat suppression, update-based escalation, in-memory state, and a notification adapter contract with an offline mock. It is decision support; it does not predict disasters scientifically, dispatch responders, or deliver real notifications.

## Architecture and contract

`alerting.evaluate_alert` accepts one Phase 8 monitoring result, or a direct Phase 5/6 incident. `evaluate_alerts` shares a stateful evaluator over an ordered batch. For repeated single-record calls, reuse one `AlertEvaluator` instance (or shared `AlertStateStore`) so suppression and escalation history persist during that process. `AlertEvaluator` unwraps `intelligence.phase5`, `phase6`, and `phase7`, applies structured rules, compares prior incident state, creates an `AlertDecision`, optionally creates an `AlertRecord`, and optionally invokes a provider. Existing Phase 8 fields are retained at the top level; normalized `incident_intelligence`, `spatial_context`, and `grounded_intelligence` are added separately from alert and notification status.

The Phase 8 operational result uses `event_id`, `event_state`, `provenance`, and `intelligence` fields. Phase 9 accepts those directly. It also accepts direct enriched incidents for unit tests and later integrations. Phase 5 `incident_id` is retained; Phase 9 `alert_id` is a separate identity.

## Levels and rules

Alert levels are `INFO`, `LOW`, `MEDIUM`, `HIGH`, and `CRITICAL`. No-alert records remain `LOW` when scores are present below configured trigger bands, and `INFO` when scores are unavailable. `priority_level` is never substituted for `alert_level`.

Default priority thresholds are read from the Phase 5 config objects: MEDIUM 25, HIGH 50, CRITICAL 75. Severity and urgency use their configured HIGH 50 and CRITICAL 75 thresholds. Urgent resource evidence uses the Phase 5 urgency floor (30); area spatial-priority evidence uses the Phase 5 HIGH priority threshold (50). The rescue flag maps to HIGH, the critical-priority flag maps to CRITICAL, and same-level material escalation defaults to a score delta of 10 points. These are configurable in `AlertConfig`; they are operational alert thresholds over Phase 5 engineering scores, not probabilities.

Rules inspect priority, urgency, severity, `IMMEDIATE_RESCUE`, `CRITICAL_PRIORITY`, explicit urgent resource evidence, and Phase 6 hotspot/spatial-priority evidence. Each rule returns a stable name, triggered boolean, and human-readable explanation. If multiple rules trigger, the highest candidate alert level wins; no opaque weighted alert score is introduced. Alert type identifies the leading rule category.

## Identity, deduplication, cooldown, and escalation

Alert IDs are SHA-256-derived from the preserved incident ID (or event ID when needed), alert level/type, and a canonical structured condition. Timestamps do not participate. Repeated equivalent conditions therefore return the same logical ID.

The in-memory state store retains the last structured observation, active alert level, alert history, and notification timestamp. Repeated unchanged conditions are returned as `SUPPRESSED`, with a visible reason and cooldown metadata; no second notification is attempted. This is the default. If unchanged-repeat suppression is explicitly disabled, cooldown still suppresses repeats during its interval, then permits another attempt. A Phase 8 `DUPLICATE` is explicitly suppressed. A changed Phase 8 event does not itself fire an alert: it must cross an alert rule or materially worsen.

The update-based early-warning comparison detects a higher alert level, a priority or urgency increase meeting the configured 10-point delta, newly appearing immediate-rescue evidence, or newly significant hotspot evidence. HIGH to CRITICAL creates an `ESCALATED` alert; an unchanged CRITICAL condition is suppressed. Decreasing conditions are represented with `deescalated=true`; resolution can be passed as `resolved=true` or `incident_status=RESOLVED/CLOSED`. This is operational escalation from observed structured updates, not scientifically validated disaster forecasting.

## State and notification handling

`AlertStateStore` is process-local and replaceable. State/history disappears at process exit; persistence belongs to Phase 11. A future API/dashboard can consume decision, record, suppression, escalation, and notification fields.

`NotificationProvider.notify(alert)` is the adapter contract. `MockNotificationProvider` records deterministic offline attempts and can simulate failure. Notification failures are attached to the alert while preserving the alert record. There are no email, webhook, SMS, or messaging integrations, credentials, or network calls. A successful mock means only that the test adapter accepted the attempt; no authority was notified and no response team was dispatched.

## Phase 5–7 preservation and uncertainty

Alert decisions use structured Phase 5 scores, flags, and requests. Phase 5 confidence remains evidence quality, not calibrated probability. Phase 6 coordinates are returned only when geocoding status is `success` and latitude/longitude pass range checks; otherwise both are null. An alert may still fire from non-spatial evidence when location is unresolved. Phase 7 grounded context is carried as a separate field and never decides whether an alert fires. Confidence and location uncertainty are surfaced in each decision/record.

## Demonstration and tests

Run the eight offline synthetic scenarios with:

```powershell
python evaluation\run_phase9_demonstration.py
```

The artifact is written to `evaluation/phase9_demonstration_results.json`. Its coordinates are synthetic fixture values only and do not validate geographic accuracy.

Run Phase 9 tests with:

```powershell
pytest tests\test_phase9_alerting.py -v
```

Regression tests cover Phases 5–8. No new package dependency is required.

## Limitations and future integration

- State and alert history are in memory, not durable or multi-process safe; Phase 11 should provide persistence and APIs.
- Mock notification attempts are not delivery to real people or services.
- No live source, dashboard, queue, web API, authentication, or production deployment is added.
- Rules are deterministic decision-support heuristics, not validated forecasting or calibrated risk probabilities.
- No autonomous dispatch or confirmation of external action occurs.
- SLA, load, resilience, and real-world alert-quality evaluation belong to Phase 12.

Phase 10 can render operational alerts, active/escalated/suppressed state, provenance, spatial context, and uncertainty. Phase 11 can persist state and expose controlled integrations. Phase 9 intentionally does not implement either phase.
