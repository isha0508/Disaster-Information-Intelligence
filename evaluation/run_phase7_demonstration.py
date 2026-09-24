"""Run an offline, synthetic Phase 7 grounded generation demonstration."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gis import OfflineGeocoder, SpatialConfig, run_spatial_pipeline
from llm import MockLLMProvider, generate_intelligence, generate_situation_report


class MalformedProvider:
    name = "malformed_demo_provider"
    def generate(self, prompt, context, config):
        return {"summary": None, "recommended_actions": "unsupported"}


class FailingProvider:
    name = "failing_demo_provider"
    def generate(self, prompt, context, config):
        raise RuntimeError("synthetic provider outage")


def run_demonstration():
    # These coordinates are synthetic fixture values for exercising the flow;
    # they do not identify or validate a real operational location.
    records = [
        {"text": "People trapped near damaged hospital; urgent water requested.",
         "location": ["Demo Harbor"], "casualties": ["120 people injured"],
         "displaced": [], "rescue": ["people trapped"],
         "infrastructure": ["hospital damaged"], "disaster_type": "flood",
         "requests": ["urgent water"], "resources": ["water"],
         "organizations": [], "persons": [], "numbers": ["120"], "entities": []},
        {"text": "Flooding reported near the harbor access road.",
         "location": ["Demo Harbor East"], "casualties": [], "displaced": [],
         "rescue": [], "infrastructure": ["road"], "disaster_type": "flood",
         "requests": [], "resources": [], "organizations": [], "persons": [],
         "numbers": [], "entities": []},
        {"text": "People request shelter after flooding.",
         "location": ["Unresolved Demo Village"], "casualties": [],
         "displaced": ["30 families displaced"], "rescue": [],
         "infrastructure": [], "disaster_type": "flood", "requests": ["shelter"],
         "resources": ["shelter"], "organizations": [], "persons": [], "numbers": [],
         "entities": []},
    ]
    spatial = run_spatial_pipeline(
        records,
        geocoder=OfflineGeocoder({"Demo Harbor": (10.0, 20.0),
                                  "Demo Harbor East": (10.02, 20.01)},
                                 source="synthetic_demo_fixture"),
        config=SpatialConfig(cluster_radius_km=10, cluster_min_incidents=2,
                             hotspot_min_incidents=2),
    )
    incidents = spatial["incidents"]
    results = []
    for record in incidents:
        result = generate_intelligence(record, provider=MockLLMProvider(), spatial_context=spatial)
        results.append({"phase5_phase6_incident": record,
                        "phase7_result": result,
                        "situation_report": generate_situation_report(
                            record, provider=MockLLMProvider(), spatial_context=spatial)})
        print(f"{result['incident_id']} | priority={result['deterministic_intelligence']['priority_level']} "
              f"| location={result['spatial_context']['normalized_location']} "
              f"| geocoding={result['spatial_context']['geocoding_status']} "
              f"| cluster={result['spatial_context']['spatial_cluster_id']} "
              f"| summary={result['summary']}")

    error_examples = {
        "malformed_provider_output": generate_intelligence(incidents[0], provider=MalformedProvider(), spatial_context=spatial),
        "provider_failure": generate_intelligence(incidents[0], provider=FailingProvider(), spatial_context=spatial),
    }
    artifact = {
        "evaluation_type": "synthetic_offline_grounded_generation_demonstration",
        "description": "Deterministic mock LLM narrative over Phase 5/6 structured outputs.",
        "geographic_accuracy": "NOT evaluated. Demo fixture coordinates are synthetic and must not be used operationally.",
        "llm_quality_validation": "NOT performed; the mock provider validates integration behavior only.",
        "phase6_spatial_metadata": spatial["metadata"],
        "incident_results": results,
        "provider_error_handling_examples": error_examples,
        "limitations": [
            "LLM outputs are not guaranteed to be hallucination-free.",
            "Grounding consistency checks are not formal verification.",
            "Recommendations are decision support, not autonomous commands.",
            "Heuristic resource estimates are not confirmed requirements.",
            "Confidence scores are evidence quality indicators, not calibrated probabilities.",
            "Spatial coordinates are only as reliable as their provider/source.",
            "The mock provider does not represent real LLM quality.",
            "No autonomous emergency dispatch is performed.",
        ],
    }
    output_path = ROOT / "evaluation" / "phase7_demonstration_results.json"
    output_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nSaved Phase 7 demonstration: {output_path}")
    return artifact


if __name__ == "__main__":
    run_demonstration()
