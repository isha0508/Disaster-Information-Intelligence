"""Load upstream outputs and assemble read-only Phase 10 dashboard data."""

import json
from pathlib import Path

from dashboard.adapters import (adapt_incident, flatten_monitoring_artifact,
                                flatten_phase7_artifact, flatten_phase9_artifact, json_safe)
from dashboard.metrics import (dashboard_summary, intelligence_summary,
                               monitoring_summary, spatial_summary, system_health)


def build_dashboard_data(*, phase5_records=None, phase6_results=None, phase7_artifact=None,
                         monitoring_artifact=None, alert_artifact=None, dataset_mode=None):
    """Create incident, alert, monitoring, summary, and health view models.

    Inputs may be individual records or Phase 7/8/9 demonstration artifact shapes.
    The input objects are copied and never modified.
    """
    phase7_records = flatten_phase7_artifact(phase7_artifact) if isinstance(phase7_artifact, dict) else list(phase7_artifact or [])
    monitoring_records = flatten_monitoring_artifact(monitoring_artifact)
    alert_results = flatten_phase9_artifact(alert_artifact) if isinstance(alert_artifact, dict) else list(alert_artifact or [])
    p5_records = list(phase5_records or [])
    p6_records = _spatial_records(phase6_results)
    raw_records = []
    raw_records.extend(p5_records)
    raw_records.extend(p6_records)
    raw_records.extend(phase7_records)
    raw_records.extend(monitoring_records)
    raw_records.extend(alert_results)
    live = isinstance(monitoring_artifact, dict) and monitoring_artifact.get("run_mode") == "live"
    synthetic = bool((isinstance(phase7_artifact, dict) and phase7_artifact.get("evaluation_type")) or
                     (isinstance(monitoring_artifact, dict) and monitoring_artifact.get("demonstration", {}).get("synthetic_data")) or
                     (isinstance(alert_artifact, dict) and alert_artifact.get("demonstration", {}).get("synthetic_data")) or
                     any(adapt_incident(item).get("synthetic_data") for item in raw_records))
    mode = dataset_mode or ("live_data" if live else ("synthetic_demo" if synthetic else "provided_data"))

    incident_by_key = {}
    adapter_errors = []
    for index, raw in enumerate(raw_records):
        try:
            view = adapt_incident(raw, dataset_mode=mode)
            incident_id, event_id = view.get("incident_id"), view.get("event_id")
            key = ("incident", str(incident_id)) if incident_id is not None else (
                ("event", str(event_id)) if event_id is not None else ("row", str(index)))
            view["dashboard_row_id"] = f"{key[0]}:{key[1]}"
            existing = incident_by_key.get(key)
            incident_by_key[key] = _merge_view(existing, view) if existing else view
        except Exception as exc:
            adapter_errors.append({"index": index, "error_type": type(exc).__name__, "message": str(exc)[:300]})
            invalid = adapt_incident(raw, dataset_mode=mode)
            invalid["adapter_status"] = "error"
            invalid["adapter_error"] = f"{type(exc).__name__}: {str(exc)[:250]}"
            invalid["dashboard_row_id"] = f"invalid:{index}"
            incident_by_key[("row", str(index))] = invalid
    incidents = list(incident_by_key.values())
    alerts = [_adapt_alert(item, mode) for item in alert_results]
    active_alerts = _active_alerts(alerts)
    dataset = {"phase": 10, "dataset_mode": mode,
               "dataset_label": ("DEMONSTRATION / SYNTHETIC DATA" if mode == "synthetic_demo" else
                                 "LIVE DATA — configured public sources" if mode in {"live", "live_data"} else
                                 "PROVIDED DATA"),
               "incidents": incidents, "alerts": alerts, "active_alerts": active_alerts,
               "monitoring_events": [json_safe(item) for item in monitoring_records],
               "alert_evaluations": [json_safe(item) for item in alert_results],
               "adapter_errors": adapter_errors,
               "limitations": [
                   "Synthetic demonstration records are not live incidents or validated operational feeds.",
                   "Phase 9 alert history and Phase 8 monitoring state are in-memory upstream state; this dashboard does not persist them.",
                   "Phase 3 classifier availability depends on local model/runtime dependencies; recorded failures remain visible.",
                   "The coordinate plot contains only supplied, range-valid, successfully geocoded coordinates; demo coordinates are synthetic.",
                   "This is decision support for human review, not autonomous response or scientifically validated prediction.",
               ]}
    dataset["summary"] = dashboard_summary(dataset)
    dataset["spatial_summary"] = spatial_summary(incidents)
    dataset["monitoring_summary"] = monitoring_summary(dataset["monitoring_events"])
    dataset["intelligence_summary"] = intelligence_summary(incidents)
    dataset["system_health"] = system_health(incidents, dataset["monitoring_events"], alert_results)
    return json_safe(dataset)


def build_demo_dashboard_data(repository_root=None):
    """Load existing Phase 7–9 synthetic artifacts; no new intelligence is generated."""
    root = Path(repository_root or Path(__file__).resolve().parents[1])
    p7 = _read_json(root / "evaluation" / "phase7_demonstration_results.json")
    p8 = _read_json(root / "evaluation" / "phase8_demonstration_results.json")
    p9 = _read_json(root / "evaluation" / "phase9_demonstration_results.json")
    return build_dashboard_data(phase7_artifact=p7, monitoring_artifact=p8,
                               alert_artifact=p9, dataset_mode="synthetic_demo")


def load_dashboard_input(path):
    """Load a caller-provided JSON dataset without external service access."""
    value = _read_json(Path(path))
    if isinstance(value, dict) and value.get("phase") == 10 and isinstance(value.get("incidents"), list):
        return json_safe(value)
    if isinstance(value, dict):
        if value.get("run_mode") == "live" and isinstance(value.get("events"), list):
            return build_dashboard_data(monitoring_artifact=value, alert_artifact=value,
                                        dataset_mode="live_data")
        return build_dashboard_data(
            phase5_records=value.get("phase5_records"), phase6_results=value.get("phase6_results"),
            phase7_artifact=value.get("phase7_artifact"), monitoring_artifact=value.get("monitoring_artifact"),
            alert_artifact=value.get("alert_artifact"), dataset_mode=value.get("dataset_mode", "provided_data"))
    if isinstance(value, list):
        return build_dashboard_data(phase5_records=value, dataset_mode="provided_data")
    raise ValueError("dashboard input must be a JSON object or list")


def build_demonstration_artifact(dataset):
    return {"phase": 10, "mode": dataset.get("dataset_mode", "unknown"),
            "dashboard_summary": dataset.get("summary", {}),
            "incident_summary": [{key: item.get(key) for key in (
                "incident_id", "event_id", "disaster_type", "priority_level", "priority_score",
                "severity_level", "urgency_level", "confidence_level", "alert_level",
                "event_state", "geocoding_status", "hotspot", "synthetic_data")}
                for item in dataset.get("incidents", [])],
            "alert_summary": dataset.get("alerts", []),
            "spatial_summary": dataset.get("spatial_summary", {}),
            "monitoring_summary": dataset.get("monitoring_summary", {}),
            "intelligence_summary": dataset.get("intelligence_summary", {}),
            "system_health": dataset.get("system_health", {}),
            "limitations": dataset.get("limitations", [])}


def _read_json(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _spatial_records(value):
    if isinstance(value, dict):
        return value.get("incidents") or ([value] if value.get("incident_id") else [])
    return list(value or [])


def _merge_view(existing, current):
    """Prefer later non-missing values while retaining earlier upstream evidence."""
    merged = dict(existing)
    for key, value in current.items():
        if value not in (None, [], {}, "") or key not in merged:
            merged[key] = value
    # Provenance and upstream records from each source remain available for drilldown.
    merged["source_records"] = list(existing.get("source_records", [existing.get("source_record")])) + [current.get("source_record")]
    return merged


def _adapt_alert(item, mode):
    item = item if isinstance(item, dict) else {}
    decision = item.get("alert_decision") if isinstance(item.get("alert_decision"), dict) else {}
    record = item.get("alert_record") if isinstance(item.get("alert_record"), dict) else {}
    spatial = item.get("spatial_context") if isinstance(item.get("spatial_context"), dict) else decision.get("spatial_context", {})
    notification = record.get("notification") if isinstance(record.get("notification"), dict) else {}
    escalation = record.get("escalation") if isinstance(record.get("escalation"), dict) else {
        "is_escalation": decision.get("is_escalation"), "reasons": decision.get("escalation_reasons"),
        "deescalated": decision.get("deescalated")}
    suppression = item.get("alert_suppression") if isinstance(item.get("alert_suppression"), dict) else {
        "suppressed": decision.get("suppressed"), "reason": decision.get("suppression_reason")}
    return json_safe({"alert_id": record.get("alert_id") or decision.get("alert_id"),
                      "incident_id": record.get("incident_id") or decision.get("incident_id") or
                                     item.get("incident_intelligence", {}).get("incident_id"),
                      "event_id": record.get("event_id") or decision.get("event_id") or item.get("event_id"),
                      "alert_level": record.get("alert_level") or decision.get("alert_level"),
                      "alert_type": record.get("alert_type") or decision.get("alert_type"),
                      "alert_status": item.get("alert_status", "UNKNOWN"),
                      "record_status": record.get("status"),
                      "triggered_rules": record.get("trigger_reasons") or decision.get("trigger_reasons") or [],
                      "explanation": [reason.get("reason") for reason in (record.get("trigger_reasons") or decision.get("trigger_reasons") or []) if isinstance(reason, dict)],
                      "created_at": record.get("created_at"), "escalation": escalation,
                      "suppression": suppression, "cooldown": item.get("alert_suppression", {}),
                      "notification_status": notification.get("status"),
                      "notification_attempted": notification.get("attempted"),
                      "location": spatial.get("normalized_location") or spatial.get("location_text"),
                      "priority_score": decision.get("priority_score") or item.get("incident_intelligence", {}).get("priority_score"),
                      "source": item.get("provenance", {}).get("source") if isinstance(item.get("provenance"), dict) else None,
                      "dataset_mode": mode, "synthetic_data": True, "source_record": item})


def _active_alerts(alerts):
    """Resolve current in-memory alert per incident from alert event order."""
    current = {}
    for alert in alerts:
        subject = alert.get("incident_id") or alert.get("event_id") or alert.get("alert_id")
        status = str(alert.get("alert_status") or "").upper()
        if subject is None:
            continue
        if status in {"CREATED", "ESCALATED"} and alert.get("alert_id"):
            current[str(subject)] = alert.get("alert_id")
        elif status == "RESOLVED":
            current.pop(str(subject), None)
    return [alert for alert in alerts if str(alert.get("incident_id") or alert.get("event_id") or alert.get("alert_id")) in current
            and alert.get("alert_id") == current[str(alert.get("incident_id") or alert.get("event_id") or alert.get("alert_id"))]
            and str(alert.get("alert_status") or "").upper() in {"CREATED", "ESCALATED"}]
