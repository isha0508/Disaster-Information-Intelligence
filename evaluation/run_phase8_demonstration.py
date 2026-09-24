"""Finite Phase 8 operational monitoring demo using only synthetic events."""

import json
import os
from pathlib import Path
import sys

# Never let the demo fetch a model. Existing local Phase 3 artifacts remain usable.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from monitoring import MonitoringConfig, MonitoringRunner, SyntheticEventSource


def main():
    source = SyntheticEventSource()
    runner = MonitoringRunner(config=MonitoringConfig(polling_interval_seconds=0),)
    cycles = runner.run_monitoring(source, cycles=2, interval_seconds=0)
    records = [record for cycle in cycles for record in cycle]
    counts = {}
    for item in records:
        counts[item["event_state"]] = counts.get(item["event_state"], 0) + 1
    phase3_counts = {}
    for item in records:
        phase_status = item.get("phase_status", {}).get("phase3", "not_run")
        phase3_counts[phase_status] = phase3_counts.get(phase_status, 0) + 1
    artifact = {
        "demonstration": {"name": "Phase 8 operational monitoring", "source_type": "synthetic_demo",
                          "synthetic_data": True, "network_required": False,
                          "claims_real_world_validation": False,
                          "description": "Deterministic finite ingestion fixture; Phase 3 model use is offline-only."},
        "cycles": cycles,
        "summary": {"events_seen": len(records), "event_states": counts,
                    "unique_event_ids": len({r["event_id"] for r in records if r.get("event_id")}),
                    "phase3_statuses": phase3_counts},
    }
    path = Path(__file__).with_name("phase8_demonstration_results.json")
    path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    print("PHASE 8 OPERATIONAL MONITORING DEMONSTRATION (SYNTHETIC DATA)")
    for index, cycle in enumerate(cycles, start=1):
        print(f"\nCycle {index}")
        for record in cycle:
            print(f"{record['event_state']:<9} event={record.get('event_id') or 'unassigned'} "
                  f"status={record['processing_status']} source={record.get('source', 'invalid')}")
    print("\nSummary")
    print(json.dumps(artifact["summary"], indent=2))
    print(f"\nJSON artifact: {path}")
    print("Synthetic data only; this does not validate real-world source quality or model accuracy.")
    return artifact


if __name__ == "__main__":
    main()
