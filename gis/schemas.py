"""Shared configuration and validation for Phase 6 spatial analysis."""

from dataclasses import dataclass
from typing import Optional, Tuple


GEOCODING_STATUSES = {"success", "pending", "ambiguous", "failed"}


def validate_coordinates(latitude, longitude) -> Tuple[bool, Optional[str]]:
    """Return validity and a reason; coordinates use WGS84 degrees."""
    if latitude is None or longitude is None:
        return False, "missing_coordinates"
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return False, "coordinates_must_be_numeric"
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return False, "coordinates_must_be_numeric"
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return False, "coordinates_out_of_range"
    return True, None


@dataclass(frozen=True)
class SpatialConfig:
    """Interpretable spatial-analysis settings; distances are in kilometers."""
    cluster_radius_km: float = 10.0
    cluster_min_incidents: int = 2
    hotspot_min_incidents: int = 2
    priority_count_weight: float = 0.20
    priority_phase5_weight: float = 0.80

    def __post_init__(self):
        if self.cluster_radius_km <= 0:
            raise ValueError("cluster_radius_km must be positive")
        if self.cluster_min_incidents < 1 or self.hotspot_min_incidents < 1:
            raise ValueError("minimum incident counts must be at least one")
        if self.priority_count_weight < 0 or self.priority_phase5_weight < 0:
            raise ValueError("priority weights must be non-negative")
        if self.priority_count_weight + self.priority_phase5_weight <= 0:
            raise ValueError("at least one priority weight must be positive")


def coordinates_from_record(record):
    """Validate and return a coordinate pair from a geocoded record."""
    valid, reason = validate_coordinates(record.get("latitude"), record.get("longitude"))
    return (float(record["latitude"]), float(record["longitude"])) if valid else (None, reason)
