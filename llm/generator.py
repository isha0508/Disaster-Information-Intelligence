"""Grounded Phase 7 generation and deterministic validation."""

from datetime import datetime, timezone
import re

from llm.config import LLMConfig
from llm.grounding import build_grounded_context
from llm import prompts
from llm.providers import MockLLMProvider, create_provider
from llm.schemas import validate_phase7_result


def _numeric_tokens(value):
    if isinstance(value, dict):
        return set().union(*(_numeric_tokens(v) for v in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_numeric_tokens(v) for v in value)) if value else set()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {str(value)}
    if isinstance(value, str):
        return set(re.findall(r"(?<!\w)\d+(?:\.\d+)?", value))
    return set()


def _grounding_warnings(payload, context):
    warnings = []
    allowed = set()
    for section in ("evidence", "computed_intelligence", "resources", "spatial"):
        allowed |= _numeric_tokens(context.get(section, {}))
    narrative = " ".join([payload.get("summary", ""), payload.get("situation_assessment", "")] +
                         payload.get("priority_explanation", []) + payload.get("recommended_actions", []))
    for token in sorted(_numeric_tokens(narrative)):
        if token not in allowed:
            warnings.append(f"Potential unsupported numeric claim in generated narrative: {token}.")
    spatial = context.get("spatial", {})
    if spatial.get("geocoding_status") != "success" and spatial.get("coordinates") is None:
        if re.search(r"\b(?:coordinates?\s+(?:are|were)\s+(?:verified|confirmed)|(?:verified|confirmed)\s+(?:WGS84\s+)?coordinates?|located at coordinates)\b", narrative, re.I):
            warnings.append("Generated narrative appears to claim verified coordinates despite unresolved geocoding.")
    type_groups = {
        "flood": {"flood", "flooding"}, "earthquake": {"earthquake", "quake"},
        "fire": {"fire", "wildfire"}, "hurricane": {"hurricane", "cyclone", "typhoon"},
        "tornado": {"tornado"}, "landslide": {"landslide", "mudslide"},
        "tsunami": {"tsunami"}, "volcano": {"volcano", "eruption"}, "storm": {"storm"},
    }
    supplied = context.get("evidence", {}).get("disaster_type")
    supplied_values = supplied if isinstance(supplied, list) else [supplied]
    allowed_types = set()
    for value in supplied_values:
        if value:
            normalized = str(value).casefold()
            allowed_types.add(normalized)
            for terms in type_groups.values():
                if normalized in terms:
                    allowed_types.update(terms)
    for canonical, terms in type_groups.items():
        if not (terms & allowed_types) and re.search(r"\b(?:" + "|".join(re.escape(term) for term in terms) + r")\b", narrative, re.I):
            warnings.append(f"Generated narrative may mention unsupported disaster type: {canonical}.")
    explicit = context.get("resources", {}).get("explicit_requests") or []
    explicit_text = " ".join(
        str(item.get("raw_text", item.get("category", ""))) if isinstance(item, dict) else str(item)
        for item in explicit
    ).casefold()
    resource_terms = ("drinking water", "water", "food", "medical supplies", "medical",
                      "medicine", "shelter", "blankets", "generators", "first aid")
    request_verbs = r"(?:requested|request(?:ed)?|asked for)"
    for term in resource_terms:
        if term in explicit_text:
            continue
        pattern = r"\b(?:" + re.escape(term) + r"\s+\w+\s+" + request_verbs + r"|" + request_verbs + r"\s+\w+\s+" + re.escape(term) + r")\b"
        if re.search(pattern, narrative, re.I):
            warnings.append(f"Generated narrative may introduce an unprovided explicit resource request: {term}.")
    return warnings


def _safe_payload(record, context, provider, config, prompt, required_fields=None):
    errors = []
    required_fields = required_fields or ("summary", "situation_assessment", "priority_explanation", "recommended_actions")
    selected = provider
    if selected is None:
        try:
            selected = create_provider(config)
        except Exception as exc:
            errors.append(f"provider_initialization_failed:{type(exc).__name__}")
    try:
        if selected is None:
            raise RuntimeError("provider unavailable")
        payload = selected.generate(prompt, context, config)
        if not isinstance(payload, dict):
            validation_errors = ["provider_output_not_object"]
        else:
            validation_errors = []
            for name in required_fields:
                if name in {"summary", "situation_assessment"}:
                    valid = isinstance(payload.get(name), str) and bool(payload[name].strip())
                else:
                    valid = isinstance(payload.get(name), list) and all(isinstance(x, str) for x in payload[name])
                if not valid:
                    validation_errors.append(f"invalid_{name}")
        if validation_errors:
            errors.extend(f"invalid_provider_output:{item}" for item in validation_errors)
            raise ValueError("invalid provider output")
        return payload, errors, getattr(selected, "name", selected.__class__.__name__), "provider"
    except Exception as exc:
        errors.append(f"generation_failed:{type(exc).__name__}")
        fallback = MockLLMProvider()
        return fallback.generate(prompt, context, config), errors, getattr(selected, "name", "unavailable"), "mock_fallback"


def _resource_summary(context):
    resources = context.get("resources", {})
    explicit = resources.get("explicit_requests") or []
    if not isinstance(explicit, list):
        explicit = [explicit]
    estimates = resources.get("estimated_needs")
    return {
        "explicit_requests": explicit,
        "estimated_needs": estimates,
        "estimated_needs_type": "heuristic_estimate" if estimates is not None else "not_provided",
        "estimate_disclaimer": "Planning aid only; not a confirmed request or validated requirement." if estimates is not None else None,
    }


def _deterministic_intelligence(context):
    values = dict(context.get("computed_intelligence", {}))
    return {key: values.get(key) for key in (
        "severity_score", "severity_level", "urgency_score", "urgency_level",
        "priority_score", "priority_level", "confidence_score", "confidence_level")}


def _key_evidence(context):
    evidence = context.get("evidence", {})
    output = []
    names = (("disaster_type", "Disaster type"), ("casualties", "Casualty evidence"),
             ("displaced", "Displacement evidence"), ("rescue", "Rescue evidence"),
             ("infrastructure", "Infrastructure evidence"), ("requests", "Request evidence"),
             ("resources", "Resource mentions"), ("location_mentions", "Location mention"))
    for key, label in names:
        value = evidence.get(key)
        if value:
            values = value if isinstance(value, list) else [value]
            output.extend(f"{label}: {item}" for item in values)
    return output


def _generate(record, provider=None, config=None, spatial_context=None, prompt=None):
    config = config or LLMConfig.from_env()
    record = record if isinstance(record, dict) else {}
    context = build_grounded_context(record, spatial_context=spatial_context, config=config)
    payload, errors, provider_name, generation_mode = _safe_payload(
        record, context, provider, config, prompt or prompts.INTELLIGENCE_PROMPT)
    warnings = _grounding_warnings(payload, context) if config.grounding_enabled else []
    spatial = context.get("spatial", {})
    result = {
        "incident_id": context.get("incident_id"),
        "summary": payload["summary"],
        "situation_assessment": payload["situation_assessment"],
        "priority_explanation": payload["priority_explanation"],
        "key_evidence": _key_evidence(context),
        "recommended_actions": payload["recommended_actions"] if config.recommendations_enabled else [],
        "resource_summary": _resource_summary(context),
        "spatial_context": spatial,
        "deterministic_intelligence": _deterministic_intelligence(context),
        "uncertainties": list(context.get("unknowns_and_uncertainties", [])),
        "confidence_note": _confidence_note(context),
        "grounding_warnings": warnings,
        "generation_errors": errors,
        "generation_metadata": {
            "phase7_version": "1.0",
            "provider": provider_name,
            "model": config.model,
            "generation_mode": generation_mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "grounding_enabled": config.grounding_enabled,
            "api_key_included": False,
        },
    }
    # Facts and fixed fields are assembled by deterministic code, never accepted
    # from the provider. Keep this check explicit for future schema changes.
    result_errors = validate_phase7_result(result)
    if result_errors:
        result["generation_errors"].extend(f"result_validation:{item}" for item in result_errors)
    expected_id = context.get("incident_id")
    if result["incident_id"] != expected_id:
        result["grounding_warnings"].append("Incident ID differs from the supplied record.")
    for key, value in _deterministic_intelligence(context).items():
        if result["deterministic_intelligence"].get(key) != value:
            result["grounding_warnings"].append(f"Deterministic {key} differs from Phase 5 input.")
    if result["spatial_context"].get("coordinates") != spatial.get("coordinates") or result["spatial_context"].get("geocoding_status") != spatial.get("geocoding_status"):
        result["grounding_warnings"].append("Spatial coordinates or geocoding status differ from supplied Phase 6 context.")
    return result


def _confidence_note(context):
    intelligence = context.get("computed_intelligence", {})
    level, score = intelligence.get("confidence_level"), intelligence.get("confidence_score")
    if level is None:
        return "Phase 5 confidence information is unavailable; no calibrated probability is provided."
    return f"Phase 5 evidence confidence is {level}" + (f" ({score}/100)" if score is not None else "") + "; it is an evidence-quality score, not a calibrated probability."


def generate_intelligence(record, provider=None, config=None, spatial_context=None):
    """Generate a complete grounded Phase 7 result for one incident."""
    try:
        return _generate(record, provider, config, spatial_context)
    except Exception as exc:
        fallback = _generate({}, provider=MockLLMProvider(), config=config, spatial_context=None)
        fallback["incident_id"] = record.get("incident_id") if isinstance(record, dict) else None
        fallback["generation_errors"].append(f"controlled_generation_error:{type(exc).__name__}")
        return fallback


def _task_payload(record, task, prompt, provider=None, config=None, spatial_context=None):
    config = config or LLMConfig.from_env()
    context = build_grounded_context(record, spatial_context=spatial_context, config=config)
    required = {"summary": ("summary",),
                "priority": ("situation_assessment", "priority_explanation"),
                "recommendations": ("recommended_actions",)}[task]
    payload, errors, _, _ = _safe_payload(record, context, provider, config, prompt, required)
    if task == "summary" and "summary" not in payload:
        payload["summary"] = "A grounded summary could not be generated."
    if task == "recommendations" and "recommended_actions" not in payload:
        payload["recommended_actions"] = []
    return payload, errors


def generate_incident_summary(record, provider=None, config=None, spatial_context=None):
    """Return a concise grounded summary string."""
    payload, _ = _task_payload(record, "summary", prompts.SITUATION_SUMMARY_PROMPT, provider, config, spatial_context)
    return payload["summary"]


def explain_priority(record, provider=None, config=None, spatial_context=None):
    """Return grounded explanation lines without recalculating Phase 5 scores."""
    payload, _ = _task_payload(record, "priority", prompts.PRIORITY_EXPLANATION_PROMPT,
                               provider, config, spatial_context)
    return payload["priority_explanation"]


def generate_recommendations(record, provider=None, config=None, spatial_context=None):
    """Return cautious evidence-grounded recommended actions."""
    if config is None:
        config = LLMConfig.from_env()
    if not config.recommendations_enabled:
        return []
    payload, _ = _task_payload(record, "recommendations", prompts.RECOMMENDED_ACTIONS_PROMPT, provider, config, spatial_context)
    return payload["recommended_actions"]


def generate_situation_report(record, provider=None, config=None, spatial_context=None):
    """Generate a readable Markdown situation report with explicit missing-data sections."""
    from llm.reports import render_situation_report
    result = _generate(record, provider, config, spatial_context, prompts.SITUATION_REPORT_PROMPT)
    return render_situation_report(result)


def generate_intelligence_batch(records, provider=None, config=None, spatial_context=None):
    """Generate results in input order; isolate malformed records/provider failures."""
    output = []
    for index, record in enumerate(records or []):
        try:
            result = generate_intelligence(record, provider, config, spatial_context)
            result["generation_metadata"]["batch_index"] = index
            output.append(result)
        except Exception as exc:
            result = _generate({}, provider=MockLLMProvider(), config=config)
            result["incident_id"] = record.get("incident_id") if isinstance(record, dict) else None
            result["generation_errors"].append(f"batch_item_failed:{type(exc).__name__}")
            result["generation_metadata"]["batch_index"] = index
            output.append(result)
    return output
