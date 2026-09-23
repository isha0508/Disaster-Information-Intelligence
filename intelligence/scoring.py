"""
intelligence/scoring.py
=======================

Phase 5 scoring engines.

Provides:
- Severity scoring
- Urgency scoring
- Priority scoring
- Confidence scoring
- Deterministic incident IDs

All scoring is rule-based, deterministic, interpretable, and bounded
between 0 and 100.

NOTE:
These scores are operational heuristics, not statistically calibrated
probabilities.
"""

import re
import hashlib
from typing import Dict, List, Any, Optional

from intelligence.config import (
    SEVERITY_CONFIG,
    URGENCY_CONFIG,
    PRIORITY_CONFIG,
    CONFIDENCE_CONFIG,
)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _extract_numbers_from_text(text: str) -> List[int]:
    """
    Extract all integer numbers from text.

    Example:
        "25 people injured and 3 missing"
        -> [25, 3]
    """
    if text is None:
        return []

    return [
        int(value)
        for value in re.findall(r"\d{1,9}", str(text))
    ]


def _extract_number_from_text(text: str) -> Optional[int]:
    """
    Extract the first number from text.

    Kept for backward compatibility with existing code/tests.
    """
    numbers = _extract_numbers_from_text(text)

    return numbers[0] if numbers else None


def _normalize_text(text: str) -> str:
    """Normalize text for keyword matching."""
    return re.sub(
        r"\s+",
        " ",
        str(text).lower()
    ).strip()


def _get_severity_level(score: float) -> str:
    """Convert severity score to operational severity level."""

    if score >= SEVERITY_CONFIG.CRITICAL_THRESHOLD:
        return "CRITICAL"

    if score >= SEVERITY_CONFIG.HIGH_THRESHOLD:
        return "HIGH"

    if score >= SEVERITY_CONFIG.MODERATE_THRESHOLD:
        return "MODERATE"

    return "LOW"


def _get_urgency_level(score: float) -> str:
    """Convert urgency score to operational urgency level."""

    if score >= URGENCY_CONFIG.CRITICAL_THRESHOLD:
        return "CRITICAL"

    if score >= URGENCY_CONFIG.HIGH_THRESHOLD:
        return "HIGH"

    if score >= URGENCY_CONFIG.MODERATE_THRESHOLD:
        return "MODERATE"

    return "LOW"


def _get_priority_level(score: float) -> str:
    """Convert priority score to operational priority level."""

    if score >= PRIORITY_CONFIG.CRITICAL_THRESHOLD:
        return "CRITICAL"

    if score >= PRIORITY_CONFIG.HIGH_THRESHOLD:
        return "HIGH"

    if score >= PRIORITY_CONFIG.MODERATE_THRESHOLD:
        return "MODERATE"

    return "LOW"


def _get_confidence_level(score: float) -> str:
    """Convert confidence score to confidence level."""

    if score >= CONFIDENCE_CONFIG.HIGH_CONFIDENCE_THRESHOLD:
        return "HIGH"

    if score >= CONFIDENCE_CONFIG.MODERATE_CONFIDENCE_THRESHOLD:
        return "MODERATE"

    return "LOW"


# ============================================================
# 1. SEVERITY
# ============================================================

def compute_severity(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute interpretable severity score from incident evidence.

    Severity reflects the scale and consequence of an incident,
    not simply how urgently somebody needs help.

    Factors:
        - Casualties
        - Displacement
        - Infrastructure damage
        - Rescue/trapped persons
        - Disaster type context

    Returns:
        {
            "severity_score": float,
            "severity_level": str,
            "severity_factors": list
        }
    """

    factors = []
    total_score = 0.0

    # ---------------------------------------------------------
    # 1. CASUALTIES
    # ---------------------------------------------------------

    casualties = record.get("casualties", []) or []

    casualty_numbers = []

    for casualty in casualties:
        casualty_numbers.extend(
            _extract_numbers_from_text(casualty)
        )

    casualty_count = max(
        casualty_numbers,
        default=0
    )

    if casualty_count >= SEVERITY_CONFIG.CASUALTY_HIGH_THRESHOLD:

        casualty_score = 100.0

    elif casualty_count >= SEVERITY_CONFIG.CASUALTY_MODERATE_THRESHOLD:

        casualty_score = (
            50.0
            + (
                (casualty_count - SEVERITY_CONFIG.CASUALTY_MODERATE_THRESHOLD)
                /
                (
                    SEVERITY_CONFIG.CASUALTY_HIGH_THRESHOLD
                    - SEVERITY_CONFIG.CASUALTY_MODERATE_THRESHOLD
                )
            )
            * 50.0
        )

    elif casualty_count >= SEVERITY_CONFIG.CASUALTY_LOW_THRESHOLD:

        casualty_score = (
            25.0
            + (
                (casualty_count - SEVERITY_CONFIG.CASUALTY_LOW_THRESHOLD)
                /
                (
                    SEVERITY_CONFIG.CASUALTY_MODERATE_THRESHOLD
                    - SEVERITY_CONFIG.CASUALTY_LOW_THRESHOLD
                )
            )
            * 25.0
        )

    elif casualties:

        # Qualitative casualty mention
        casualty_score = 20.0

    else:

        casualty_score = 0.0

    contribution = (
        casualty_score
        * SEVERITY_CONFIG.CASUALTY_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "casualties",
        "score": round(casualty_score, 2),
        "weight": SEVERITY_CONFIG.CASUALTY_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            f"{casualty_count} affected"
            if casualty_count
            else f"{len(casualties)} qualitative mentions"
        )
    })

    # ---------------------------------------------------------
    # 2. DISPLACEMENT
    # ---------------------------------------------------------

    displaced = record.get("displaced", []) or []

    displaced_numbers = []

    for item in displaced:
        displaced_numbers.extend(
            _extract_numbers_from_text(item)
        )

    displaced_count = max(
        displaced_numbers,
        default=0
    )

    if displaced_count >= SEVERITY_CONFIG.DISPLACED_HIGH_THRESHOLD:

        displaced_score = 100.0

    elif displaced_count >= SEVERITY_CONFIG.DISPLACED_MODERATE_THRESHOLD:

        displaced_score = (
            50.0
            + (
                (displaced_count - SEVERITY_CONFIG.DISPLACED_MODERATE_THRESHOLD)
                /
                (
                    SEVERITY_CONFIG.DISPLACED_HIGH_THRESHOLD
                    - SEVERITY_CONFIG.DISPLACED_MODERATE_THRESHOLD
                )
            )
            * 50.0
        )

    elif displaced_count >= SEVERITY_CONFIG.DISPLACED_LOW_THRESHOLD:

        displaced_score = (
            25.0
            + (
                (displaced_count - SEVERITY_CONFIG.DISPLACED_LOW_THRESHOLD)
                /
                (
                    SEVERITY_CONFIG.DISPLACED_MODERATE_THRESHOLD
                    - SEVERITY_CONFIG.DISPLACED_LOW_THRESHOLD
                )
            )
            * 25.0
        )

    elif displaced:

        displaced_score = 20.0

    else:

        displaced_score = 0.0

    contribution = (
        displaced_score
        * SEVERITY_CONFIG.DISPLACED_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "displaced",
        "score": round(displaced_score, 2),
        "weight": SEVERITY_CONFIG.DISPLACED_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            f"{displaced_count} displaced"
            if displaced_count
            else f"{len(displaced)} qualitative mentions"
        )
    })

    # ---------------------------------------------------------
    # 3. INFRASTRUCTURE
    # ---------------------------------------------------------

    infrastructure = (
        record.get("infrastructure", [])
        or []
    )

    infra_score = 0.0

    # IMPORTANT:
    # These are lists, not integer counters.
    critical_infra = []
    high_impact_infra = []

    for item in infrastructure:

        item_lower = _normalize_text(item)

        if any(
            keyword in item_lower
            for keyword in SEVERITY_CONFIG.CRITICAL_INFRASTRUCTURE
        ):

            critical_infra.append(item)

        elif any(
            keyword in item_lower
            for keyword in SEVERITY_CONFIG.HIGH_IMPACT_INFRASTRUCTURE
        ):

            high_impact_infra.append(item)

    if critical_infra:

        infra_score = 100.0

    elif high_impact_infra:

        infra_score = 75.0

    elif infrastructure:

        infra_score = 45.0

    contribution = (
        infra_score
        * SEVERITY_CONFIG.INFRASTRUCTURE_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "infrastructure",
        "score": round(infra_score, 2),
        "weight": SEVERITY_CONFIG.INFRASTRUCTURE_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            f"{len(critical_infra)} critical, "
            f"{len(high_impact_infra)} high-impact"
        )
    })

    # ---------------------------------------------------------
    # 4. RESCUE / TRAPPED PERSONS
    # ---------------------------------------------------------

    rescue = record.get("rescue", []) or []

    rescue_text = " ".join(
        _normalize_text(item)
        for item in rescue
    )

    trapped_signals = [
        "trapped",
        "stranded",
        "stuck",
        "drowning",
        "buried",
    ]

    if any(
        signal in rescue_text
        for signal in trapped_signals
    ):

        rescue_score = 90.0

    elif rescue:

        rescue_score = 70.0

    else:

        rescue_score = 0.0

    contribution = (
        rescue_score
        * SEVERITY_CONFIG.RESCUE_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "rescue",
        "score": round(rescue_score, 2),
        "weight": SEVERITY_CONFIG.RESCUE_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            "trapped/person rescue indicators"
            if rescue
            else "none"
        )
    })

    # ---------------------------------------------------------
    # 5. DISASTER TYPE CONTEXT
    # ---------------------------------------------------------

    disaster_type = record.get("disaster_type")

    disaster_modifier = 1.0

    if isinstance(disaster_type, str):

        disaster_modifier = (
            SEVERITY_CONFIG.DISASTER_TYPE_MODIFIERS.get(
                disaster_type.lower(),
                1.0
            )
        )

    elif isinstance(disaster_type, list) and disaster_type:

        disaster_modifier = max(
            SEVERITY_CONFIG.DISASTER_TYPE_MODIFIERS.get(
                str(item).lower(),
                1.0
            )
            for item in disaster_type
        )

    # Convert modifier into a bounded contextual score.
    disaster_score = min(
        max(
            (disaster_modifier - 0.7)
            / 0.6
            * 100.0,
            0.0
        ),
        100.0
    )

    contribution = (
        disaster_score
        * SEVERITY_CONFIG.DISASTER_TYPE_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "disaster_type",
        "score": round(disaster_score, 2),
        "weight": SEVERITY_CONFIG.DISASTER_TYPE_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": str(disaster_type)
    })

    # ---------------------------------------------------------
    # 6. OPERATIONAL SEVERITY FLOORS
    # ---------------------------------------------------------
    #
    # Weighted averages can otherwise dilute very large
    # humanitarian events.
    #
    # Example:
    # 500 displaced people should not accidentally become
    # LOW severity simply because other fields are missing.
    #

    if casualty_count >= SEVERITY_CONFIG.CASUALTY_HIGH_THRESHOLD:

        total_score = max(
            total_score,
            SEVERITY_CONFIG.CASUALTY_HIGH_FLOOR
        )

    if displaced_count >= SEVERITY_CONFIG.DISPLACED_HIGH_THRESHOLD:

        total_score = max(
            total_score,
            SEVERITY_CONFIG.DISPLACEMENT_HIGH_FLOOR
        )

    # IMPORTANT FIX:
    # critical_infra is a list, therefore use:
    #     if critical_infra:
    #
    # NOT:
    #     if critical_infra > 0

    if critical_infra:

        total_score = max(
            total_score,
            SEVERITY_CONFIG.CRITICAL_INFRASTRUCTURE_FLOOR
        )

    # Trapped-person rescue should prevent an extremely low
    # severity classification when life-threatening evidence exists.

    if any(
        signal in rescue_text
        for signal in trapped_signals
    ):

        total_score = max(
            total_score,
            SEVERITY_CONFIG.TRAPPED_RESCUE_FLOOR
        )

    # ---------------------------------------------------------
    # FINAL BOUND
    # ---------------------------------------------------------

    total_score = min(
        max(total_score, 0.0),
        SEVERITY_CONFIG.MAX_SEVERITY
    )

    return {
        "severity_score": round(total_score, 2),
        "severity_level": _get_severity_level(total_score),
        "severity_factors": factors,
    }


# ============================================================
# 2. URGENCY
# ============================================================

def compute_urgency(
    record: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Compute urgency score from time-critical evidence.

    Urgency answers:

        "How quickly might intervention be required?"

    It is intentionally separate from severity.
    """

    factors = []
    total_score = 0.0

    text = _normalize_text(
        record.get("text", "")
    )

    # ---------------------------------------------------------
    # RESCUE
    # ---------------------------------------------------------

    rescue = record.get("rescue", []) or []

    rescue_keywords_found = []

    for item in rescue:

        item_text = _normalize_text(item)

        for keyword in (
            URGENCY_CONFIG.CRITICAL_URGENCY_KEYWORDS
        ):

            if keyword in item_text:
                rescue_keywords_found.append(keyword)

    if rescue_keywords_found:

        rescue_score = 100.0

    elif rescue:

        rescue_score = 70.0

    else:

        rescue_score = 0.0

    contribution = (
        rescue_score
        * URGENCY_CONFIG.RESCUE_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "rescue",
        "score": round(rescue_score, 2),
        "weight": URGENCY_CONFIG.RESCUE_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            rescue_keywords_found
            if rescue_keywords_found
            else f"{len(rescue)} mentions"
        )
    })

    # ---------------------------------------------------------
    # CASUALTY URGENCY
    # ---------------------------------------------------------

    casualties = record.get("casualties", []) or []

    casualty_urgency = 0.0

    if casualties:

        casualty_text = _normalize_text(
            " ".join(
                str(item)
                for item in casualties
            )
        )

        if any(
            word in casualty_text
            for word in [
                "killed",
                "dying",
                "critical",
                "fatal",
                "dead",
            ]
        ):

            casualty_urgency = 80.0

        else:

            casualty_urgency = 50.0

    contribution = (
        casualty_urgency
        * URGENCY_CONFIG.CASUALTY_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "casualty_urgency",
        "score": round(casualty_urgency, 2),
        "weight": URGENCY_CONFIG.CASUALTY_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            f"{len(casualties)} casualty mentions"
        )
    })

    # ---------------------------------------------------------
    # IMMEDIATE DANGER
    # ---------------------------------------------------------

    danger_keywords = []

    for keyword in (
        URGENCY_CONFIG.CRITICAL_URGENCY_KEYWORDS
    ):

        if keyword in text:

            danger_keywords.append(keyword)

    if danger_keywords:

        danger_score = 100.0

    elif any(
        keyword in text
        for keyword in URGENCY_CONFIG.HIGH_URGENCY_KEYWORDS
    ):

        danger_score = 60.0

    else:

        danger_score = 0.0

    contribution = (
        danger_score
        * URGENCY_CONFIG.IMMEDIATE_DANGER_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "immediate_danger",
        "score": round(danger_score, 2),
        "weight": URGENCY_CONFIG.IMMEDIATE_DANGER_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            danger_keywords
            if danger_keywords
            else "none"
        )
    })

    # ---------------------------------------------------------
    # RESOURCE URGENCY
    # ---------------------------------------------------------

    requests = record.get("requests", []) or []

    resource_urgency = 0.0

    if requests:

        request_text = _normalize_text(
            " ".join(
                str(item)
                for item in requests
            )
        )

        if any(
            word in request_text
            for word in [
                "urgent",
                "urgently",
                "immediately",
                "emergency",
                "asap",
            ]
        ):

            resource_urgency = 80.0

        else:

            resource_urgency = 40.0

    contribution = (
        resource_urgency
        * URGENCY_CONFIG.RESOURCE_URGENCY_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "resource_urgency",
        "score": round(resource_urgency, 2),
        "weight": URGENCY_CONFIG.RESOURCE_URGENCY_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            f"{len(requests)} request mentions"
        )
    })

    # ---------------------------------------------------------
    # FINAL
    # ---------------------------------------------------------

    total_score = min(
        max(total_score, 0.0),
        URGENCY_CONFIG.MAX_URGENCY
    )

    return {
        "urgency_score": round(total_score, 2),
        "urgency_level": _get_urgency_level(total_score),
        "urgency_factors": factors,
    }


# ============================================================
# 3. PRIORITY
# ============================================================

def compute_priority(
    severity_result: Dict[str, Any],
    urgency_result: Dict[str, Any],
    confidence_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute operational priority.

    Priority combines:

        60% Severity
        40% Urgency

    Confidence can apply a penalty when evidence is weak.
    """

    severity_score = float(
        severity_result["severity_score"]
    )

    urgency_score = float(
        urgency_result["urgency_score"]
    )

    priority_score = (
        severity_score
        * PRIORITY_CONFIG.SEVERITY_WEIGHT
        +
        urgency_score
        * PRIORITY_CONFIG.URGENCY_WEIGHT
    )

    factors = [
        {
            "factor": "severity",
            "score": severity_score,
            "weight": PRIORITY_CONFIG.SEVERITY_WEIGHT,
            "contribution": round(
                severity_score
                * PRIORITY_CONFIG.SEVERITY_WEIGHT,
                2
            ),
        },
        {
            "factor": "urgency",
            "score": urgency_score,
            "weight": PRIORITY_CONFIG.URGENCY_WEIGHT,
            "contribution": round(
                urgency_score
                * PRIORITY_CONFIG.URGENCY_WEIGHT,
                2
            ),
        },
    ]

    # ---------------------------------------------------------
    # CONFIDENCE ADJUSTMENT
    # ---------------------------------------------------------

    if (
        PRIORITY_CONFIG.CONFIDENCE_ADJUSTMENT
        and confidence_result
    ):

        confidence_score = float(
            confidence_result["confidence_score"]
        )

        if (
            confidence_score
            < CONFIDENCE_CONFIG.MODERATE_CONFIDENCE_THRESHOLD
        ):

            original_priority = priority_score

            priority_score *= (
                PRIORITY_CONFIG.LOW_CONFIDENCE_PENALTY
            )

            factors.append({
                "factor": "confidence_adjustment",
                "score": confidence_score,
                "weight": 0.0,
                "contribution": round(
                    priority_score
                    - original_priority,
                    2
                ),
                "evidence": (
                    "Low confidence penalty applied"
                ),
            })

    priority_score = min(
        max(priority_score, 0.0),
        PRIORITY_CONFIG.MAX_PRIORITY
    )

    return {
        "priority_score": round(priority_score, 2),
        "priority_level": _get_priority_level(
            priority_score
        ),
        "priority_factors": factors,
    }


# ============================================================
# 4. CONFIDENCE
# ============================================================

def compute_confidence(
    record: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Compute evidence/completeness confidence score.

    This is NOT a calibrated probability.

    It represents the amount and quality of structured evidence
    available for the incident.
    """

    factors = []
    total_score = 0.0

    # ---------------------------------------------------------
    # CASUALTY EVIDENCE
    # ---------------------------------------------------------

    casualties = record.get("casualties", []) or []

    casualty_confidence = 0.0

    if casualties:

        has_number = any(
            _extract_numbers_from_text(item)
            for item in casualties
        )

        if has_number:

            casualty_confidence = 100.0

        else:

            casualty_confidence = 60.0

    contribution = (
        casualty_confidence
        * CONFIDENCE_CONFIG.EXPLICIT_CASUALTY_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "explicit_casualty",
        "score": casualty_confidence,
        "weight": CONFIDENCE_CONFIG.EXPLICIT_CASUALTY_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            f"{len(casualties)} mentions, "
            f"explicit number: "
            f"{any(_extract_numbers_from_text(c) for c in casualties)}"
        ),
    })

    # ---------------------------------------------------------
    # LOCATION
    # ---------------------------------------------------------

    location = record.get("location", []) or []

    location_confidence = (
        100.0
        if location
        else 0.0
    )

    contribution = (
        location_confidence
        * CONFIDENCE_CONFIG.EXPLICIT_LOCATION_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "explicit_location",
        "score": location_confidence,
        "weight": CONFIDENCE_CONFIG.EXPLICIT_LOCATION_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            f"{len(location)} location mentions"
        ),
    })

    # ---------------------------------------------------------
    # DISASTER TYPE
    # ---------------------------------------------------------

    disaster_type = record.get(
        "disaster_type"
    )

    disaster_confidence = (
        100.0
        if disaster_type
        else 0.0
    )

    contribution = (
        disaster_confidence
        * CONFIDENCE_CONFIG.EXPLICIT_DISASTER_TYPE_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "explicit_disaster_type",
        "score": disaster_confidence,
        "weight": CONFIDENCE_CONFIG.EXPLICIT_DISASTER_TYPE_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            f"disaster_type: {disaster_type}"
        ),
    })

    # ---------------------------------------------------------
    # RESOURCE REQUEST
    # ---------------------------------------------------------

    requests = record.get(
        "requests",
        []
    ) or []

    request_confidence = (
        80.0
        if requests
        else 0.0
    )

    contribution = (
        request_confidence
        * CONFIDENCE_CONFIG.EXPLICIT_RESOURCE_REQUEST_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "explicit_resource_request",
        "score": request_confidence,
        "weight": CONFIDENCE_CONFIG.EXPLICIT_RESOURCE_REQUEST_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            f"{len(requests)} request mentions"
        ),
    })

    # ---------------------------------------------------------
    # RESCUE
    # ---------------------------------------------------------

    rescue = record.get(
        "rescue",
        []
    ) or []

    rescue_confidence = (
        80.0
        if rescue
        else 0.0
    )

    contribution = (
        rescue_confidence
        * CONFIDENCE_CONFIG.EXPLICIT_RESCUE_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "explicit_rescue",
        "score": rescue_confidence,
        "weight": CONFIDENCE_CONFIG.EXPLICIT_RESCUE_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            f"{len(rescue)} rescue mentions"
        ),
    })

    # ---------------------------------------------------------
    # STRUCTURED EXTRACTION
    # ---------------------------------------------------------

    entities = record.get(
        "entities",
        []
    ) or []

    extraction_confidence = min(
        len(entities) * 5.0,
        100.0
    )

    contribution = (
        extraction_confidence
        * CONFIDENCE_CONFIG.STRUCTURED_EXTRACTION_WEIGHT
    )

    total_score += contribution

    factors.append({
        "factor": "structured_extraction",
        "score": extraction_confidence,
        "weight": CONFIDENCE_CONFIG.STRUCTURED_EXTRACTION_WEIGHT,
        "contribution": round(contribution, 2),
        "evidence": (
            f"{len(entities)} entities extracted"
        ),
    })

    # ---------------------------------------------------------
    # FINAL
    # ---------------------------------------------------------

    total_score = min(
        max(total_score, 0.0),
        CONFIDENCE_CONFIG.MAX_CONFIDENCE
    )

    return {
        "confidence_score": round(total_score, 2),
        "confidence_level": _get_confidence_level(
            total_score
        ),
        "confidence_factors": factors,
    }


# ============================================================
# 5. DETERMINISTIC INCIDENT ID
# ============================================================

def generate_incident_id(
    record: Dict[str, Any]
) -> str:
    """
    Generate deterministic incident ID.

    Same stable incident information produces the same ID.
    """

    location = record.get(
        "location",
        []
    ) or []

    casualties = record.get(
        "casualties",
        []
    ) or []

    stable_fields = {
        "text": record.get(
            "text",
            ""
        ),
        "location": sorted(
            str(item)
            for item in location
        ),
        "disaster_type": record.get(
            "disaster_type"
        ),
        "casualties": sorted(
            str(item)
            for item in casualties
        ),
    }

    stable_string = str(
        sorted(
            stable_fields.items()
        )
    )

    hash_object = hashlib.sha256(
        stable_string.encode("utf-8")
    )

    hash_hex = hash_object.hexdigest()

    return (
        f"INC_{hash_hex[:16].upper()}"
    )