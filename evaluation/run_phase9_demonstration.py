"""Offline deterministic demonstration of Phase 9 alerting and escalation."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alerting import AlertConfig, AlertEvaluator, MockNotificationProvider


class DemoClock:
    """Fixed clock that advances one deterministic second per evaluation."""
    def __init__(self):
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def __call__(self):
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def make_event(event_id, incident_id, priority, severity, urgency, flags=None,
               event_state="NEW", phase6=None, confidence=70):
    return {
        "event_id": event_id, "event_state": event_state,
        "source": "synthetic_phase9_demo", "source_type": "synthetic",
        "provenance": {"source": "synthetic_phase9_demo", "source_event_id": event_id,
                       "source_type": "synthetic"},
        "intelligence": {
            "phase5": {"incident_id": incident_id, "priority_score": priority,
                       "severity_score": severity, "urgency_score": urgency,
                       "confidence_score": confidence, "confidence_level": "MODERATE",
                       "decision_flags": flags or [], "requests": [],
                       "resource_estimates": {"explicit_requests": {"raw_mentions": []}}},
            "phase6": phase6 or {},
            "phase7": {"summary": "Synthetic grounded demonstration context",
                       "uncertainties": ["Synthetic fixture; not a verified field report."]},
        },
    }


def engine(provider=None):
    return AlertEvaluator(config=AlertConfig(), notification_provider=provider or MockNotificationProvider(),
                          clock=DemoClock())


def main():
    results = []
    # 1 — no alert
    results.append({"scenario": "1_no_alert", "expected": "no operational alert",
                    "result": engine().evaluate(make_event("evt-low", "inc-low", 8, 10, 5))})
    # 2 — high alert from structured Phase 5 priority
    results.append({"scenario": "2_high_alert", "expected": "HIGH alert",
                    "result": engine().evaluate(make_event("evt-high", "inc-high", 58, 60, 52))})
    # 3 — critical urgency and rescue evidence
    results.append({"scenario": "3_critical_rescue", "expected": "CRITICAL alert",
                    "result": engine().evaluate(make_event("evt-critical", "inc-critical", 82, 80, 88,
                                                              ["IMMEDIATE_RESCUE", "CASUALTY_REPORT"]))})
    # 4 — update-based rise: no alert, first HIGH, then CRITICAL escalation
    progression = engine()
    update_records = [
        progression.evaluate(make_event("evt-update", "inc-update", 8, 10, 5)),
        progression.evaluate(make_event("evt-update", "inc-update", 56, 58, 54, event_state="UPDATED")),
        progression.evaluate(make_event("evt-update", "inc-update", 82, 78, 80,
                                        ["IMMEDIATE_RESCUE"], event_state="UPDATED")),
    ]
    results.append({"scenario": "4_escalation", "expected": "no alert → HIGH → CRITICAL escalation",
                    "history": update_records})
    # 5 — exact repeated source event: suppress the later Phase 8 duplicate
    repeats = engine()
    duplicate_records = [
        repeats.evaluate(make_event("evt-repeat", "inc-repeat", 82, 80, 80)),
        repeats.evaluate(make_event("evt-repeat", "inc-repeat", 82, 80, 80, event_state="DUPLICATE")),
    ]
    results.append({"scenario": "5_duplicate_suppression", "expected": "created then suppressed",
                    "history": duplicate_records})
    # 6 — notification failure does not erase alert creation
    results.append({"scenario": "6_notification_failure", "expected": "alert created; mock delivery failed",
                    "result": engine(MockNotificationProvider(fail=True)).evaluate(
                        make_event("evt-notify", "inc-notify", 81, 80, 75))})
    # 7 — high alert remains evaluable with an unresolved, coordinate-free location
    unresolved = make_event("evt-unresolved", "inc-unresolved", 62, 60, 55)
    unresolved["intelligence"]["phase6"] = {"incidents": [{"incident_id": "inc-unresolved",
        "location_text": "Synthetic unverified district", "geocoding_status": "failed",
        "latitude": None, "longitude": None, "spatial_metadata": {"geocoding_error": "not_resolved"}}]}
    results.append({"scenario": "7_unresolved_location", "expected": "alert evaluated; coordinates remain null",
                    "result": engine().evaluate(unresolved)})
    # 8 — spatial hotspot is sourced from a Phase 6-compatible synthetic fixture.
    spatial = {"incidents": [{"incident_id": "inc-hotspot", "latitude": 12.5, "longitude": 34.5,
                              "geocoding_status": "success", "geocoding_source": "synthetic_fixture",
                              "spatial_cluster_id": "sp-synthetic-1", "spatial_area_id": "area-synthetic-1"}],
               "hotspots": [{"hotspot_id": "hotspot-synthetic-1", "spatial_cluster_id": "sp-synthetic-1",
                             "incident_ids": ["inc-hotspot"], "incident_count": 4, "hotspot_score": 61.0}],
               "spatial_priority_ranking": [{"spatial_area_id": "area-synthetic-1",
                                             "spatial_cluster_id": "sp-synthetic-1", "spatial_priority_score": 62.0}]}
    results.append({"scenario": "8_spatial_hotspot", "expected": "hotspot contributes MEDIUM alert",
                    "result": engine().evaluate(make_event("evt-hotspot", "inc-hotspot", 5, 10, 5,
                                                            phase6=spatial))})

    artifact = {
        "demonstration": {"phase": 9, "source_type": "synthetic_demo", "synthetic_data": True,
                          "offline": True, "deterministic_clock": True,
                          "coordinates_are_synthetic_fixture_data": True,
                          "real_world_geographic_accuracy_validated": False,
                          "scientific_disaster_forecasting": False,
                          "autonomous_dispatch": False},
        "scenarios": results,
    }
    output = Path(__file__).with_name("phase9_demonstration_results.json")
    output.write_text(json.dumps(artifact, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print("PHASE 9 ALERTING & EARLY WARNING DEMONSTRATION - SYNTHETIC DATA")
    print("All fixture coordinates are synthetic; no real-world geographic accuracy is claimed.\n")
    for scenario in results:
        if "history" in scenario:
            values = scenario["history"]
            state = " -> ".join(f"{v['alert_status']}:{v['alert_decision']['alert_level']}" for v in values)
        else:
            value = scenario["result"]
            state = f"{value['alert_status']}:{value['alert_decision']['alert_level']}"
            if scenario["scenario"] == "6_notification_failure":
                state += f" notification={value['alert_record']['notification']['status']} alert_present={bool(value['alert_record'])}"
            if scenario["scenario"] == "7_unresolved_location":
                state += f" coordinates={value['spatial_context']['coordinates']}"
        print(f"{scenario['scenario']}: {state}")
    print(f"\nJSON artifact: {output}")
    return artifact


if __name__ == "__main__":
    main()
