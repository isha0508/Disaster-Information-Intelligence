"""Run a deterministic offline GIS/spatial-intelligence demonstration."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gis import OfflineGeocoder, SpatialConfig, run_spatial_pipeline


# Demo-only fixture coordinates. They are supplied to the offline mock and are
# not claimed to be geocoded or geographically validated by this project.
GAZETTEER = {
    "Cebu North": (10.3157, 123.8854), "Cebu Port": (10.2930, 123.9020),
    "Cebu Clinic": (10.3300, 123.8900),
    "Kathmandu Ward 1": (27.7172, 85.3240), "Kathmandu Hospital": (27.7100, 85.3150),
    "Kathmandu Bridge": (27.7250, 85.3350),
    "Nairobi East": (-1.2864, 36.8172), "Nairobi Clinic": (-1.2921, 36.8219),
    "Nairobi Market": (-1.2800, 36.8300), "Nairobi Shelter": (-1.3000, 36.8100),
    "Quito Hill": (-0.1807, -78.4678),
}


def _incident(text, location, scenario, **fields):
    record = {"text": text, "location": [location], "casualties": [], "displaced": [],
              "requests": [], "resources": [], "rescue": [], "infrastructure": [],
              "disaster_type": "flood", "organizations": [], "persons": [], "numbers": [],
              "entities": [], "demo_scenario": scenario}
    record.update(fields)
    return record


SCENARIOS = [
    _incident("Flooding reported near homes", "Cebu North", "SCENARIO_1_SPATIAL_CLUSTER"),
    _incident("Flood damage at port road", "Cebu Port", "SCENARIO_1_SPATIAL_CLUSTER",
              infrastructure=["road"]),
    _incident("Residents need shelter", "Cebu Clinic", "SCENARIO_1_SPATIAL_CLUSTER",
              requests=["shelter"], resources=["shelter"]),
    _incident("Isolated landslide report", "Quito Hill", "SCENARIO_2_ISOLATED_INCIDENT",
              disaster_type="landslide", casualties=["2 injured"]),
    _incident("People trapped after earthquake", "Kathmandu Ward 1",
              "SCENARIO_3_HIGH_PRIORITY_RESCUE_CASUALTY_CLUSTER",
              disaster_type="earthquake", rescue=["people trapped", "rescue"],
              casualties=["12 injured"]),
    _incident("Casualties near hospital", "Kathmandu Hospital",
              "SCENARIO_3_HIGH_PRIORITY_RESCUE_CASUALTY_CLUSTER",
              disaster_type="earthquake", casualties=["25 injured"]),
    _incident("Bridge collapse, request rescue teams", "Kathmandu Bridge",
              "SCENARIO_3_HIGH_PRIORITY_RESCUE_CASUALTY_CLUSTER",
              disaster_type="earthquake", rescue=["rescue teams"],
              infrastructure=["bridge"], requests=["rescue teams"]),
    _incident("Flood report from unmapped village", "Unknown Village",
              "SCENARIO_4_UNRESOLVED_LOCATION"),
    _incident("Heavy rain causes flooding", "Nairobi East", "SCENARIO_5_GEOGRAPHIC_HOTSPOT"),
    _incident("Medical supplies requested", "Nairobi Clinic", "SCENARIO_5_GEOGRAPHIC_HOTSPOT",
              requests=["medical supplies"], resources=["medical"]),
    _incident("Market area needs evacuation", "Nairobi Market", "SCENARIO_5_GEOGRAPHIC_HOTSPOT",
              rescue=["evacuation"], displaced=["120 displaced"]),
    _incident("Families need shelter after flooding", "Nairobi Shelter",
              "SCENARIO_5_GEOGRAPHIC_HOTSPOT", displaced=["80 displaced"], requests=["shelter"]),
]


def run_demonstration():
    result = run_spatial_pipeline(
        SCENARIOS,
        geocoder=OfflineGeocoder(GAZETTEER, source="synthetic_demo_fixture"),
        config=SpatialConfig(cluster_radius_km=10, cluster_min_incidents=2,
                             hotspot_min_incidents=2),
    )
    rows = []
    for incident in result["incidents"]:
        rows.append({
            "scenario": incident["demo_scenario"],
            "incident_id": incident["incident_id"],
            "original_location": incident["location_text"],
            "normalized_location": incident["normalized_location"],
            "latitude": incident["latitude"], "longitude": incident["longitude"],
            "geocoding_status": incident["geocoding_status"],
            "spatial_cluster_id": incident["spatial_cluster_id"],
            "hotspot_id": next((h["hotspot_id"] for h in result["hotspots"]
                                if h["spatial_cluster_id"] == incident["spatial_cluster_id"]), None),
            "priority_score": incident.get("priority_score", 0),
            "spatial_priority_score": next((p["spatial_priority_score"] for p in result["spatial_priority_ranking"]
                                             if p["spatial_area_id"] == incident["spatial_area_id"]), None),
        })
        print(" | ".join(f"{key}={value}" for key, value in rows[-1].items()))
    artifact = {
        "evaluation_type": "deterministic_rule_algorithm_demonstration",
        "description": "Synthetic offline fixture demonstrating spatial normalization, mock geocoding, clustering, hotspots, and area priority.",
        "geographic_accuracy": "NOT evaluated; fixture coordinates are not evidence of real-world geocoding accuracy.",
        "live_geocoding": False,
        "configuration": result["metadata"],
        "incidents": rows,
        "clusters": result["clusters"],
        "hotspots": result["hotspots"],
        "spatial_priority_ranking": result["spatial_priority_ranking"],
        "geojson": result["geojson"],
        "phase5_fields_preserved": True,
    }
    output = ROOT / "evaluation" / "phase6_demonstration_results.json"
    output.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved Phase 6 demonstration: {output}")
    return artifact


if __name__ == "__main__":
    run_demonstration()
