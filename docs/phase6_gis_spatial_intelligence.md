# Phase 6 — GIS & Spatial Intelligence

## Objective

Phase 6 adds spatial context to Phase 4 structured and Phase 5 enriched incident records. It preserves existing record fields and supplies normalized location mentions, optional coordinates, geographic clusters, interpretable hotspot summaries, geographic priority rankings, and GeoJSON point output.

## Architecture

- `gis/normalization.py` conservatively trims and whitespace-normalizes location mentions, removes duplicate mentions case-insensitively, and retains each original string.
- `gis/geocoding.py` defines a provider interface, an exact-match offline gazetteer geocoder, and a pending-provider placeholder.
- `gis/schemas.py` validates WGS84 coordinates and defines explicit analysis configuration.
- `gis/spatial.py` calculates Haversine distance and radius-connected spatial clusters.
- `gis/hotspots.py` aggregates cluster evidence and ranks spatial areas.
- `gis/pipeline.py` optionally applies Phase 5 enrichment to Phase 4 records, enriches locations, clusters, detects hotspots, and returns GeoJSON.

Public entry point: `gis.run_spatial_pipeline(records, geocoder=..., config=...)`. Records with Phase 5 `incident_id` and `priority_score` are treated as already enriched. To pass Phase 5 records without re-enrichment, the API also accepts `enrich_phase5=False`.

## Location normalization

Phase 4's `build_structured_record` produces `location` as a list of strings; `entities` are dictionaries with `text`, offsets, `label`, score, and source. Phase 5 preserves `location`, `entities`, and the operational fields while adding IDs, scoring fields, decision flags, estimates, and its text/entity `cluster_id`. Normalization accepts a string, a list/tuple of strings, or text dictionaries. It does not infer geographic names or merge distinct mentions. The record keeps `location` untouched; Phase 6 adds `location_text`, `normalized_location`, and `normalized_locations`. For a point record, the first normalized mention is used and all mentions remain available.

## Geocoding abstraction and coordinate representation

The provider contract returns `latitude`, `longitude`, `status`, and `source`. Supported statuses are `success`, `pending`, `ambiguous`, and `failed`. `OfflineGeocoder` uses only coordinates explicitly supplied by its caller and exact case-insensitive place matching. Missing names fail without a coordinate. `PendingGeocoder` demonstrates deferred/asynchronous integration without making a network request. A future remote adapter must implement its provider's attribution, rate limits, privacy, and usage terms; none is bundled here.

Successful coordinates are numeric WGS84 latitude/longitude degrees (`EPSG:4326`), validated against latitude [-90, 90] and longitude [-180, 180]. GeoJSON coordinate arrays use the required `[longitude, latitude]` order. Invalid or unresolved locations retain null coordinate fields and are excluded from point geometries.

## Distance and spatial clustering

`haversine_distance_km` computes great-circle distances using a mean Earth radius of 6,371.0088 km. It returns `None` if a coordinate is missing or invalid. Spatial clustering uses deterministic radius-connected components: any pair no farther apart than `cluster_radius_km` is connected, and chains can therefore extend beyond the radius end-to-end. Components smaller than `cluster_min_incidents` remain noise with no cluster ID. Missing-coordinate records are marked separately. IDs are hashes of sorted member incident IDs; incident identity and Phase 5 `cluster_id` are untouched.

Defaults: 10 km radius and 2 incident minimum. Configure with `SpatialConfig`.

## Hotspots and spatial priority

Hotspots are spatial clusters meeting `hotspot_min_incidents` (default 2). Each reports counts, average Phase 5 severity/urgency/priority, rescue/casualty signals, and a reason list. The interpretable hotspot score is:

`0.50 × mean priority + 0.25 × mean severity + 0.15 × mean urgency + 0.10 × min(100, 25 × incident_count)`

Spatial area ranking uses Phase 5 priority and incident concentration. Clustered incidents share an area; valid-coordinate noise/singletons each receive their own deterministic point area and can still be ranked:

`(priority_phase5_weight × mean Phase 5 priority + priority_count_weight × min(100, 25 × incident_count)) / (sum of weights)`

Defaults are 0.80 for Phase 5 priority and 0.20 for count. These Phase 6 aggregates do not replace Phase 5 incident scores. All thresholds and weights are configurable through `SpatialConfig`.

## GIS output

`to_geojson` returns a GeoJSON-compatible FeatureCollection of valid incident points. Each feature contains the preserved incident fields as properties; collection metadata identifies EPSG:4326 and includes explicit cluster and hotspot summaries. Cluster centroids are arithmetic means of member point coordinates and are descriptive only. Area-ranking summaries are also returned separately by the pipeline. No GIS GUI dependency is required.

## Configuration

`SpatialConfig` exposes radius, cluster minimum size, hotspot minimum size, and the two area-priority weights. No external geocoder, GeoPandas, or Shapely dependency is required. Applications supply any vetted offline gazetteer or future provider implementation.

## Testing

Run `pytest tests/test_phase6_gis.py -v` and the Phase 5 regression suite `pytest tests/test_phase5_intelligence.py -v`. Tests use mock/offline geocoding and cover normalization, coordinate boundaries, distance, clustering/noise, hotspots, ranking, serialization, malformed inputs, determinism, empty data, and preservation of Phase 5 fields. Run `python evaluation/run_phase6_demonstration.py` to regenerate the synthetic output artifact.

## Limitations

- Geographic accuracy has not been evaluated against gold-standard coordinates.
- The demonstration's coordinates are synthetic/offline fixture inputs; they do not validate a geocoder.
- The initial clustering is radius-connected components, not full DBSCAN density-reachability.
- Hotspots are derived from detected clusters; no polygon boundaries, administrative regions, or population exposure are modeled.
- A multi-location incident is represented by its first normalized mention for point analysis; all mentions are retained in the incident record.
- No live geocoding, monitoring, alerting, dashboard, PostGIS store, or real-time performance work is included.

## Phase 7 and Phase 8 integration

Phase 7 can consume normalized location fields and spatial evidence as structured context while retaining uncertainty statuses. Phase 8 can call the same pipeline incrementally with a provider configured for its operational policies; asynchronous `pending` status is supported. Production ingestion should persist provider/source, query timestamp, and provenance and should not treat mock coordinates as verified locations.
