"""Canonical disaster-type normalization and source-aware evidence resolution."""

import re


# Canonical values stay deliberately small and compatible with labels already
# consumed by Phase 3/5, with explicit canonical forms for common source labels.
_ALIASES = {
    "earthquake": "earthquake", "earthquakes": "earthquake", "eq": "earthquake",
    "flood": "flood", "floods": "flood", "flooding": "flood",
    "flash flood": "flood", "flash floods": "flood", "flash_flood": "flood", "fl": "flood",
    "cyclone": "cyclone", "tropical cyclone": "cyclone", "hurricane": "cyclone",
    "typhoon": "cyclone", "tc": "cyclone",
    "storm": "storm",
    "wildfire": "wildfire", "wildfires": "wildfire", "forest fire": "wildfire",
    "forest fires": "wildfire", "forest_fire": "wildfire", "wf": "wildfire",
    "fire": "fire", "fires": "fire",
    "landslide": "landslide", "landslides": "landslide", "ls": "landslide",
    "volcanic eruption": "volcanic_eruption", "volcanic eruptions": "volcanic_eruption",
    "volcanic_eruption": "volcanic_eruption", "volcano": "volcanic_eruption",
    "volcanoes": "volcanic_eruption", "vo": "volcanic_eruption",
    "tsunami": "tsunami", "drought": "drought", "dr": "drought",
}


def canonicalize_disaster_type(value):
    """Return a known canonical type, or None when evidence is not mappable."""
    if isinstance(value, dict):
        value = value.get("label", value.get("text", value.get("value")))
    if isinstance(value, (list, tuple)):
        for item in value:
            canonical = canonicalize_disaster_type(item)
            if canonical:
                return canonical
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    key = re.sub(r"[\s_-]+", " ", value.strip().casefold())
    return _ALIASES.get(key) or _ALIASES.get(key.replace(" ", "_"))


def source_type_evidence(event):
    """Read structured type fields from the known official source adapters."""
    source = str(event.get("source") or "").casefold()
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    raw = None
    if source == "usgs":
        # Feature type is the adapter's preserved source properties.type value.
        raw = metadata.get("feature_type")
    elif source == "gdacs":
        raw = event.get("disaster_type")
        if raw is None:
            raw = metadata.get("event_type")
    if raw is None:
        return None
    canonical = canonicalize_disaster_type(raw)
    return {"value": raw, "canonical_value": canonical, "source": source,
            "source_authority": source.upper(),
            "provenance": "authoritative_structured_metadata",
            "status": "resolved" if canonical else "unresolved"}


def _prediction_evidence(classification):
    prediction = classification.get("disaster_type") if isinstance(classification, dict) else None
    if not isinstance(prediction, dict):
        prediction = {}
    raw = prediction.get("label")
    if raw is None:
        return None
    return {"value": raw, "canonical_value": canonicalize_disaster_type(raw),
            "confidence": prediction.get("confidence"),
            "model": prediction.get("model", "distilbert_disaster_type"),
            "provenance": "phase3_ml"}


def _nlp_evidence(value):
    if value is None or value == "":
        return None
    return {"value": value, "canonical_value": canonicalize_disaster_type(value),
            "provenance": "phase4_nlp"}


def resolve_disaster_type(event, classification=None, phase4_type=None):
    """Resolve operational type while retaining source, Phase 4, and Phase 3 evidence.

    Precedence: recognized authoritative source type, recognized Phase 4 type,
    then recognized Phase 3 prediction. An unknown source value stays visible
    and unresolved as source evidence, while a separately attributed fallback
    may still be used operationally.
    """
    source_evidence = source_type_evidence(event)
    nlp_evidence = _nlp_evidence(phase4_type)
    prediction_evidence = _prediction_evidence(classification)
    source_canonical = source_evidence.get("canonical_value") if source_evidence else None
    nlp_canonical = nlp_evidence.get("canonical_value") if nlp_evidence else None
    ml_canonical = prediction_evidence.get("canonical_value") if prediction_evidence else None

    if source_canonical:
        canonical, selected = source_canonical, "authoritative_source"
        rationale = (f"Authoritative {source_evidence['source_authority']} source type resolved to "
                     f"{canonical}; Phase 4 and Phase 3 evidence were retained separately.")
    elif nlp_canonical:
        canonical, selected = nlp_canonical, "phase4_nlp_fallback"
        rationale = ("No mappable authoritative source type was available; the Phase 4 extracted type "
                     "was used as a fallback. Source and Phase 3 evidence remain separately available.")
    elif ml_canonical:
        canonical, selected = ml_canonical, "phase3_ml_fallback"
        rationale = ("No mappable authoritative source or Phase 4 type was available; the Phase 3 "
                     "prediction was used as a fallback, not treated as authoritative.")
    else:
        canonical, selected = None, "unresolved"
        rationale = "No mappable source, Phase 4, or Phase 3 type was available; disaster type is unresolved."

    if source_canonical and ml_canonical:
        agreement = source_canonical == ml_canonical
        agreement_status = "AGREEMENT" if agreement else "DISAGREEMENT"
    elif source_canonical:
        agreement, agreement_status = None, "SOURCE_ONLY"
    elif ml_canonical:
        agreement, agreement_status = None, "ML_ONLY"
    else:
        agreement, agreement_status = None, "UNRESOLVED"
    disagreement = agreement_status == "DISAGREEMENT"
    if disagreement:
        rationale += (f" Phase 3 predicted {ml_canonical} (confidence "
                      f"{prediction_evidence.get('confidence')}); the disagreement is recorded and "
                      "the source type remains operational.")
    elif agreement_status == "AGREEMENT":
        rationale += " The normalized Phase 3 prediction agrees with the source type."
    elif agreement_status == "ML_ONLY" and source_evidence:
        rationale += " The raw authoritative type was unmappable; this fallback does not rewrite it."

    warning = None
    if source_evidence and not source_canonical:
        warning = "Authoritative source type is present but unmappable; it was retained without normalization."
    return {
        "source_disaster_type": source_evidence,
        "phase4_disaster_type": nlp_evidence,
        "ml_disaster_type": prediction_evidence,
        "canonical_disaster_type": canonical,
        "selected_from": selected,
        "resolution_method": selected,
        "resolution_reason": rationale,
        "resolution_rationale": rationale,
        "type_agreement": agreement,
        "type_agreement_status": agreement_status,
        "model_disagreement": disagreement,
        "source_type_value": source_evidence.get("value") if source_evidence else None,
        "ml_type_value": prediction_evidence.get("value") if prediction_evidence else None,
        "ml_confidence": prediction_evidence.get("confidence") if prediction_evidence else None,
        "source_authority": source_evidence.get("source_authority") if source_evidence else None,
        "warning": warning,
    }
