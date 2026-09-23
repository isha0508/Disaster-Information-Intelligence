"""
intelligence/
============
Phase 5 — Incident Intelligence & Decision Support Layer

This package transforms Phase 4 structured incident records into enriched
operational intelligence records containing severity, urgency, priority,
evidence indicators, deduplication/clustering information, resource estimates,
and decision-support flags.

All scoring is rule-based and interpretable. No supervised ML models are used
due to the absence of labeled severity/urgency datasets.

Public API
----------
    from intelligence import enrich_incident, enrich_incidents
    
    enriched = enrich_incident(phase4_record)
    # Returns enriched incident with severity, urgency, priority, etc.
    
    batch_enriched = enrich_incidents(phase4_records)
    # Batch processing of multiple incidents
"""

from intelligence.incident import enrich_incident, enrich_incidents
from intelligence.scoring import (
    compute_severity,
    compute_urgency,
    compute_priority,
    compute_confidence,
)
from intelligence.clustering import (
    detect_duplicates,
    cluster_incidents,
)
from intelligence.resources import estimate_resource_needs

__all__ = [
    "enrich_incident",
    "enrich_incidents",
    "compute_severity",
    "compute_urgency",
    "compute_priority",
    "compute_confidence",
    "detect_duplicates",
    "cluster_incidents",
    "estimate_resource_needs",
]
