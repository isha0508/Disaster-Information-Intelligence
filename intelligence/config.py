"""
intelligence/config.py
=====================
Phase 5 configuration — scoring weights, thresholds, and constants.
"""

from typing import Dict, Any
from dataclasses import dataclass, field


@dataclass
class SeverityConfig:
    """Severity scoring configuration."""

    MAX_SEVERITY: float = 100.0

    CRITICAL_THRESHOLD: float = 75.0
    HIGH_THRESHOLD: float = 50.0
    MODERATE_THRESHOLD: float = 25.0

    # Evidence weights
    CASUALTY_WEIGHT: float = 0.45
    DISPLACED_WEIGHT: float = 0.30
    INFRASTRUCTURE_WEIGHT: float = 0.15
    RESCUE_WEIGHT: float = 0.08
    DISASTER_TYPE_WEIGHT: float = 0.02

    # Casualties
    CASUALTY_HIGH_THRESHOLD: int = 100
    CASUALTY_MODERATE_THRESHOLD: int = 10
    CASUALTY_LOW_THRESHOLD: int = 1

    # Displacement
    DISPLACED_HIGH_THRESHOLD: int = 1000
    DISPLACED_MODERATE_THRESHOLD: int = 100
    DISPLACED_LOW_THRESHOLD: int = 10

    # Operational severity floors
    CASUALTY_HIGH_FLOOR: float = 60.0
    DISPLACEMENT_HIGH_FLOOR: float = 60.0
    CRITICAL_INFRASTRUCTURE_FLOOR: float = 50.0
    RESCUE_SEVERITY_FLOOR: float = 25.0
    TRAPPED_RESCUE_FLOOR: float = 30.0

    CRITICAL_INFRASTRUCTURE: set = field(default_factory=lambda: {
        "hospital", "dam", "power plant", "nuclear", "airport"
    })

    HIGH_IMPACT_INFRASTRUCTURE: set = field(default_factory=lambda: {
        "bridge", "highway", "school", "power line", "pipeline"
    })

    DISASTER_TYPE_MODIFIERS: Dict[str, float] = field(default_factory=lambda: {
        "earthquake": 1.20,
        "tsunami": 1.30,
        "volcanic eruption": 1.20,
        "hurricane": 1.10,
        "flood": 1.00,
        "fire": 0.90,
        "tornado": 1.10,
        "landslide": 1.00,
        "storm": 0.80,
    })


@dataclass
class UrgencyConfig:
    """Urgency scoring configuration."""

    MAX_URGENCY: float = 100.0

    CRITICAL_THRESHOLD: float = 75.0
    HIGH_THRESHOLD: float = 50.0
    MODERATE_THRESHOLD: float = 25.0

    RESCUE_WEIGHT: float = 0.50
    CASUALTY_WEIGHT: float = 0.25
    IMMEDIATE_DANGER_WEIGHT: float = 0.15
    RESOURCE_URGENCY_WEIGHT: float = 0.10

    # Operational urgency floors
    CRITICAL_RESCUE_FLOOR: float = 80.0
    URGENT_RESOURCE_FLOOR: float = 30.0

    CRITICAL_URGENCY_KEYWORDS: set = field(default_factory=lambda: {
        "trapped", "stranded", "stuck", "drowning", "burning",
        "collapse", "collapsed", "sinking", "immediate",
        "emergency", "critical", "life-threatening",
        "save us", "sos"
    })

    HIGH_URGENCY_KEYWORDS: set = field(default_factory=lambda: {
        "urgent", "urgently", "asap", "immediately",
        "quickly", "need help", "help needed", "rescue"
    })


@dataclass
class PriorityConfig:
    """Operational priority configuration."""

    MAX_PRIORITY: float = 100.0

    CRITICAL_THRESHOLD: float = 75.0
    HIGH_THRESHOLD: float = 50.0
    MODERATE_THRESHOLD: float = 25.0

    SEVERITY_WEIGHT: float = 0.55
    URGENCY_WEIGHT: float = 0.45

    # Operational priority floors
    CRITICAL_URGENCY_PRIORITY_FLOOR: float = 50.0
    HIGH_IMPACT_PRIORITY_FLOOR: float = 50.0

    CONFIDENCE_ADJUSTMENT: bool = True

    # Do not heavily suppress genuinely urgent incidents
    LOW_CONFIDENCE_PENALTY: float = 0.90


@dataclass
class ConfidenceConfig:
    """Evidence strength configuration."""

    MAX_CONFIDENCE: float = 100.0

    EXPLICIT_CASUALTY_WEIGHT: float = 0.25
    EXPLICIT_LOCATION_WEIGHT: float = 0.20
    EXPLICIT_DISASTER_TYPE_WEIGHT: float = 0.15
    EXPLICIT_RESOURCE_REQUEST_WEIGHT: float = 0.15
    EXPLICIT_RESCUE_WEIGHT: float = 0.15
    STRUCTURED_EXTRACTION_WEIGHT: float = 0.10

    HIGH_CONFIDENCE_THRESHOLD: float = 75.0
    MODERATE_CONFIDENCE_THRESHOLD: float = 50.0


@dataclass
class ClusteringConfig:
    """Deduplication and clustering configuration."""

    DUPLICATE_SIMILARITY_THRESHOLD: float = 0.85
    CLUSTER_ENTITY_OVERLAP_THRESHOLD: float = 0.5
    LOCATION_MATCH_THRESHOLD: float = 0.7
    MIN_CLUSTER_SIZE: int = 2


@dataclass
class ResourceConfig:
    """Resource estimation configuration."""

    RESOURCE_CATEGORIES: set = field(default_factory=lambda: {
        "food", "water", "medical", "shelter",
        "rescue_teams", "blankets", "medicine",
        "first_aid", "generators"
    })

    FOOD_PER_CASUALTY: float = 1.5
    WATER_PER_CASUALTY: float = 3.0
    MEDICAL_PER_CASUALTY: float = 0.3

    SHELTER_PER_DISPLACED: float = 0.8
    FOOD_PER_DISPLACED: float = 2.0
    WATER_PER_DISPLACED: float = 4.0


SEVERITY_CONFIG = SeverityConfig()
URGENCY_CONFIG = UrgencyConfig()
PRIORITY_CONFIG = PriorityConfig()
CONFIDENCE_CONFIG = ConfidenceConfig()
CLUSTERING_CONFIG = ClusteringConfig()
RESOURCE_CONFIG = ResourceConfig()


def get_all_configs() -> Dict[str, Any]:
    return {
        "severity": SEVERITY_CONFIG,
        "urgency": URGENCY_CONFIG,
        "priority": PRIORITY_CONFIG,
        "confidence": CONFIDENCE_CONFIG,
        "clustering": CLUSTERING_CONFIG,
        "resources": RESOURCE_CONFIG,
    }