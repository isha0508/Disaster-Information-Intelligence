"""Build bounded, provenance-aware context from Phase 4/5/6 records."""

import json
import re

from gis.schemas import validate_coordinates
from llm.config import LLMConfig


EVIDENCE_FIELDS = ("casualties", "displaced", "rescue", "infrastructure", "requests",
                   "resources", "organizations", "persons", "numbers")


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [item for item in value if item is not None and str(item).strip()]
    return []


def _safe(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


def _spatial_for_record(record, supplied):
    if not isinstance(supplied, dict):
        supplied = {}
    incident_id = record.get("incident_id")
    if isinstance(supplied.get("incidents"), list):
        match = next((item for item in supplied["incidents"]
                      if isinstance(item, dict) and item.get("incident_id") == incident_id), None)
        if match:
            merged = dict(match)
            for key in ("clusters", "hotspots", "spatial_priority_ranking"):
                merged[key] = supplied.get(key, [])
            supplied = merged
    else:
        supplied = {**record, **supplied}
    status = supplied.get("geocoding_status", record.get("geocoding_status", "unavailable"))
    latitude, longitude = supplied.get("latitude", record.get("latitude")), supplied.get("longitude", record.get("longitude"))
    valid, _ = validate_coordinates(latitude, longitude)
    coordinates = {"latitude": float(latitude), "longitude": float(longitude)} if status == "success" and valid else None
    location = supplied.get("normalized_location", record.get("normalized_location"))
    if location is None:
        raw = supplied.get("location", record.get("location", []))
        raw_list = _as_list(raw)
        location = str(raw_list[0]) if raw_list else supplied.get("location_text", record.get("location_text"))
    cluster_id = supplied.get("spatial_cluster_id", record.get("spatial_cluster_id"))
    cluster_size = supplied.get("spatial_cluster_size")
    hotspots = supplied.get("hotspots", []) or []
    hotspot = next((h for h in hotspots if isinstance(h, dict) and h.get("spatial_cluster_id") == cluster_id), None)
    priority_rows = supplied.get("spatial_priority_ranking", []) or []
    area_id = supplied.get("spatial_area_id")
    priority = next((p for p in priority_rows if isinstance(p, dict) and
                     (p.get("spatial_area_id") == area_id or (cluster_id and p.get("spatial_cluster_id") == cluster_id))), None)
    if priority is None and isinstance(supplied.get("spatial_priority"), dict):
        priority = supplied["spatial_priority"]
    return {
        "location_text": _safe(supplied.get("location_text", record.get("location_text", location))),
        "normalized_location": _safe(location),
        "coordinates": coordinates,
        "geocoding_status": status,
        "geocoding_source": supplied.get("geocoding_source", record.get("geocoding_source")),
        "spatial_cluster_id": cluster_id,
        "spatial_cluster_size": cluster_size,
        "hotspot": bool(hotspot) or bool(supplied.get("hotspot", False)),
        "hotspot_id": hotspot.get("hotspot_id") if hotspot else supplied.get("hotspot_id"),
        "hotspot_incident_count": hotspot.get("incident_count") if hotspot else None,
        "spatial_priority_score": (priority or {}).get("spatial_priority_score"),
        "spatial_metadata": _safe(supplied.get("spatial_metadata", record.get("spatial_metadata", {}))),
    }


def build_grounded_context(record, spatial_context=None, config=None):
    """Convert Phase 4/5/6 values to explicit evidence/intelligence/unknown sections.

    Phase 7 does not re-extract facts from free text. Raw text is included only
    as unvalidated source text and is explicitly labeled as such.
    """
    config = config or LLMConfig.from_env()
    if not isinstance(record, dict):
        record = {}
    evidence = {field: _safe(_as_list(record.get(field))) for field in EVIDENCE_FIELDS}
    evidence["disaster_type"] = _safe(record.get("disaster_type"))
    evidence["location_mentions"] = _safe(_as_list(record.get("location", record.get("location_text"))))
    computed = {key: _safe(record.get(key)) for key in (
        "severity_score", "severity_level", "urgency_score", "urgency_level",
        "priority_score", "priority_level", "confidence_score", "confidence_level",
        "severity_factors", "urgency_factors", "priority_factors", "confidence_factors",
        "decision_flags", "is_duplicate", "duplicate_of", "cluster_id") if key in record}
    resource_estimates = record.get("resource_estimates")
    if resource_estimates is None:
        resource_estimates = {"explicit_requests": {"raw_mentions": evidence["requests"]},
                              "estimated_needs": None}
    else:
        resource_estimates = _safe(resource_estimates)
    explicit = resource_estimates.get("explicit_requests", {}) if isinstance(resource_estimates, dict) else {}
    explicit_requests = explicit.get("raw_mentions", []) if isinstance(explicit, dict) else []
    if not explicit_requests:
        explicit_requests = evidence["requests"]
    estimates = resource_estimates.get("estimated_needs") if isinstance(resource_estimates, dict) else None
    spatial = _spatial_for_record(record, spatial_context) if config.spatial_context_enabled else {
        "location_text": None, "normalized_location": None, "coordinates": None,
        "geocoding_status": "not_included", "geocoding_source": None,
        "spatial_cluster_id": None, "spatial_cluster_size": None, "hotspot": False,
        "hotspot_id": None, "hotspot_incident_count": None,
        "spatial_priority_score": None, "spatial_metadata": {},
    }
    uncertainties = []
    if not evidence["location_mentions"] and not spatial["location_text"]:
        uncertainties.append("No structured location mention is available.")
    elif spatial["geocoding_status"] != "success" or spatial["coordinates"] is None:
        uncertainties.append(f"Location is mentioned as {spatial['normalized_location'] or spatial['location_text']}, but verified coordinates are unavailable (geocoding status: {spatial['geocoding_status']}).")
    if not evidence["casualties"]:
        uncertainties.append("No structured casualty information was provided.")
    elif not any(re.search(r"\d", str(x)) for x in evidence["casualties"]):
        uncertainties.append("Casualty mentions are present, but no explicit count was provided.")
    if not evidence["disaster_type"]:
        uncertainties.append("Disaster type is unavailable in structured evidence.")
    confidence = record.get("confidence_level")
    if confidence == "LOW":
        uncertainties.append("Phase 5 confidence is LOW; extracted evidence may be incomplete.")
    elif confidence is None:
        uncertainties.append("No Phase 5 confidence score was provided.")
    if estimates is not None:
        uncertainties.append("Estimated resource needs are heuristic planning aids, not confirmed requests or requirements.")
    if not computed.get("priority_score"):
        # Zero is a valid computed score; test key presence, not truthiness.
        if "priority_score" not in computed:
            uncertainties.append("No deterministic Phase 5 priority score was provided.")
    source_text = record.get("text", "")
    context = {
        "incident_id": _safe(record.get("incident_id")),
        "source_text": str(source_text)[:min(2000, config.max_context_chars)],
        "source_text_status": "unvalidated_raw_text_not_reextracted_by_phase7",
        "evidence_provenance": "phase4_structured_extractions_not_independently_verified",
        "evidence": evidence,
        "computed_intelligence": computed,
        "resources": {
            "explicit_requests": _safe(explicit_requests),
            "estimated_needs": _safe(estimates),
            "estimates_are_heuristic": estimates is not None,
        },
        "spatial": spatial,
        "unknowns_and_uncertainties": uncertainties,
        "grounding_rules": [
            "Use structured evidence only as supplied; raw text is context, not newly validated extraction.",
            "Keep explicit requests separate from heuristic estimates.",
            "Do not change computed severity, urgency, priority, confidence, or incident ID.",
            "Only state coordinates when geocoding_status is success and coordinates passed range validation.",
            "State missing information and uncertainty explicitly; never invent facts.",
        ],
    }
    encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > config.max_context_chars:
        context["context_truncated"] = True
        context["source_text"] = context["source_text"][:min(256, config.max_context_chars // 20)]
        for field, values in context["evidence"].items():
            if isinstance(values, list):
                context["evidence"][field] = [str(item)[:128] for item in values[:5]]
        for key, value in list(context["computed_intelligence"].items()):
            if isinstance(value, list):
                context["computed_intelligence"][key] = value[:5] if key.endswith("_factors") else value[:20]
        explicit_items = context["resources"]["explicit_requests"]
        context["resources"]["explicit_requests"] = explicit_items[:5] if isinstance(explicit_items, list) else []
        estimate_json = json.dumps(context["resources"]["estimated_needs"], ensure_ascii=False, default=str)
        if len(estimate_json) > config.max_context_chars // 3:
            context["resources"]["estimated_needs"] = {"truncated": True,
                "description": "Heuristic estimate details omitted because the context size limit was reached."}
            context["unknowns_and_uncertainties"].append("Some heuristic estimate details were truncated to meet the configured context limit.")
        context["spatial"]["spatial_metadata"] = {}
        context["unknowns_and_uncertainties"] = context["unknowns_and_uncertainties"][:12]
        encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > config.max_context_chars:
            context["source_text"] = ""
            for field, values in context["evidence"].items():
                if isinstance(values, list):
                    context["evidence"][field] = [str(item)[:64] for item in values[:2]]
            for key, value in list(context["computed_intelligence"].items()):
                if isinstance(value, list):
                    context["computed_intelligence"][key] = value[:2] if key.endswith("_factors") else value[:5]
            explicit_items = context["resources"]["explicit_requests"]
            if isinstance(explicit_items, list):
                context["resources"]["explicit_requests"] = [
                    {key: (str(value)[:80] if isinstance(value, str) else value)
                     for key, value in item.items() if key in {"category", "quantity", "raw_text", "source"}}
                    if isinstance(item, dict) else str(item)[:80]
                    for item in explicit_items[:3]
                ]
            if context["resources"]["estimated_needs"] is not None:
                context["resources"]["estimated_needs"] = {"truncated": True,
                    "description": "Heuristic estimate details omitted because the context size limit was reached."}
            context["unknowns_and_uncertainties"] = [str(item)[:120] for item in context["unknowns_and_uncertainties"][:5]]
            context["grounding_rules"] = [
                "Use structured evidence only; raw text is not re-extracted.",
                "Separate explicit requests from heuristic estimates.",
                "Do not change Phase 5 scores or incident ID.",
                "Only include coordinates when geocoding succeeded and coordinates validate.",
                "State missing information and uncertainty; do not invent facts.",
            ]
            if len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) > config.max_context_chars:
                context["computed_intelligence"] = {key: value for key, value in context["computed_intelligence"].items()
                                                     if key.endswith("_score") or key.endswith("_level")}
                context["evidence"] = {"disaster_type": context["evidence"].get("disaster_type"),
                                       "location_mentions": context["evidence"].get("location_mentions"),
                                       "casualties": context["evidence"].get("casualties", [])[:2]}
    return context
