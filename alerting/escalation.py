"""Compare successive structured incident snapshots for material worsening."""

from alerting.rules import LEVEL_RANK, _score


def assess_escalation(previous, current, config):
    """Return (escalated, deescalated, reasons) without using timestamps as evidence."""
    if not previous:
        return False, False, []
    prior_level = previous.get("last_alert_level")
    level = current.get("alert_level", "LOW")
    reasons = []
    if prior_level and LEVEL_RANK.get(level, 0) > LEVEL_RANK.get(prior_level, 0):
        reasons.append(f"Alert level increased from {prior_level} to {level}.")
    prev_observed = previous.get("last_observed", {})
    prev_priority = _score(prev_observed.get("priority_score"))
    now_priority = _score(current.get("priority_score"))
    prev_urgency = _score(prev_observed.get("urgency_score"))
    now_urgency = _score(current.get("urgency_score"))
    if (prior_level and level == prior_level and prev_priority is not None and now_priority is not None
            and now_priority - prev_priority >= config.escalation_score_delta):
        reasons.append(f"Priority rose by {now_priority - prev_priority:.1f}, meeting the configured escalation delta {config.escalation_score_delta:.1f}.")
    if (prior_level and level == prior_level and prev_urgency is not None and now_urgency is not None
            and now_urgency - prev_urgency >= config.escalation_score_delta):
        reasons.append(f"Urgency rose by {now_urgency - prev_urgency:.1f}, meeting the configured escalation delta {config.escalation_score_delta:.1f}.")
    old_flags = set(prev_observed.get("decision_flags", []))
    new_flags = set(current.get("decision_flags", []))
    if "IMMEDIATE_RESCUE" in new_flags - old_flags:
        reasons.append("IMMEDIATE_RESCUE appeared in the updated structured evidence.")
    if current.get("hotspot") and not prev_observed.get("hotspot"):
        reasons.append("Phase 6 hotspot evidence became significant in the update.")
    escalated = bool(prior_level and reasons)
    deescalated = bool(prior_level and LEVEL_RANK.get(level, 0) < LEVEL_RANK.get(prior_level, 0))
    return escalated, deescalated, reasons
