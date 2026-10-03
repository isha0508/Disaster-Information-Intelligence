"""Summary metrics derived from upstream Phase 5–9 outputs without rescoring."""

from collections import Counter


LEVELS = ("CRITICAL", "HIGH", "MEDIUM", "MODERATE", "LOW", "INFO", "UNKNOWN")


def distribution(records, field):
    counts = Counter()
    for record in records or []:
        value = record.get(field)
        counts[str(value).upper() if value not in (None, "") else "UNKNOWN"] += 1
    return {level: counts.get(level, 0) for level in LEVELS if counts.get(level, 0)} | (
        {key: counts[key] for key in sorted(counts) if key not in LEVELS})


def dashboard_summary(dataset):
    incidents = dataset.get("incidents") or []
    alerts = dataset.get("alerts") or []
    events = dataset.get("monitoring_events") or []
    unique = {}
    for record in incidents:
        key = record.get("incident_id") or record.get("event_id")
        if key is not None:
            unique.setdefault(str(key), record)
    distinct = list(unique.values())
    active_alerts = dataset.get("active_alerts")
    if active_alerts is None:
        active_alerts = [a for a in alerts if str(a.get("alert_status") or "").upper() in {"CREATED", "ESCALATED"}
                         or str(a.get("status") or "").upper() in {"ACTIVE", "ESCALATED"}]
    event_counts = Counter(str(event.get("event_state") or "UNKNOWN").upper() for event in events)
    priority = Counter(str(item.get("priority_level") or "UNKNOWN").upper() for item in distinct)
    critical_alerts = sum(str(item.get("alert_level") or "").upper() == "CRITICAL" for item in active_alerts)
    unresolved = sum(_has_location(item) and item.get("geocoding_status") != "success" for item in distinct)
    hotspots = sum(bool(item.get("hotspot")) for item in distinct)
    needs_review = sum(str(item.get("priority_level") or "").upper() in {"HIGH", "CRITICAL"} or
                       str(item.get("alert_status") or "").upper() in {"CREATED", "ESCALATED"}
                       for item in distinct)
    return {
        "total_incidents": len(distinct), "critical_incidents": priority.get("CRITICAL", 0),
        "high_priority_incidents": priority.get("HIGH", 0), "active_alerts": len(active_alerts),
        "critical_alerts": critical_alerts, "new_monitoring_events": event_counts.get("NEW", 0),
        "updated_events": event_counts.get("UPDATED", 0),
        "duplicate_or_suppressed_events": event_counts.get("DUPLICATE", 0) + event_counts.get("SUPPRESSED", 0),
        "invalid_events": event_counts.get("INVALID", 0), "unresolved_locations": unresolved,
        "spatial_hotspots": hotspots, "requiring_human_review": needs_review,
        "priority_distribution": dict(priority),
        "severity_distribution": distribution(distinct, "severity_level"),
        "urgency_distribution": distribution(distinct, "urgency_level"),
        "confidence_distribution": distribution(distinct, "confidence_level"),
        "alert_level_distribution": distribution(alerts, "alert_level"),
        "dataset_mode": dataset.get("dataset_mode", "unknown"),
        "synthetic_data": dataset.get("dataset_mode") == "synthetic_demo",
    }


def monitoring_summary(events):
    events = events or []
    counts = Counter(str(event.get("event_state") or "UNKNOWN").upper() for event in events)
    error_rows = [error for event in events for error in (event.get("processing_errors") or [])]
    source_failures = sum(error.get("phase") == "source" for error in error_rows if isinstance(error, dict))
    phase_failures = sum(error.get("phase") not in {"source", "normalization"} for error in error_rows if isinstance(error, dict))
    return {"total_events": len(events), "new": counts.get("NEW", 0), "updated": counts.get("UPDATED", 0),
            "duplicate": counts.get("DUPLICATE", 0), "invalid": counts.get("INVALID", 0),
            "failed": counts.get("FAILED", 0), "source_failures": source_failures,
            "pipeline_failures": phase_failures,
            "source_status": "failed" if source_failures else ("not_reported" if not events else "no_source_failure_observed"),
            "normalization_status": "degraded" if counts.get("INVALID") else ("available" if events else "unavailable")}


def spatial_summary(incidents):
    incidents = incidents or []
    points = [item for item in incidents if item.get("latitude") is not None and item.get("longitude") is not None]
    clusters = {item.get("spatial_cluster_id") for item in incidents if item.get("spatial_cluster_id")}
    hotspots = {item.get("hotspot_id") for item in incidents if item.get("hotspot_id")}
    unresolved = [item for item in incidents if _has_location(item) and item.get("geocoding_status") != "success"]
    return {"valid_coordinate_incidents": len(points), "unresolved_location_incidents": len(unresolved),
            "spatial_cluster_count": len(clusters), "hotspot_count": len(hotspots),
            "hotspot_incident_count": sum(bool(item.get("hotspot")) for item in incidents),
            "points": [{"incident_id": x.get("incident_id"), "latitude": x.get("latitude"),
                        "longitude": x.get("longitude"), "priority_level": x.get("priority_level"),
                        "alert_level": x.get("alert_level"), "hotspot": x.get("hotspot"),
                        "spatial_cluster_id": x.get("spatial_cluster_id"),
                        "synthetic_data": x.get("synthetic_data")} for x in points],
            "unresolved": [{"incident_id": x.get("incident_id"), "location": x.get("normalized_location") or x.get("location_text"),
                            "geocoding_status": x.get("geocoding_status")} for x in unresolved]}


def intelligence_summary(incidents):
    records = incidents or []
    return {"with_grounded_summary": sum(bool(x.get("phase7_summary")) for x in records),
            "recommendation_count": sum(len(x.get("phase7_recommendations") or []) for x in records),
            "uncertainty_count": sum(len(x.get("phase7_uncertainties") or []) for x in records),
            "grounding_warning_count": sum(len(x.get("grounding_warnings") or []) for x in records),
            "disclaimer": "Generated narrative is grounded in structured system evidence but is not guaranteed to be hallucination-free. Recommendations are decision support for human review, not autonomous commands."}


def system_health(incidents, monitoring_events, alert_results):
    collected = {str(phase): [] for phase in range(3, 10)}
    for item in monitoring_events or []:
        event_status = str(item.get("processing_status") or item.get("event_state") or "unavailable").upper()
        collected.setdefault("8", []).append(event_status)
        for phase, status in (item.get("phase_status") or {}).items():
            if str(phase).startswith("phase"):
                collected.setdefault(phase.removeprefix("phase"), []).append(str(status))
    for item in incidents or []:
        if item.get("phase_status"):
            for phase, status in item["phase_status"].items():
                collected.setdefault(str(phase).removeprefix("phase"), []).append(str(status))
    if alert_results:
        collected.setdefault("9", []).extend("completed" if item.get("alert_status") not in {None, "INVALID"} else "failed"
                                             for item in alert_results)
    result = {}
    for phase in range(3, 10):
        statuses = collected.get(str(phase), [])
        if not statuses:
            status = "unavailable"
        elif any("failed" in value or value == "INVALID" for value in statuses):
            status = "degraded" if any("completed" in value or value in {"PROCESSED", "CREATED", "ESCALATED", "NO_ALERT", "SUPPRESSED"} for value in statuses) else "failed"
        elif any("unavailable" in value or "skipped" in value for value in statuses):
            status = "degraded"
        else:
            status = "successful"
        result[f"phase{phase}"] = {"status": status, "observations": len(statuses), "observed_statuses": dict(Counter(statuses))}
    result["environment_note"] = "Phase 3 classifier dependency status is taken from recorded monitoring results; the known local NumPy C-extension import issue can leave classification degraded."
    return result


def _has_location(item):
    location = item.get("location")
    return bool(item.get("location_text") or item.get("normalized_location") or location)
