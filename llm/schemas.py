"""Lightweight schema helpers for Phase 7 results."""

from typing import Any, Dict, List, TypedDict


class Phase7Result(TypedDict):
    incident_id: Any
    summary: str
    situation_assessment: str
    priority_explanation: List[str]
    key_evidence: List[str]
    recommended_actions: List[str]
    resource_summary: Dict[str, Any]
    spatial_context: Dict[str, Any]
    deterministic_intelligence: Dict[str, Any]
    uncertainties: List[str]
    confidence_note: str
    grounding_warnings: List[str]
    generation_errors: List[str]
    generation_metadata: Dict[str, Any]


REQUIRED_RESULT_FIELDS = (
    "incident_id", "summary", "situation_assessment", "priority_explanation",
    "key_evidence", "recommended_actions", "resource_summary", "spatial_context", "deterministic_intelligence",
    "uncertainties", "confidence_note", "grounding_warnings", "generation_errors", "generation_metadata",
)


def validate_provider_payload(payload):
    """Validate only provider-authored narrative fields; facts are code-owned."""
    errors = []
    if not isinstance(payload, dict):
        return [], ["provider_output_not_object"]
    for name in ("summary", "situation_assessment"):
        if not isinstance(payload.get(name), str) or not payload[name].strip():
            errors.append(f"invalid_{name}")
    for name in ("priority_explanation", "recommended_actions"):
        value = payload.get(name)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            errors.append(f"invalid_{name}")
    return errors


def validate_phase7_result(result):
    """Return schema errors for a completed Phase 7 result mapping."""
    errors = []
    if not isinstance(result, dict):
        return ["result_not_object"]
    for key in REQUIRED_RESULT_FIELDS:
        if key not in result:
            errors.append(f"missing_{key}")
    for key in ("summary", "situation_assessment", "confidence_note"):
        if key in result and not isinstance(result[key], str):
            errors.append(f"invalid_{key}")
    for key in ("priority_explanation", "key_evidence", "recommended_actions", "uncertainties", "grounding_warnings", "generation_errors"):
        if key in result and (not isinstance(result[key], list) or any(not isinstance(x, str) for x in result[key])):
            errors.append(f"invalid_{key}")
    for key in ("resource_summary", "spatial_context", "deterministic_intelligence", "generation_metadata"):
        if key in result and not isinstance(result[key], dict):
            errors.append(f"invalid_{key}")
    return errors
