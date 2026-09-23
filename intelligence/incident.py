"""
intelligence/incident.py
=======================
Phase 5 incident enrichment — main public API.

Transforms Phase 4 structured incident records into enriched operational
intelligence records containing severity, urgency, priority, evidence
indicators, deduplication/clustering information, resource estimates,
and decision-support flags.
"""

from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

from intelligence.scoring import (
    compute_severity,
    compute_urgency,
    compute_priority,
    compute_confidence,
    generate_incident_id,
)
from intelligence.clustering import (
    detect_duplicates,
    cluster_incidents,
)
from intelligence.resources import (
    estimate_resource_needs,
    prioritize_resources,
)


def _generate_decision_flags(record: Dict[str, Any],
                            severity_result: Dict[str, Any],
                            urgency_result: Dict[str, Any],
                            priority_result: Dict[str, Any],
                            confidence_result: Dict[str, Any]) -> List[str]:
    """Generate machine-readable decision-support flags."""
    flags = []
    
    # Priority flags
    if priority_result["priority_level"] == "CRITICAL":
        flags.append("CRITICAL_PRIORITY")
    elif priority_result["priority_level"] == "HIGH":
        flags.append("HIGH_PRIORITY")
    
    # Urgency flags
    if urgency_result["urgency_level"] == "CRITICAL":
        flags.append("IMMEDIATE_RESCUE")
    
    # Specific incident type flags
    if record.get("casualties"):
        flags.append("CASUALTY_REPORT")
    
    if record.get("displaced"):
        if len(record.get("displaced", [])) > 5 or any(
            "1000" in d or "thousand" in d.lower() for d in record.get("displaced", [])
        ):
            flags.append("MASS_DISPLACEMENT")
    
    if record.get("infrastructure"):
        flags.append("INFRASTRUCTURE_DAMAGE")
    
    if record.get("rescue"):
        flags.append("RESCUE_OPERATION")
    
    # Resource urgency flags
    if record.get("requests"):
        if urgency_result["urgency_level"] in ["HIGH", "CRITICAL"]:
            flags.append("URGENT_RESOURCE_NEED")
        else:
            flags.append("RESOURCE_REQUEST")
    
    # Location flag
    if record.get("location"):
        flags.append("LOCATION_IDENTIFIED")
    
    # Evidence quality flag
    if confidence_result["confidence_level"] == "LOW":
        flags.append("LOW_EVIDENCE")
    
    # Duplicate flag (if already computed)
    if record.get("is_duplicate"):
        flags.append("POTENTIAL_DUPLICATE")
    
    # Preserve the order in which rules produced flags while ensuring a
    # flag is represented only once in the public decision-support output.
    return list(dict.fromkeys(flags))


def enrich_incident(record: Dict[str, Any],
                   existing_records: Optional[List[Dict[str, Any]]] = None,
                   phase3_classifications: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Enrich a single Phase 4 incident record with Phase 5 intelligence.
    
    Parameters
    ----------
    record : dict
        Phase 4 structured incident record from nlp.extractor.build_structured_record()
    existing_records : list of dict, optional
        Existing incident database for duplicate detection
    phase3_classifications : dict, optional
        Optional Phase 3 classification outputs (relevance, humanitarian, disaster_type)
    
    Returns
    -------
    dict — Enriched incident record with Phase 5 fields added
    """
    # Create enriched record with default fields for missing ones
    enriched = {
        "text": record.get("text", ""),
        "location": record.get("location", []),
        "casualties": record.get("casualties", []),
        "displaced": record.get("displaced", []),
        "rescue": record.get("rescue", []),
        "infrastructure": record.get("infrastructure", []),
        "disaster_type": record.get("disaster_type"),
        "requests": record.get("requests", []),
        "resources": record.get("resources", []),
        "organizations": record.get("organizations", []),
        "persons": record.get("persons", []),
        "numbers": record.get("numbers", []),
        "entities": record.get("entities", []),
        "request_resource_links": record.get("request_resource_links", []),
    }
    
    # Generate incident ID
    incident_id = generate_incident_id(record)
    enriched["incident_id"] = incident_id
    
    # Compute severity
    severity_result = compute_severity(record)
    enriched["severity_score"] = severity_result["severity_score"]
    enriched["severity_level"] = severity_result["severity_level"]
    enriched["severity_factors"] = severity_result["severity_factors"]
    
    # Compute urgency
    urgency_result = compute_urgency(record)
    enriched["urgency_score"] = urgency_result["urgency_score"]
    enriched["urgency_level"] = urgency_result["urgency_level"]
    enriched["urgency_factors"] = urgency_result["urgency_factors"]
    
    # Compute confidence
    confidence_result = compute_confidence(record)
    enriched["confidence_score"] = confidence_result["confidence_score"]
    enriched["confidence_level"] = confidence_result["confidence_level"]
    enriched["confidence_factors"] = confidence_result["confidence_factors"]
    
    # Compute priority (combines severity, urgency, confidence)
    priority_result = compute_priority(severity_result, urgency_result, confidence_result)
    enriched["priority_score"] = priority_result["priority_score"]
    enriched["priority_level"] = priority_result["priority_level"]
    enriched["priority_factors"] = priority_result["priority_factors"]
    
    # Resource estimation
    resource_result = estimate_resource_needs(record)
    enriched["resource_estimates"] = resource_result
    enriched["resource_priorities"] = prioritize_resources(record)
    
    # Duplicate detection (if existing records provided)
    if existing_records:
        duplicate_result = detect_duplicates(record, existing_records)
        enriched["is_duplicate"] = duplicate_result["is_duplicate"]
        enriched["duplicate_of"] = duplicate_result["duplicate_of"]
        enriched["similarity_scores"] = duplicate_result["similarity_scores"]
    else:
        enriched["is_duplicate"] = False
        enriched["duplicate_of"] = None
        enriched["similarity_scores"] = {}
    
    # Generate decision flags
    decision_flags = _generate_decision_flags(
        record, severity_result, urgency_result, priority_result, confidence_result
    )
    enriched["decision_flags"] = decision_flags
    
    # Add Phase 3 classifications if provided
    if phase3_classifications:
        enriched["phase3_classifications"] = phase3_classifications
    
    # Processing metadata
    enriched["processing_metadata"] = {
        "enrichment_timestamp": datetime.now(timezone.utc).isoformat(),
        "phase5_version": "1.0",
        "scoring_method": "rule_based_interpretable",
        "has_phase3_input": phase3_classifications is not None
    }
    
    return enriched


def enrich_incidents(records: List[Dict[str, Any]],
                    phase3_classifications: Optional[List[Dict[str, Any]]] = None,
                    enable_clustering: bool = True) -> List[Dict[str, Any]]:
    """
    Enrich multiple Phase 4 incident records with Phase 5 intelligence.
    
    Parameters
    ----------
    records : list of dict
        Phase 4 structured incident records
    phase3_classifications : list of dict, optional
        Optional Phase 3 classification outputs (one per record)
    enable_clustering : bool
        Whether to perform incident clustering (default: True)
    
    Returns
    -------
    list of dict — Enriched incident records with clustering information
    """
    if not records:
        return []
    
    # Enrich individual records
    enriched_records = []
    for i, record in enumerate(records):
        # Get corresponding Phase 3 classifications if available
        p3_class = phase3_classifications[i] if phase3_classifications and i < len(phase3_classifications) else None
        
        # For duplicate detection, use previously enriched records
        existing_for_duplication = enriched_records if enriched_records else None
        
        enriched = enrich_incident(record, existing_for_duplication, p3_class)
        enriched_records.append(enriched)
    
    # Perform clustering if enabled
    if enable_clustering:
        clustered_records = cluster_incidents(enriched_records)
        return clustered_records
    
    return enriched_records


def create_incident_summary(enriched_record: Dict[str, Any]) -> str:
    """
    Create a human-readable summary of an enriched incident.
    
    Returns a concise text summary suitable for dashboard display or
    operational review.
    """
    lines = [
        f"Incident ID: {enriched_record.get('incident_id', 'UNKNOWN')}",
        f"Priority: {enriched_record.get('priority_level', 'UNKNOWN')} ({enriched_record.get('priority_score', 0):.1f}/100)",
        f"Severity: {enriched_record.get('severity_level', 'UNKNOWN')} ({enriched_record.get('severity_score', 0):.1f}/100)",
        f"Urgency: {enriched_record.get('urgency_level', 'UNKNOWN')} ({enriched_record.get('urgency_score', 0):.1f}/100)",
        f"Confidence: {enriched_record.get('confidence_level', 'UNKNOWN')} ({enriched_record.get('confidence_score', 0):.1f}/100)",
    ]
    
    # Add key flags
    flags = enriched_record.get('decision_flags', [])
    if flags:
        lines.append(f"Flags: {', '.join(flags[:5])}")  # Show first 5 flags
    
    # Add disaster type if available
    disaster_type = enriched_record.get('disaster_type')
    if disaster_type:
        lines.append(f"Disaster Type: {disaster_type}")
    
    # Add location if available
    location = enriched_record.get('location', [])
    if location:
        lines.append(f"Location: {', '.join(location[:3])}")  # Show first 3 locations
    
    # Add casualty info if available
    casualties = enriched_record.get('casualties', [])
    if casualties:
        lines.append(f"Casualties: {len(casualties)} mentions")
    
    # Add cluster info if available
    cluster_id = enriched_record.get('cluster_id')
    if cluster_id:
        cluster_size = enriched_record.get('cluster_size', 1)
        lines.append(f"Cluster: {cluster_id} ({cluster_size} reports)")
    
    return " | ".join(lines)


def filter_by_priority(records: List[Dict[str, Any]], 
                      min_priority_level: str = "HIGH") -> List[Dict[str, Any]]:
    """
    Filter incidents by minimum priority level.
    
    Priority levels: LOW < MODERATE < HIGH < CRITICAL
    """
    priority_order = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "CRITICAL": 3}
    min_level = priority_order.get(min_priority_level, 0)
    
    filtered = [
        record for record in records
        if priority_order.get(record.get("priority_level", "LOW"), 0) >= min_level
    ]
    
    return filtered


def filter_by_flags(records: List[Dict[str, Any]], 
                   required_flags: List[str]) -> List[Dict[str, Any]]:
    """
    Filter incidents that have all required decision flags.
    """
    filtered = [
        record for record in records
        if all(flag in record.get("decision_flags", []) for flag in required_flags)
    ]
    
    return filtered
