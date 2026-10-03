"""Explainable rules over structured Phase 5/6 evidence."""

LEVEL_RANK = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def _score(value):
    try:
        numeric = float(value)
        if numeric != numeric or numeric in (float("inf"), float("-inf")):
            return None
        return numeric if 0.0 <= numeric <= 100.0 else None
    except (TypeError, ValueError):
        return None


def evaluate_rules(incident, spatial, config):
    """Return candidate level, explainable results, and alert type."""
    priority = _score(incident.get("priority_score"))
    severity = _score(incident.get("severity_score"))
    urgency = _score(incident.get("urgency_score"))
    flags = incident.get("decision_flags") or []
    flags = [str(flag) for flag in flags] if isinstance(flags, (list, tuple, set)) else []
    requests = incident.get("requests") or []
    resource_urgent = "URGENT_RESOURCE_NEED" in flags or (
        bool(requests) and urgency is not None and urgency >= config.urgent_resource_threshold)
    hotspot = bool(spatial.get("hotspot")) or (
        spatial.get("spatial_priority_score") is not None and
        _score(spatial.get("spatial_priority_score")) is not None and
        _score(spatial.get("spatial_priority_score")) >= config.hotspot_priority_threshold)

    results = []
    candidates = []
    def record(rule, triggered, reason, level=None, kind=None):
        results.append({"rule": rule, "triggered": bool(triggered), "reason": reason})
        if triggered and level:
            candidates.append((level, kind or "OPERATIONAL_INCIDENT", rule))

    record("critical_priority", priority is not None and priority >= config.priority_critical_threshold,
           (f"Priority score {priority:.1f} meets CRITICAL threshold {config.priority_critical_threshold:.1f}."
            if priority is not None and priority >= config.priority_critical_threshold else
            f"Priority score does not meet CRITICAL threshold {config.priority_critical_threshold:.1f}."),
           "CRITICAL", "CRITICAL_PRIORITY")
    record("high_priority", priority is not None and config.priority_high_threshold <= priority < config.priority_critical_threshold,
           (f"Priority score {priority:.1f} meets HIGH threshold {config.priority_high_threshold:.1f}."
            if priority is not None and config.priority_high_threshold <= priority < config.priority_critical_threshold else
            f"Priority score does not meet HIGH band {config.priority_high_threshold:.1f}–{config.priority_critical_threshold:.1f}."),
           "HIGH", "HIGH_PRIORITY")
    record("medium_priority", priority is not None and config.priority_medium_threshold <= priority < config.priority_high_threshold,
           (f"Priority score {priority:.1f} meets MEDIUM threshold {config.priority_medium_threshold:.1f}."
            if priority is not None and config.priority_medium_threshold <= priority < config.priority_high_threshold else
            f"Priority score does not meet MEDIUM band {config.priority_medium_threshold:.1f}–{config.priority_high_threshold:.1f}."),
           "MEDIUM", "PRIORITY_REVIEW")
    record("critical_urgency", urgency is not None and urgency >= config.urgency_critical_threshold,
           (f"Urgency score {urgency:.1f} meets CRITICAL threshold {config.urgency_critical_threshold:.1f}."
            if urgency is not None and urgency >= config.urgency_critical_threshold else
            f"Urgency score does not meet CRITICAL threshold {config.urgency_critical_threshold:.1f}."),
           "CRITICAL", "URGENT_INCIDENT")
    record("high_urgency", urgency is not None and config.urgency_high_threshold <= urgency < config.urgency_critical_threshold,
           (f"Urgency score {urgency:.1f} meets HIGH threshold {config.urgency_high_threshold:.1f}."
            if urgency is not None and config.urgency_high_threshold <= urgency < config.urgency_critical_threshold else
            f"Urgency score does not meet HIGH band {config.urgency_high_threshold:.1f}–{config.urgency_critical_threshold:.1f}."),
           "HIGH", "URGENT_INCIDENT")
    record("critical_severity", severity is not None and severity >= config.severity_critical_threshold,
           (f"Severity score {severity:.1f} meets CRITICAL threshold {config.severity_critical_threshold:.1f}."
            if severity is not None and severity >= config.severity_critical_threshold else
            f"Severity score does not meet CRITICAL threshold {config.severity_critical_threshold:.1f}."),
           "CRITICAL", "SEVERE_INCIDENT")
    record("high_severity", severity is not None and config.severity_high_threshold <= severity < config.severity_critical_threshold,
           (f"Severity score {severity:.1f} meets HIGH threshold {config.severity_high_threshold:.1f}."
            if severity is not None and config.severity_high_threshold <= severity < config.severity_critical_threshold else
            f"Severity score does not meet HIGH band {config.severity_high_threshold:.1f}–{config.severity_critical_threshold:.1f}."),
           "HIGH", "SEVERE_INCIDENT")
    rescue = "IMMEDIATE_RESCUE" in flags
    record("immediate_rescue", rescue,
           "Phase 5 IMMEDIATE_RESCUE flag is present." if rescue else "No Phase 5 IMMEDIATE_RESCUE flag is present.",
           config.immediate_rescue_level, "RESCUE")
    critical_flag = "CRITICAL_PRIORITY" in flags
    record("critical_priority_flag", critical_flag,
           "Phase 5 CRITICAL_PRIORITY flag is present." if critical_flag else "No Phase 5 CRITICAL_PRIORITY flag is present.",
           config.critical_flag_level, "CRITICAL_PRIORITY")
    record("urgent_resource_request", resource_urgent,
           (f"Urgent resource evidence meets configured urgency threshold {config.urgent_resource_threshold:.1f}."
            if resource_urgent else "No configured urgent resource request evidence is present."),
           config.resource_alert_level, "RESOURCE_REQUEST")
    record("spatial_hotspot", hotspot,
           ("Phase 6 hotspot or configured spatial-priority evidence is present."
            if hotspot else "No Phase 6 hotspot or qualifying spatial-priority evidence is present."),
           config.hotspot_alert_level, "SPATIAL_HOTSPOT")

    if not candidates:
        level = "INFO" if priority is None and severity is None and urgency is None else "LOW"
        return level, results, [], "NO_ALERT"
    highest = max(LEVEL_RANK[level] for level, _, _ in candidates)
    winners = [(level, kind, rule) for level, kind, rule in candidates if LEVEL_RANK[level] == highest]
    return winners[0][0], results, [item[2] for item in winners], winners[0][1]
