"""Build and validate the deterministic Phase 10 dashboard demonstration."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.data import build_demo_dashboard_data, build_demonstration_artifact


OUTPUT = ROOT / "evaluation" / "phase10_demonstration_results.json"


def main():
    data = build_demo_dashboard_data(ROOT)
    artifact = build_demonstration_artifact(data)
    incidents = artifact["incident_summary"]
    assert data["dataset_mode"] == "synthetic_demo"
    assert any(row.get("priority_level") in {"HIGH", "CRITICAL"} for row in incidents)
    assert any(alert.get("alert_level") in {"HIGH", "CRITICAL"} for alert in artifact["alert_summary"])
    assert artifact["alert_summary"], "Phase 9 alert states should be represented"
    assert any(row.get("event_state") == "DUPLICATE" for row in incidents)
    assert any(row.get("event_state") == "UPDATED" for row in incidents)
    assert any(row.get("geocoding_status") not in (None, "success") for row in incidents)
    assert artifact["spatial_summary"]["valid_coordinate_incidents"] > 0
    assert artifact["spatial_summary"]["hotspot_incident_count"] > 0
    assert artifact["intelligence_summary"]["uncertainty_count"] > 0
    assert artifact["intelligence_summary"]["grounding_warning_count"] > 0
    OUTPUT.write_text(json.dumps(artifact, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print("Phase 10 deterministic dashboard demonstration")
    print(f"Mode: {artifact['mode']} (synthetic; not live data)")
    for key, value in artifact["dashboard_summary"].items():
        if isinstance(value, (int, float)):
            print(f"{key}: {value}")
    print(f"Valid coordinate points: {artifact['spatial_summary']['valid_coordinate_incidents']}")
    print(f"Monitoring events: {artifact['monitoring_summary']['total_events']}")
    print(f"Phase 9 alert evaluations: {len(artifact['alert_summary'])}")
    print(f"Results: {OUTPUT}")


if __name__ == "__main__":
    main()
