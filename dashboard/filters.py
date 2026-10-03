"""Deterministic filter and sort operations over Phase 10 view models."""


def filter_incidents(records, filters=None):
    filters = filters or {}
    result = []
    for item in records or []:
        if not isinstance(item, dict):
            continue
        checks = (
            ("disaster_type", "disaster_type"), ("priority_level", "priority_level"),
            ("severity_level", "severity_level"), ("urgency_level", "urgency_level"),
            ("confidence_level", "confidence_level"), ("alert_level", "alert_level"),
            ("event_state", "event_state"), ("geocoding_status", "geocoding_status"),
            ("spatial_cluster_id", "spatial_cluster_id"),
        )
        if any(_active(filters.get(filter_key)) and item.get(field) != filters[filter_key]
               for filter_key, field in checks):
            continue
        hotspot = filters.get("hotspot")
        if hotspot is True and not item.get("hotspot"):
            continue
        if hotspot is False and item.get("hotspot"):
            continue
        flag = filters.get("decision_flag")
        if _active(flag) and flag not in (item.get("decision_flags") or []):
            continue
        result.append(item)
    return result


def sort_incidents(records, sort_by="priority_score", descending=True):
    """Sort on upstream values; missing values remain last in either direction."""
    records = list(records or [])
    if sort_by == "alert_level":
        order = {"CRITICAL": 5, "HIGH": 4, "MEDIUM": 3, "LOW": 2, "INFO": 1}
        return sorted(records, key=lambda item: (
            item.get(sort_by) is None,
            -order.get(str(item.get(sort_by) or "").upper(), 0) if descending else order.get(str(item.get(sort_by) or "").upper(), 0),
            str(item.get("incident_id") or item.get("event_id") or "")))
    if sort_by not in {"priority_score", "severity_score", "urgency_score", "confidence_score"}:
        sort_by = "priority_score"
    def key(item):
        value = item.get(sort_by)
        try:
            number = float(value) if value is not None else None
        except (TypeError, ValueError):
            number = None
        return (number is None, (-number if descending else number) if number is not None else 0,
                str(item.get("incident_id") or item.get("event_id") or ""))
    return sorted(records, key=key)


def _active(value):
    return value not in (None, "", "All", "ALL")
