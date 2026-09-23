"""Deterministic geographic distance and radius-based connected clustering."""

import hashlib
import math

from gis.schemas import SpatialConfig, coordinates_from_record

EARTH_RADIUS_KM = 6371.0088


def haversine_distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between WGS84 latitude/longitude pairs, in km."""
    from gis.schemas import validate_coordinates
    if not validate_coordinates(lat1, lon1)[0] or not validate_coordinates(lat2, lon2)[0]:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, map(float, (lat1, lon1, lat2, lon2)))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def cluster_incidents_spatial(records, config=None):
    """Cluster valid points by radius-connected components; noise stays unclustered.

    Each point within ``cluster_radius_km`` links two incidents, so components
    may extend beyond the radius through chains. IDs derive from sorted member
    incident IDs, making the result deterministic for stable incident IDs.
    """
    config = config or SpatialConfig()
    groups, valid_indices = [], []
    for i, record in enumerate(records):
        lat, _ = coordinates_from_record(record)
        lon = record.get("longitude") if lat is not None else None
        if lat is not None:
            valid_indices.append(i)
    parent = {i: i for i in valid_indices}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for pos, i in enumerate(valid_indices):
        for j in valid_indices[pos + 1:]:
            d = haversine_distance_km(records[i]["latitude"], records[i]["longitude"],
                                      records[j]["latitude"], records[j]["longitude"])
            if d is not None and d <= config.cluster_radius_km:
                union(i, j)
    for i in valid_indices:
        groups.append(find(i))
    members = {}
    for i, root in zip(valid_indices, groups):
        members.setdefault(root, []).append(i)
    components = [v for v in members.values() if len(v) >= config.cluster_min_incidents]
    components.sort(key=lambda indices: tuple(sorted(str(records[i].get("incident_id", i)) for i in indices)))
    assigned = {}
    for component in components:
        identity = "|".join(sorted(str(records[i].get("incident_id", i)) for i in component))
        cluster_id = "SPATIAL_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12].upper()
        for i in component:
            assigned[i] = (cluster_id, len(component))
    output = []
    for i, record in enumerate(records):
        updated = dict(record)
        updated["spatial_cluster_id"], updated["spatial_cluster_size"] = assigned.get(i, (None, 1 if i in valid_indices else 0))
        updated["spatial_cluster_status"] = "clustered" if i in assigned else ("noise" if i in valid_indices else "missing_coordinates")
        if i in assigned:
            updated["spatial_area_id"] = assigned[i][0]
        elif i in valid_indices:
            identity = str(record.get("incident_id", i))
            updated["spatial_area_id"] = "SPATIAL_POINT_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12].upper()
        else:
            updated["spatial_area_id"] = None
        output.append(updated)
    return output
