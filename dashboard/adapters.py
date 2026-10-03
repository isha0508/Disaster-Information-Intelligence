"""Read-only adapters from Phase 5–9 records to dashboard view models."""

from copy import deepcopy
import json
import math
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SECRET_PARTS = ("password", "secret", "token", "credential", "authorization", "api_key", "apikey")


def _clean(value):
    """Remove credential-like fields and convert nested values to JSON-safe data."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = str(key)
            if any(part in name.casefold() for part in _SECRET_PARTS):
                continue
            if "url" in name.casefold() and isinstance(item, str):
                try:
                    parts = urlsplit(item)
                    host = parts.hostname or ""
                    if parts.port:
                        host += f":{parts.port}"
                    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not any(part in k.casefold() for part in _SECRET_PARTS)]
                    result[name] = urlunsplit((parts.scheme, host, parts.path, urlencode(query), parts.fragment))
                except ValueError:
                    result[name] = item
            else:
                result[name] = _clean(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, set):
        return [_clean(item) for item in sorted(value, key=str)]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _phase6_row(phase6, incident_id):
    if not isinstance(phase6, dict):
        return {}
    rows = phase6.get("incidents") or []
    if incident_id is not None:
        match = next((row for row in rows if isinstance(row, dict) and row.get("incident_id") == incident_id), None)
        if match:
            return deepcopy(match)
    return deepcopy(rows[0]) if len(rows) == 1 and isinstance(rows[0], dict) else {}


def _spatial_from_global(phase6, incident_id, row):
    spatial = deepcopy(row)
    if not isinstance(phase6, dict):
        return spatial
    cluster_id = spatial.get("spatial_cluster_id")
    for hotspot in phase6.get("hotspots") or []:
        if not isinstance(hotspot, dict):
            continue
        member_ids = hotspot.get("incident_ids") or []
        if (incident_id is not None and incident_id in member_ids) or (
                cluster_id is not None and hotspot.get("spatial_cluster_id") == cluster_id):
            spatial.update({"hotspot": True, "hotspot_id": hotspot.get("hotspot_id"),
                            "hotspot_score": hotspot.get("hotspot_score"),
                            "hotspot_incident_count": hotspot.get("incident_count")})
            break
    for ranking in phase6.get("spatial_priority_ranking") or []:
        if not isinstance(ranking, dict):
            continue
        matches_area = spatial.get("spatial_area_id") is not None and ranking.get("spatial_area_id") == spatial.get("spatial_area_id")
        matches_cluster = cluster_id is not None and ranking.get("spatial_cluster_id") == cluster_id
        if matches_area or matches_cluster:
            spatial["spatial_priority_score"] = ranking.get("spatial_priority_score")
            break
    return spatial


def adapt_incident(record, dataset_mode="unknown"):
    """Map one supported upstream shape to dashboard fields without rescore/mutation."""
    if not isinstance(record, dict):
        return {"incident_id": None, "event_id": None, "dataset_mode": dataset_mode,
                "adapter_status": "invalid", "adapter_error": "record is not an object",
                "source_record": _clean(record)}
    source = _clean(record)
    source_event = _mapping(source.get("event"))
    intelligence = _mapping(source.get("intelligence"))
    phase5 = source.get("incident_intelligence") or source.get("incident_record") or source.get("incident") or intelligence.get("phase5")
    if not isinstance(phase5, dict):
        phase5 = source.get("phase5_phase6_incident") if isinstance(source.get("phase5_phase6_incident"), dict) else source
    phase5 = deepcopy(phase5)
    incident_id = phase5.get("incident_id") or source.get("incident_id")
    phase6 = intelligence.get("phase6") or source.get("phase6")
    phase6_row = _phase6_row(phase6, incident_id)
    if not phase6_row and isinstance(source.get("spatial_context"), dict):
        phase6_row = deepcopy(source["spatial_context"])
    elif not phase6_row and isinstance(source.get("spatial"), dict):
        phase6_row = deepcopy(source["spatial"])
    spatial = _spatial_from_global(phase6, incident_id, phase6_row)
    phase7 = source.get("grounded_intelligence") or source.get("phase7_result") or intelligence.get("phase7") or {}
    phase7 = _mapping(phase7)
    type_resolution = _mapping(source.get("type_resolution") or phase5.get("type_resolution"))
    source_location = _mapping(source.get("source_location") or phase5.get("source_location"))
    alert_decision = _mapping(source.get("alert_decision"))
    alert_record = _mapping(source.get("alert_record"))
    alert_spatial = _mapping(alert_decision.get("spatial_context"))
    if not spatial:
        spatial = alert_spatial or _mapping(source.get("spatial_context"))
    # Coordinates are only exposed as a point when Phase 6 said geocoding succeeded.
    geocoding_status = spatial.get("geocoding_status", phase5.get("geocoding_status"))
    latitude = longitude = None
    if geocoding_status == "success":
        try:
            candidate_lat = spatial.get("latitude", phase5.get("latitude"))
            candidate_lon = spatial.get("longitude", phase5.get("longitude"))
            if isinstance(candidate_lat, bool) or isinstance(candidate_lon, bool):
                raise ValueError("boolean coordinates")
            candidate_lat, candidate_lon = float(candidate_lat), float(candidate_lon)
            if math.isfinite(candidate_lat) and math.isfinite(candidate_lon) and -90 <= candidate_lat <= 90 and -180 <= candidate_lon <= 180:
                latitude, longitude = candidate_lat, candidate_lon
        except (TypeError, ValueError):
            pass
    requests = phase5.get("requests")
    estimates = phase5.get("resource_estimates")
    monitoring_errors = source.get("processing_errors") or []
    phase_status = source.get("phase_status") or {}
    notification = _mapping(alert_record.get("notification"))
    suppression = _mapping(source.get("alert_suppression"))
    suppression.update({"suppressed": alert_decision.get("suppressed", suppression.get("suppressed", False)),
                        "reason": alert_decision.get("suppression_reason")})
    provenance = source.get("provenance") or _mapping(phase5.get("provenance"))
    return {
        "incident_id": incident_id, "event_id": source.get("event_id") or alert_decision.get("event_id"),
        "alert_id": alert_record.get("alert_id") or alert_decision.get("alert_id"),
        "dataset_mode": dataset_mode, "adapter_status": "ok",
        "disaster_type": phase5.get("canonical_disaster_type") or type_resolution.get("canonical_disaster_type") or phase5.get("disaster_type"),
        "canonical_disaster_type": phase5.get("canonical_disaster_type") or type_resolution.get("canonical_disaster_type"),
        "source_disaster_type": phase5.get("source_disaster_type") or type_resolution.get("source_disaster_type"),
        "phase4_disaster_type": phase5.get("phase4_disaster_type") or type_resolution.get("phase4_disaster_type"),
        "ml_disaster_type": phase5.get("ml_disaster_type") or type_resolution.get("ml_disaster_type"),
        "ml_confidence": type_resolution.get("ml_confidence"),
        "type_agreement": type_resolution.get("type_agreement"),
        "type_agreement_status": type_resolution.get("type_agreement_status"),
        "model_disagreement": type_resolution.get("model_disagreement", False),
        "resolution_method": type_resolution.get("resolution_method"),
        "resolution_rationale": type_resolution.get("resolution_rationale") or type_resolution.get("resolution_reason"),
        "source_authority": type_resolution.get("source_authority"),
        "type_resolution_warning": type_resolution.get("warning"),
        "type_resolution": type_resolution,
        "location": phase5.get("location"), "location_text": spatial.get("location_text", phase5.get("location_text")),
        "normalized_location": spatial.get("normalized_location", phase5.get("normalized_location")),
        "latitude": latitude, "longitude": longitude, "geocoding_status": geocoding_status,
        "geocoding_source": spatial.get("geocoding_source", phase5.get("geocoding_source")),
        "coordinate_source": spatial.get("coordinate_source", phase5.get("coordinate_source")),
        "source_location": source_location,
        "nlp_location_entities": phase5.get("nlp_location_entities", source.get("nlp_location_entities", [])),
        "severity_score": phase5.get("severity_score"), "severity_level": phase5.get("severity_level"),
        "urgency_score": phase5.get("urgency_score"), "urgency_level": phase5.get("urgency_level"),
        "priority_score": phase5.get("priority_score"), "priority_level": phase5.get("priority_level"),
        "confidence_score": phase5.get("confidence_score"), "confidence_level": phase5.get("confidence_level"),
        "casualties": phase5.get("casualties"), "displaced": phase5.get("displaced"),
        "infrastructure": phase5.get("infrastructure"), "rescue": phase5.get("rescue"),
        "requests": requests, "resources": phase5.get("resources"),
        "resource_estimates": estimates, "resource_priorities": phase5.get("resource_priorities"),
        "resource_estimates_are_heuristic": _estimate_is_heuristic(estimates),
        "decision_flags": phase5.get("decision_flags"),
        "spatial_cluster_id": spatial.get("spatial_cluster_id", phase5.get("spatial_cluster_id")),
        "spatial_cluster_size": spatial.get("spatial_cluster_size", phase5.get("spatial_cluster_size")),
        "spatial_area_id": spatial.get("spatial_area_id", phase5.get("spatial_area_id")),
        "hotspot": bool(spatial.get("hotspot") or spatial.get("hotspot_id")),
        "hotspot_id": spatial.get("hotspot_id"), "spatial_priority_score": spatial.get("spatial_priority_score"),
        "spatial_metadata": spatial.get("spatial_metadata", phase5.get("spatial_metadata")),
        "phase7_summary": phase7.get("summary"),
        "phase7_situation_assessment": phase7.get("situation_assessment"),
        "phase7_key_evidence": phase7.get("key_evidence"),
        "phase7_recommendations": phase7.get("recommended_actions"),
        "phase7_uncertainties": phase7.get("uncertainties"),
        "phase7_confidence_note": phase7.get("confidence_note"),
        "grounding_warnings": phase7.get("grounding_warnings"),
        "recommendations_disclaimer": "Decision support for human review; not an autonomous command.",
        "event_state": source.get("event_state"), "processing_status": source.get("processing_status"),
        "phase_status": phase_status, "processing_errors": monitoring_errors,
        "source": source.get("source") or _mapping(provenance).get("source"),
        "source_event_id": source.get("source_event_id"), "provenance": provenance,
        "source_title": source_event.get("title"),
        "source_url": source_event.get("source_url") or source_event.get("url"),
        "source_event_timestamp": source_event.get("event_timestamp"),
        "source_published_at": source_event.get("published_at"),
        "source_updated_at": source_event.get("updated_at"),
        "source_retrieved_at": source_event.get("retrieved_at"),
        "source_metadata": source_event.get("metadata"),
        "source_latitude": source_event.get("latitude"),
        "source_longitude": source_event.get("longitude"),
        "first_seen": source.get("first_seen"), "last_seen": source.get("last_seen"),
        "observed_at": _mapping(source.get("event")).get("observed_at"),
        "published_at": _mapping(source.get("event")).get("published_at"),
        "alert_status": source.get("alert_status"),
        "alert_level": alert_record.get("alert_level") or alert_decision.get("alert_level"),
        "alert_type": alert_record.get("alert_type") or alert_decision.get("alert_type"),
        "alert_reasons": alert_record.get("trigger_reasons") or alert_decision.get("trigger_reasons"),
        "alert_escalation": alert_record.get("escalation") or {
            "is_escalation": alert_decision.get("is_escalation"),
            "reasons": alert_decision.get("escalation_reasons"),
            "deescalated": alert_decision.get("deescalated")},
        "alert_suppression": suppression,
        "notification_status": notification.get("status"),
        "notification_attempted": notification.get("attempted"),
        "confidence_is_probability": False,
        "synthetic_data": dataset_mode == "synthetic_demo" or _source_is_synthetic(source, provenance),
        "source_provenance": provenance,
        "source_record": source,
    }


def _estimate_is_heuristic(estimates):
    if not isinstance(estimates, dict):
        return None
    estimated = estimates.get("estimated_needs")
    return bool(estimated is not None and (estimates.get("is_heuristic") or estimates.get("method") or estimated))


def _source_is_synthetic(source, provenance):
    return bool(source.get("synthetic_data") or source.get("source_type") == "synthetic" or
                _mapping(provenance).get("source_type") == "synthetic" or
                str(source.get("source", "")).casefold().startswith("synthetic"))


def flatten_monitoring_artifact(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("events"), list):
        return value["events"]
    if isinstance(value, dict) and isinstance(value.get("cycles"), list):
        return [item for cycle in value["cycles"] if isinstance(cycle, list) for item in cycle]
    if isinstance(value, dict) and value.get("event_id") is not None:
        return [value]
    return []


def flatten_phase7_artifact(value):
    if not isinstance(value, dict):
        return []
    results = []
    for row in value.get("incident_results") or []:
        if isinstance(row, dict):
            results.append({"incident_intelligence": row.get("phase5_phase6_incident"),
                            "grounded_intelligence": row.get("phase7_result"),
                            "phase6": row.get("phase6_spatial_result"),
                            "source": "synthetic_phase7_demonstration", "source_type": "synthetic"})
    return results


def flatten_phase9_artifact(value):
    if not isinstance(value, dict):
        return []
    if isinstance(value.get("alerts"), list):
        return [item for item in value["alerts"] if isinstance(item, dict)]
    results = []
    for scenario in value.get("scenarios") or []:
        if not isinstance(scenario, dict):
            continue
        if isinstance(scenario.get("result"), dict):
            item = deepcopy(scenario["result"])
            item["scenario"] = scenario.get("scenario")
            item["synthetic_data"] = True
            results.append(item)
        for item in scenario.get("history") or []:
            if isinstance(item, dict):
                copied = deepcopy(item)
                copied["scenario"] = scenario.get("scenario")
                copied["synthetic_data"] = True
                results.append(copied)
    return results


def json_safe(value):
    return _clean(value)
