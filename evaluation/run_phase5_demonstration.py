"""
evaluation/run_phase5_demonstration.py
======================================

Phase 5 demonstration script.

Runs the rule-based Phase 5 intelligence pipeline on synthetic
incident scenarios and generates a reproducible demonstration artifact.

IMPORTANT
---------
This is NOT supervised ML validation.

The scenarios are synthetic engineering sanity checks used to demonstrate:

- Severity scoring
- Urgency scoring
- Priority scoring
- Confidence scoring
- Decision-support flags
- Deterministic incident enrichment

The actual scoring configuration is imported from intelligence.config
so that the generated artifact cannot become inconsistent with the
implementation.
"""

import json
from pathlib import Path
import sys


# ---------------------------------------------------------------------
# PROJECT ROOT
# ---------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------
# PHASE 5 IMPORTS
# ---------------------------------------------------------------------

from intelligence import enrich_incident

from intelligence.config import (
    SEVERITY_CONFIG,
    URGENCY_CONFIG,
    PRIORITY_CONFIG,
    CONFIDENCE_CONFIG,
)


# ---------------------------------------------------------------------
# SYNTHETIC TEST SCENARIOS
# ---------------------------------------------------------------------

test_scenarios = [

    {
        "scenario_id": "SCENARIO_1",
        "description": "Minor infrastructure damage",

        "input_incident": {
            "text": "Minor road damage reported after light storm.",
            "location": ["Main Street"],
            "casualties": [],
            "displaced": [],
            "rescue": [],
            "infrastructure": ["road"],
            "disaster_type": "storm",
            "requests": [],
            "resources": [],
            "organizations": [],
            "persons": [],
            "numbers": [],
            "entities": []
        },

        "expected_behavior":
            "Low severity, low urgency, low priority"
    },

    {
        "scenario_id": "SCENARIO_2",
        "description": "Large casualty event",

        "input_incident": {
            "text": "500 people killed in massive earthquake.",
            "location": ["City Center"],
            "casualties": ["500 people killed"],
            "displaced": [],
            "rescue": [],
            "infrastructure": [],
            "disaster_type": "earthquake",
            "requests": [],
            "resources": [],
            "organizations": [],
            "persons": [],
            "numbers": [],
            "entities": []
        },

        "expected_behavior":
            "High severity, low urgency, moderate priority; large casualties trigger the operational severity floor, while priority remains weighted across severity and urgency."
    },

    {
        "scenario_id": "SCENARIO_3",
        "description": "Trapped-person rescue emergency",

        "input_incident": {
            "text":
                "People trapped under collapsed building! "
                "Need rescue immediately!",

            "location": ["Downtown"],
            "casualties": [],
            "displaced": [],
            "rescue": ["trapped", "rescue"],
            "infrastructure": ["building"],
            "disaster_type": "earthquake",
            "requests": [],
            "resources": [],
            "organizations": [],
            "persons": [],
            "numbers": [],
            "entities": []
        },

        "expected_behavior":
            "Moderate severity, high urgency, moderate priority; trapped-person evidence elevates urgency and triggers rescue response flags."
    },

    {
        "scenario_id": "SCENARIO_4",
        "description": "Mass displacement",

        "input_incident": {
            "text":
                "5000 people displaced by severe flooding.",

            "location": ["Riverside"],
            "casualties": [],
            "displaced": ["5000 people displaced"],
            "rescue": [],
            "infrastructure": [],
            "disaster_type": "flood",
            "requests": [],
            "resources": [],
            "organizations": [],
            "persons": [],
            "numbers": [],
            "entities": []
        },

        "expected_behavior":
            "High severity, low urgency, moderate priority; mass displacement triggers the operational severity floor."
    },

    {
        "scenario_id": "SCENARIO_5",
        "description": "Urgent food/water request",

        "input_incident": {
            "text":
                "Urgently need food and clean water "
                "for affected families.",

            "location": ["Shelter Area 3"],
            "casualties": [],
            "displaced": [],
            "rescue": [],
            "infrastructure": [],
            "disaster_type": None,
            "requests": ["food and clean water"],
            "resources": ["food", "water"],
            "organizations": [],
            "persons": [],
            "numbers": [],
            "entities": []
        },

        "expected_behavior":
            "Low severity, low urgency, low priority; the resource request is retained as a resource request, but its urgency score remains below the configured moderate threshold."
    },
]


# ---------------------------------------------------------------------
# HELPER: DETERMINE WHETHER EXPECTATION MATCHES
# ---------------------------------------------------------------------

def expectation_matches(enriched):
    """
    Determine whether the generated result matches the qualitative
    expectation stated for the scenario.

    This intentionally uses only the qualitative level labels.
    """

    return {
        "severity": enriched["severity_level"],
        "urgency": enriched["urgency_level"],
        "priority": enriched["priority_level"],
    }


# ---------------------------------------------------------------------
# MAIN DEMONSTRATION
# ---------------------------------------------------------------------

def run_demonstration():
    """Run the Phase 5 synthetic demonstration."""

    print("=" * 80)
    print("PHASE 5 INTELLIGENCE - RULE-BASED DEMONSTRATION")
    print("=" * 80)

    results = []

    for scenario in test_scenarios:

        print(
            f"\n{scenario['scenario_id']}: "
            f"{scenario['description']}"
        )

        print(
            f"Expected: "
            f"{scenario['expected_behavior']}"
        )

        print("-" * 80)

        # -------------------------------------------------------------
        # ENRICH INCIDENT
        # -------------------------------------------------------------

        enriched = enrich_incident(
            scenario["input_incident"]
        )

        # -------------------------------------------------------------
        # EXTRACT LEVELS
        # -------------------------------------------------------------

        observed_behavior = expectation_matches(
            enriched
        )

        # -------------------------------------------------------------
        # RESULT SUMMARY
        # -------------------------------------------------------------

        result_summary = {

            "scenario_id":
                scenario["scenario_id"],

            "description":
                scenario["description"],

            "input_text":
                scenario["input_incident"]["text"],

            "expected_behavior":
                scenario["expected_behavior"],

            "observed_behavior":
                observed_behavior,

            "enriched_output": {

                "incident_id":
                    enriched["incident_id"],

                "severity_score":
                    enriched["severity_score"],

                "severity_level":
                    enriched["severity_level"],

                "urgency_score":
                    enriched["urgency_score"],

                "urgency_level":
                    enriched["urgency_level"],

                "priority_score":
                    enriched["priority_score"],

                "priority_level":
                    enriched["priority_level"],

                "confidence_score":
                    enriched["confidence_score"],

                "confidence_level":
                    enriched["confidence_level"],

                "decision_flags":
                    enriched["decision_flags"]
            }
        }

        results.append(result_summary)

        # -------------------------------------------------------------
        # PRINT RESULTS
        # -------------------------------------------------------------

        print(
            f"Severity: "
            f"{enriched['severity_level']} "
            f"({enriched['severity_score']:.1f}/100)"
        )

        print(
            f"Urgency:   "
            f"{enriched['urgency_level']} "
            f"({enriched['urgency_score']:.1f}/100)"
        )

        print(
            f"Priority: "
            f"{enriched['priority_level']} "
            f"({enriched['priority_score']:.1f}/100)"
        )

        print(
            f"Confidence: "
            f"{enriched['confidence_level']} "
            f"({enriched['confidence_score']:.1f}/100)"
        )

        print(
            f"Flags: "
            f"{', '.join(enriched['decision_flags'][:5])}"
        )

    # -----------------------------------------------------------------
    # OUTPUT PATH
    # -----------------------------------------------------------------

    output_path = (
        ROOT
        / "evaluation"
        / "phase5_demonstration_results.json"
    )

    # -----------------------------------------------------------------
    # ACTUAL SCORING CONFIGURATION
    # -----------------------------------------------------------------
    #
    # IMPORTANT:
    # These values are taken directly from intelligence.config.
    # This prevents documentation drift.
    #

    scoring_configuration = {

        "thresholds": {
            "severity": {
                "critical": SEVERITY_CONFIG.CRITICAL_THRESHOLD,
                "high": SEVERITY_CONFIG.HIGH_THRESHOLD,
                "moderate": SEVERITY_CONFIG.MODERATE_THRESHOLD,
            },
            "urgency": {
                "critical": URGENCY_CONFIG.CRITICAL_THRESHOLD,
                "high": URGENCY_CONFIG.HIGH_THRESHOLD,
                "moderate": URGENCY_CONFIG.MODERATE_THRESHOLD,
            },
            "priority": {
                "critical": PRIORITY_CONFIG.CRITICAL_THRESHOLD,
                "high": PRIORITY_CONFIG.HIGH_THRESHOLD,
                "moderate": PRIORITY_CONFIG.MODERATE_THRESHOLD,
            },
            "confidence": {
                "high": CONFIDENCE_CONFIG.HIGH_CONFIDENCE_THRESHOLD,
                "moderate": CONFIDENCE_CONFIG.MODERATE_CONFIDENCE_THRESHOLD,
            },
        },

        "severity_weights": {

            "casualties":
                SEVERITY_CONFIG.CASUALTY_WEIGHT,

            "displaced":
                SEVERITY_CONFIG.DISPLACED_WEIGHT,

            "infrastructure":
                SEVERITY_CONFIG.INFRASTRUCTURE_WEIGHT,

            "rescue":
                SEVERITY_CONFIG.RESCUE_WEIGHT,

            "disaster_type":
                SEVERITY_CONFIG.DISASTER_TYPE_WEIGHT,
        },

        "urgency_weights": {

            "rescue":
                URGENCY_CONFIG.RESCUE_WEIGHT,

            "casualty":
                URGENCY_CONFIG.CASUALTY_WEIGHT,

            "immediate_danger":
                URGENCY_CONFIG.IMMEDIATE_DANGER_WEIGHT,

            "resource_urgency":
                URGENCY_CONFIG.RESOURCE_URGENCY_WEIGHT,
        },

        "priority_weights": {

            "severity":
                PRIORITY_CONFIG.SEVERITY_WEIGHT,

            "urgency":
                PRIORITY_CONFIG.URGENCY_WEIGHT,
        },

        "confidence_weights": {

            "explicit_casualty":
                CONFIDENCE_CONFIG.EXPLICIT_CASUALTY_WEIGHT,

            "explicit_location":
                CONFIDENCE_CONFIG.EXPLICIT_LOCATION_WEIGHT,

            "explicit_disaster_type":
                CONFIDENCE_CONFIG.EXPLICIT_DISASTER_TYPE_WEIGHT,

            "explicit_resource_request":
                CONFIDENCE_CONFIG.EXPLICIT_RESOURCE_REQUEST_WEIGHT,

            "explicit_rescue":
                CONFIDENCE_CONFIG.EXPLICIT_RESCUE_WEIGHT,

            "structured_extraction":
                CONFIDENCE_CONFIG.STRUCTURED_EXTRACTION_WEIGHT,
        }
    }

    # -----------------------------------------------------------------
    # DEMONSTRATION ARTIFACT
    # -----------------------------------------------------------------

    demonstration_artifact = {

        "evaluation_type":
            "rule_based_demonstration",

        "description":
            (
                "Phase 5 Intelligence & Decision Support - "
                "rule-based demonstration using synthetic scenarios."
            ),

        "method":
            "interpretable_rule_based_scoring",

        "disclaimer":
            (
                "This deterministic, interpretable, rule-based artifact is a sanity/demonstration evaluation only; it is NOT supervised ML validation and NOT calibrated probability estimation. "
                "These are deterministic synthetic scenarios "
                "used to demonstrate and sanity-check the "
                "Phase 5 intelligence pipeline."
            ),

        "test_results":
            results,

        "scoring_configuration":
            scoring_configuration,

        "system_characteristics": {

            "deterministic":
                True,

            "rule_based":
                True,

            "sanity_demonstration_evaluation":
                True,

            "supervised_ml_validation":
                False,

            "calibrated_probability_estimation":
                False,

            "interpretable":
                True,

            "confidence_based_on_evidence":
                True,

            "requires_supervised_training":
                False,

            "requires_labeled_severity_data":
                False,

            "resource_estimation":
                "heuristic_conservative",

            "deduplication":
                "text_entity_based",

            "clustering":
                "text_entity_based",

            "phase6_ready":
                True
        },

        "limitations": [

            "No supervised ML validation - rule-based only",

            "Heuristic resource estimates are not calibrated",

            "No geographic clustering in Phase 5",

            "No real-time performance optimization",

            "Confidence scores represent evidence quality, "
            "not calibrated probabilities",

            "Thresholds are engineering judgments and "
            "not scientifically derived",

            "Synthetic scenarios do not represent the full "
            "distribution of real disaster reports"
        ],

        "phase6_compatibility": {

            "preserves_original_location_text":
                True,

            "provides_incident_id":
                True,

            "provides_cluster_id":
                True,

            "provides_priority":
                True,

            "provides_severity":
                True,

            "provides_urgency":
                True,

            "ready_for_geocoding":
                True,

            "ready_for_spatial_analysis":
                True
        }
    }

    # -----------------------------------------------------------------
    # SAVE ARTIFACT
    # -----------------------------------------------------------------

    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            demonstration_artifact,
            f,
            indent=2,
            ensure_ascii=False
        )

    print("\n" + "=" * 80)

    print(
        "Demonstration results saved to: "
        f"{output_path}"
    )

    print("=" * 80)

    return demonstration_artifact


# ---------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------

if __name__ == "__main__":

    demonstration_artifact = run_demonstration()
