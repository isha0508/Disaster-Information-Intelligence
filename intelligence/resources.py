"""
intelligence/resources.py
==========================
Phase 5 resource estimation for incident intelligence.

Implements conservative heuristic resource estimation based on extracted
casualty counts, displaced counts, and explicit resource requests.

Distinguishes between explicit requests (what people asked for) and
estimated needs (what they likely need based on incident scale).
"""

import re
from typing import Dict, List, Any

from intelligence.config import RESOURCE_CONFIG


def _extract_number_from_text(text: str) -> int:
    """Extract the first number from text, return 0 if not found."""
    match = re.search(r'\d{1,6}', text)
    return int(match.group()) if match else 0


def _normalize_text(text: str) -> str:
    """Normalize text for comparison."""
    return re.sub(r'\s+', ' ', str(text).lower()).strip()


def estimate_resource_needs(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Estimate resource needs based on incident evidence.
    
    Distinguishes between:
    - explicit_requests: what was directly requested in the text
    - estimated_needs: heuristic estimates based on incident scale
    
    Returns dict with both explicit and estimated resource information.
    """
    explicit_requests = _extract_explicit_requests(record)
    estimated_needs = _compute_heuristic_estimates(record)
    
    return {
        "explicit_requests": explicit_requests,
        "estimated_needs": estimated_needs,
        "resource_summary": _summarize_resources(explicit_requests, estimated_needs)
    }


def _extract_explicit_requests(record: Dict[str, Any]) -> Dict[str, Any]:
    """Extract explicit resource requests from the incident record."""
    requests = record.get("requests", [])
    resources = record.get("resources", [])
    
    explicit_items = []
    
    # Process request entities
    for req in requests:
        req_lower = _normalize_text(req)
        for category in RESOURCE_CONFIG.RESOURCE_CATEGORIES:
            if category in req_lower:
                # Try to extract quantity
                quantity = _extract_number_from_text(req)
                explicit_items.append({
                    "category": category,
                    "quantity": quantity if quantity > 0 else None,
                    "raw_text": req,
                    "source": "request_entity"
                })
    
    # Process resource entities
    for res in resources:
        res_lower = _normalize_text(res)
        for category in RESOURCE_CONFIG.RESOURCE_CATEGORIES:
            if category in res_lower:
                quantity = _extract_number_from_text(res)
                explicit_items.append({
                    "category": category,
                    "quantity": quantity if quantity > 0 else None,
                    "raw_text": res,
                    "source": "resource_entity"
                })
    
    # Group by category
    categorized = {}
    for item in explicit_items:
        cat = item["category"]
        if cat not in categorized:
            categorized[cat] = []
        categorized[cat].append(item)
    
    return {
        "total_requests": len(explicit_items),
        "by_category": categorized,
        "raw_mentions": explicit_items
    }


def _compute_heuristic_estimates(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute heuristic resource estimates based on incident scale.
    
    Uses conservative multipliers based on casualty and displacement counts.
    Clearly labeled as heuristic estimates, not exact requirements.
    """
    # Extract casualty count
    casualties = record.get("casualties", [])
    casualty_count = 0
    for casualty in casualties:
        num = _extract_number_from_text(casualty)
        casualty_count = max(casualty_count, num)
    
    # Extract displaced count
    displaced = record.get("displaced", [])
    displaced_count = 0
    for disp in displaced:
        num = _extract_number_from_text(disp)
        displaced_count = max(displaced_count, num)
    
    # If no explicit counts, use presence of entities as minimum signal
    if casualty_count == 0 and casualties:
        casualty_count = 1  # Conservative minimum
    if displaced_count == 0 and displaced:
        displaced_count = 5  # Conservative minimum
    
    # Compute estimates using configured multipliers
    estimates = {
        "based_on_casualties": {
            "input_casualty_count": casualty_count,
            "food_meals": round(casualty_count * RESOURCE_CONFIG.FOOD_PER_CASUALTY, 1),
            "water_liters": round(casualty_count * RESOURCE_CONFIG.WATER_PER_CASUALTY, 1),
            "medical_kits": round(casualty_count * RESOURCE_CONFIG.MEDICAL_PER_CASUALTY, 1),
        },
        "based_on_displaced": {
            "input_displaced_count": displaced_count,
            "shelter_spaces": round(displaced_count * RESOURCE_CONFIG.SHELTER_PER_DISPLACED, 1),
            "food_meals_daily": round(displaced_count * RESOURCE_CONFIG.FOOD_PER_DISPLACED, 1),
            "water_liters_daily": round(displaced_count * RESOURCE_CONFIG.WATER_PER_DISPLACED, 1),
        }
    }
    
    # Rescue team estimation based on rescue mentions
    rescue = record.get("rescue", [])
    rescue_teams_needed = 0
    if rescue:
        # Base estimate on rescue mention count and casualty count
        rescue_teams_needed = max(1, min(10, (casualty_count // 10) + len(rescue)))
    
    estimates["rescue_teams"] = {
        "teams_needed": rescue_teams_needed,
        "based_on": f"{len(rescue)} rescue mentions, {casualty_count} casualties"
    }
    
    # Add metadata
    estimates["metadata"] = {
        "method": "heuristic_multipliers",
        "confidence": "low" if (casualty_count < 10 and displaced_count < 50) else "moderate",
        "disclaimer": "Heuristic estimates for planning purposes only. Actual needs may vary significantly."
    }
    
    return estimates


def _summarize_resources(explicit: Dict[str, Any], estimated: Dict[str, Any]) -> Dict[str, Any]:
    """Create a summary of resource information."""
    summary = {
        "has_explicit_requests": explicit["total_requests"] > 0,
        "explicit_categories": list(explicit["by_category"].keys()),
        "has_estimates": True,
        "estimation_basis": []
    }
    
    if estimated["based_on_casualties"]["input_casualty_count"] > 0:
        summary["estimation_basis"].append("casualties")
    
    if estimated["based_on_displaced"]["input_displaced_count"] > 0:
        summary["estimation_basis"].append("displaced")
    
    if estimated["rescue_teams"]["teams_needed"] > 0:
        summary["estimation_basis"].append("rescue_operations")
    
    return summary


def prioritize_resources(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Prioritize resource needs based on urgency and severity signals.
    
    Returns ordered list of resource categories with priority levels.
    """
    resource_needs = estimate_resource_needs(record)
    
    # Get urgency and severity indicators from record if available
    urgency = record.get("urgency_level", "UNKNOWN")
    severity = record.get("severity_level", "UNKNOWN")
    
    # Base priority categories
    priority_categories = []
    
    # Rescue always top priority if mentioned
    if record.get("rescue"):
        priority_categories.append({
            "category": "rescue_teams",
            "priority": "CRITICAL",
            "reason": "Active rescue operations required"
        })
    
    # Medical priority based on casualties
    if record.get("casualties"):
        med_priority = "CRITICAL" if severity in ["CRITICAL", "HIGH"] else "HIGH"
        priority_categories.append({
            "category": "medical",
            "priority": med_priority,
            "reason": f"Casualty treatment required (severity: {severity})"
        })
    
    # Water and food based on displacement and urgency
    if record.get("displaced") or record.get("requests"):
        water_priority = "HIGH" if urgency in ["CRITICAL", "HIGH"] else "MODERATE"
        priority_categories.append({
            "category": "water",
            "priority": water_priority,
            "reason": f"Basic life support (urgency: {urgency})"
        })
        
        priority_categories.append({
            "category": "food",
            "priority": "MODERATE",
            "reason": "Sustenance for affected population"
        })
    
    # Shelter for displaced
    if record.get("displaced"):
        priority_categories.append({
            "category": "shelter",
            "priority": "MODERATE",
            "reason": "Temporary housing for displaced persons"
        })
    
    return priority_categories
