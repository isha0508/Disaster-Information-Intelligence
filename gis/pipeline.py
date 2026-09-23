"""Composable Phase 4/5 to GIS-compatible Phase 6 pipeline."""

import copy

from gis.geocoding import OfflineGeocoder
from gis.hotspots import detect_hotspots, rank_spatial_priority
from gis.normalization import normalize_location
from gis.schemas import SpatialConfig, validate_coordinates
from gis.spatial import cluster_incidents_spatial


def enrich_spatial_record(record, geocoder=None):
    """Add spatial fields to one Phase 4/5 record, retaining every input field."""
    geocoder = geocoder or OfflineGeocoder()
    enriched = copy.deepcopy(record)
    normalized = normalize_location(record.get("location", record.get("location_text")))
    first = normalized[0] if normalized else None
    location_text = first["original_text"] if first else None
    result = {"latitude": None, "longitude": None, "status": "failed", "source": getattr(geocoder, "source", "unknown")}
    if first:
        try:
            result = geocoder.geocode(first["normalized_text"])
        except Exception as exc:
            result = {"latitude": None, "longitude": None, "status": "failed",
                      "source": getattr(geocoder, "source", geocoder.__class__.__name__),
                      "error": type(exc).__name__}
    if not isinstance(result, dict):
        result = {"latitude": None, "longitude": None, "status": "failed",
                  "source": getattr(geocoder, "source", geocoder.__class__.__name__),
                  "error": "invalid_provider_response"}
    status = result.get("status", "failed")
    latitude, longitude = result.get("latitude"), result.get("longitude")
    valid, coordinate_error = validate_coordinates(latitude, longitude)
    if status == "success" and not valid:
        status, latitude, longitude = "failed", None, None
    elif status != "success":
        latitude, longitude = None, None
    else:
        latitude, longitude = float(latitude), float(longitude)
    enriched.update({
        "location_text": location_text,
        "normalized_location": first["normalized_text"] if first else None,
        "normalized_locations": normalized,
        "latitude": latitude,
        "longitude": longitude,
        "geocoding_status": status if status in {"success", "pending", "ambiguous", "failed"} else "failed",
        "geocoding_source": result.get("source", geocoder.__class__.__name__),
        "spatial_metadata": {
            "coordinate_reference_system": "EPSG:4326",
            "coordinate_order": ["latitude", "longitude"],
            "coordinate_validation": "valid" if valid and status == "success" else coordinate_error,
            "geocoding_candidates": result.get("candidates"),
            "geocoding_error": result.get("error"),
        },
    })
    return enriched


def to_geojson(records, hotspots=None, clusters=None):
    """Serialize valid incident coordinates as a GeoJSON FeatureCollection."""
    features = []
    for record in records:
        valid, _ = validate_coordinates(record.get("latitude"), record.get("longitude"))
        if not valid:
            continue
        properties = {key: value for key, value in record.items() if key not in {"latitude", "longitude"}}
        features.append({"type": "Feature", "geometry": {"type": "Point",
                         "coordinates": [float(record["longitude"]), float(record["latitude"])]},
                         "properties": properties})
    return {"type": "FeatureCollection", "features": features,
            "metadata": {"coordinate_reference_system": "EPSG:4326",
                         "feature_type": "incident_points",
                         "clusters": clusters or [], "hotspots": hotspots or []}}


def summarize_spatial_clusters(records):
    """Return explicit cluster summaries alongside per-incident cluster IDs."""
    groups = {}
    for record in records:
        cluster_id = record.get("spatial_cluster_id")
        if cluster_id:
            groups.setdefault(cluster_id, []).append(record)
    summaries = []
    for cluster_id, members in sorted(groups.items()):
        summaries.append({
            "spatial_cluster_id": cluster_id,
            "incident_count": len(members),
            "incident_ids": sorted(str(r.get("incident_id", "")) for r in members),
            "centroid": {
                "latitude": sum(float(r["latitude"]) for r in members) / len(members),
                "longitude": sum(float(r["longitude"]) for r in members) / len(members),
                "method": "arithmetic_mean_of_point_coordinates",
            },
        })
    return summaries


def run_spatial_pipeline(records, geocoder=None, config=None, enrich_phase5=True):
    """Run optional Phase 5 enrichment followed by spatial enrichment/analysis.

    Already enriched Phase 5 records are accepted unchanged as input. For
    Phase 4 records, Phase 5 enrichment is performed by default.
    """
    config = config or SpatialConfig()
    records = list(records or [])
    if enrich_phase5 and records and not all("incident_id" in r and "priority_score" in r for r in records):
        from intelligence import enrich_incidents
        source_records = records
        records = enrich_incidents(records)
        # Phase 5 intentionally emits its public schema. Carry through caller
        # metadata (for example an ingestion/source label) without overriding
        # any canonical Phase 5 fields.
        for source, enriched in zip(source_records, records):
            for key, value in source.items():
                if key not in enriched:
                    enriched[key] = value
    spatial_records = [enrich_spatial_record(record, geocoder) for record in records]
    spatial_records = cluster_incidents_spatial(spatial_records, config)
    clusters = summarize_spatial_clusters(spatial_records)
    hotspots = detect_hotspots(spatial_records, config)
    priority_areas = rank_spatial_priority(spatial_records, config)
    return {"incidents": spatial_records, "clusters": clusters, "hotspots": hotspots,
            "spatial_priority_ranking": priority_areas,
            "geojson": to_geojson(spatial_records, hotspots, clusters),
            "metadata": {"algorithm": "haversine_radius_connected_components",
                         "cluster_radius_km": config.cluster_radius_km,
                         "cluster_min_incidents": config.cluster_min_incidents,
                         "hotspot_min_incidents": config.hotspot_min_incidents,
                         "coordinate_reference_system": "EPSG:4326"}}
