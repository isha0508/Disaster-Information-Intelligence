"""Deterministic synthetic sanity demonstration of source-aware type resolution.

This does not fetch live incidents and does not validate Phase 3 model accuracy.
"""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.type_resolution import resolve_disaster_type


def _case(name, event, ml_type=None, confidence=None, phase4_type=None, expected=None):
    classification = {"disaster_type": {"label": ml_type, "confidence": confidence,
                                         "model": "distilbert_disaster_type"}} if ml_type else {}
    result = resolve_disaster_type(event, classification, phase4_type)
    actual = {"canonical_type": result["canonical_disaster_type"],
              "type_agreement_status": result["type_agreement_status"],
              "model_disagreement": result["model_disagreement"],
              "resolution_method": result["resolution_method"]}
    if expected:
        assert actual == expected, f"{name}: expected {expected}, got {actual}"
    return {"case": name, **actual,
            "source_type": result["source_disaster_type"],
            "phase4_type": result["phase4_disaster_type"],
            "ml_type": result["ml_disaster_type"],
            "resolution_rationale": result["resolution_rationale"],
            "warning": result["warning"]}


def main():
    cases = [
        _case("USGS earthquake + ML earthquake",
              {"source": "usgs", "metadata": {"feature_type": "earthquake"}}, "earthquake", .91,
              expected={"canonical_type": "earthquake", "type_agreement_status": "AGREEMENT",
                        "model_disagreement": False, "resolution_method": "authoritative_source"}),
        _case("USGS earthquake + ML hurricane",
              {"source": "usgs", "metadata": {"feature_type": "earthquake"}}, "hurricane", .9987,
              expected={"canonical_type": "earthquake", "type_agreement_status": "DISAGREEMENT",
                        "model_disagreement": True, "resolution_method": "authoritative_source"}),
        _case("GDACS flood + ML hurricane",
              {"source": "gdacs", "disaster_type": "FL"}, "hurricane", .9996,
              expected={"canonical_type": "flood", "type_agreement_status": "DISAGREEMENT",
                        "model_disagreement": True, "resolution_method": "authoritative_source"}),
        _case("GDACS drought + ML hurricane",
              {"source": "gdacs", "disaster_type": "DR"}, "hurricane", .9995,
              expected={"canonical_type": "drought", "type_agreement_status": "DISAGREEMENT",
                        "model_disagreement": True, "resolution_method": "authoritative_source"}),
        _case("No source type + ML fallback", {"source": "news_rss"}, "flood", .83,
              expected={"canonical_type": "flood", "type_agreement_status": "ML_ONLY",
                        "model_disagreement": False, "resolution_method": "phase3_ml_fallback"}),
        _case("Unknown source type + ML fallback",
              {"source": "gdacs", "disaster_type": "UNMAPPED-EVENT"}, "typhoon", .77,
              expected={"canonical_type": "cyclone", "type_agreement_status": "ML_ONLY",
                        "model_disagreement": False, "resolution_method": "phase3_ml_fallback"}),
        _case("No usable type evidence", {"source": "news_rss"},
              expected={"canonical_type": None, "type_agreement_status": "UNRESOLVED",
                        "model_disagreement": False, "resolution_method": "unresolved"}),
    ]
    artifact = {"evaluation_type": "deterministic_rule_resolution_sanity_demo",
                "synthetic_fixtures": True,
                "disclaimer": "Not a live-data evaluation, supervised ML validation, or calibrated probability assessment.",
                "cases": cases}
    print(json.dumps(artifact, indent=2, ensure_ascii=False, allow_nan=False))
    return artifact


if __name__ == "__main__":
    main()
