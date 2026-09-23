"""Phase 6 GIS and spatial intelligence public API."""

from gis.geocoding import Geocoder, OfflineGeocoder, PendingGeocoder
from gis.hotspots import detect_hotspots, rank_spatial_priority
from gis.normalization import normalize_location, normalize_locations
from gis.pipeline import enrich_spatial_record, run_spatial_pipeline, summarize_spatial_clusters, to_geojson
from gis.schemas import SpatialConfig, validate_coordinates
from gis.spatial import cluster_incidents_spatial, haversine_distance_km

__all__ = [
    "Geocoder", "OfflineGeocoder", "PendingGeocoder", "SpatialConfig",
    "normalize_location", "normalize_locations", "validate_coordinates",
    "haversine_distance_km", "cluster_incidents_spatial",
    "detect_hotspots", "rank_spatial_priority", "enrich_spatial_record",
    "to_geojson", "summarize_spatial_clusters", "run_spatial_pipeline",
]
