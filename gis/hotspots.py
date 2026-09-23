"""Interpretable geographic concentration and area-priority summaries."""

from gis.schemas import SpatialConfig


def _score(record, key):
    try:
        value = float(record.get(key, 0) or 0)
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(100.0, value))


def detect_hotspots(records, config=None):
    """Create hotspot summaries for spatial clusters meeting count threshold."""
    config = config or SpatialConfig()
    groups = {}
    for record in records:
        cluster_id = record.get("spatial_cluster_id")
        if cluster_id:
            groups.setdefault(cluster_id, []).append(record)
    hotspots = []
    for cluster_id, members in sorted(groups.items()):
        if len(members) < config.hotspot_min_incidents:
            continue
        high_priority = sum(_score(r, "priority_score") >= 50 for r in members)
        rescue = sum("RESCUE_OPERATION" in (r.get("decision_flags") or []) for r in members)
        casualties = sum(bool(r.get("casualties")) or "CASUALTY_REPORT" in (r.get("decision_flags") or []) for r in members)
        severity = sum(_score(r, "severity_score") for r in members) / len(members)
        urgency = sum(_score(r, "urgency_score") for r in members) / len(members)
        priority = sum(_score(r, "priority_score") for r in members) / len(members)
        count_signal = min(100.0, 25.0 * len(members))
        score = round(0.5 * priority + 0.25 * severity + 0.15 * urgency + 0.10 * count_signal, 2)
        reasons = [f"{len(members)} incidents within the configured spatial cluster"]
        if high_priority:
            reasons.append(f"{high_priority} high/critical-priority incidents")
        if rescue:
            reasons.append(f"{rescue} rescue incidents")
        if casualties:
            reasons.append(f"{casualties} casualty-related incidents")
        hotspots.append({"hotspot_id": f"HOTSPOT_{cluster_id}", "spatial_cluster_id": cluster_id,
                         "incident_ids": sorted(str(r.get("incident_id", "")) for r in members),
                         "incident_count": len(members), "high_priority_count": high_priority,
                         "rescue_incident_count": rescue, "casualty_incident_count": casualties,
                         "severity_score": round(severity, 2), "urgency_score": round(urgency, 2),
                         "priority_score": round(priority, 2), "hotspot_score": score, "reason": reasons})
    return hotspots


def rank_spatial_priority(records, config=None):
    """Rank cluster areas using average Phase 5 priority plus capped count signal.

    Formula: normalized weighted average of mean Phase 5 priority and
    ``min(100, 25 * incident_count)``. Config weights default to 0.8/0.2.
    """
    config = config or SpatialConfig()
    groups = {}
    for record in records:
        key = record.get("spatial_area_id") or record.get("spatial_cluster_id")
        if key:
            groups.setdefault(key, []).append(record)
    denominator = config.priority_count_weight + config.priority_phase5_weight
    results = []
    for cluster_id, members in groups.items():
        mean_priority = sum(_score(r, "priority_score") for r in members) / len(members)
        count_signal = min(100.0, 25.0 * len(members))
        score = (config.priority_phase5_weight * mean_priority + config.priority_count_weight * count_signal) / denominator
        results.append({"spatial_area_id": cluster_id,
                        "spatial_cluster_id": members[0].get("spatial_cluster_id"),
                        "incident_count": len(members),
                        "mean_phase5_priority": round(mean_priority, 2),
                        "incident_count_signal": round(count_signal, 2),
                        "spatial_priority_score": round(score, 2),
                        "incident_ids": sorted(str(r.get("incident_id", "")) for r in members)})
    return sorted(results, key=lambda x: (-x["spatial_priority_score"], x["spatial_area_id"]))
