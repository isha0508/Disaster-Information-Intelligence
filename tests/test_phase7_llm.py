"""Offline Phase 7 grounding, provider, report, and error-handling tests."""

import json

from llm import (LLMConfig, MockLLMProvider, build_grounded_context,
                 explain_priority, generate_incident_summary,
                 generate_intelligence, generate_intelligence_batch,
                 generate_recommendations, generate_situation_report)


def phase5_record(**updates):
    record = {
        "incident_id": "INC_PHASE7_TEST", "text": "Raw: 999 people are trapped in Neverland.",
        "location": ["Harbor Ward"], "casualties": ["12 injured"],
        "displaced": ["40 families displaced"], "rescue": ["people trapped"],
        "infrastructure": ["bridge damaged"], "disaster_type": "flood",
        "requests": ["urgent drinking water"], "resources": ["water"],
        "organizations": [], "persons": [], "numbers": ["12"], "entities": [],
        "severity_score": 62.0, "severity_level": "HIGH", "severity_factors": [],
        "urgency_score": 71.0, "urgency_level": "HIGH", "urgency_factors": [],
        "priority_score": 66.0, "priority_level": "HIGH", "priority_factors": [
            {"factor": "severity", "contribution": 34.1}, {"factor": "urgency", "contribution": 31.9}],
        "confidence_score": 68.0, "confidence_level": "MODERATE", "confidence_factors": [],
        "resource_estimates": {
            "explicit_requests": {"raw_mentions": [{"category": "water", "quantity": None,
                                "raw_text": "urgent drinking water", "source": "request_entity"}]},
            "estimated_needs": {"water_liters_daily": 160, "metadata": {"method": "heuristic_multipliers"}},
        },
        "decision_flags": ["RESCUE_OPERATION"],
        "normalized_location": "Harbor Ward", "latitude": 10.1, "longitude": 123.2,
        "geocoding_status": "success", "geocoding_source": "test_fixture",
        "spatial_cluster_id": "SPATIAL_X", "spatial_cluster_size": 3,
        "spatial_metadata": {"coordinate_reference_system": "EPSG:4326"},
    }
    record.update(updates)
    return record


def test_grounded_context_separates_source_evidence_scores_estimates_spatial():
    ctx = build_grounded_context(phase5_record())
    assert ctx["evidence"]["casualties"] == ["12 injured"]
    assert ctx["computed_intelligence"]["priority_score"] == 66.0
    assert ctx["resources"]["explicit_requests"][0]["raw_text"] == "urgent drinking water"
    assert ctx["resources"]["estimated_needs"]["water_liters_daily"] == 160
    assert ctx["spatial"]["geocoding_status"] == "success"
    assert ctx["spatial"]["coordinates"] == {"latitude": 10.1, "longitude": 123.2}
    assert "not_reextracted" in ctx["source_text_status"]


def test_raw_text_is_not_promoted_to_structured_evidence():
    ctx = build_grounded_context({"text": "999 people trapped in Neverland"})
    assert ctx["evidence"]["casualties"] == []
    assert ctx["evidence"]["location_mentions"] == []
    assert "999" not in json.dumps(ctx["evidence"])
    assert "Neverland" not in json.dumps(ctx["evidence"])
    assert ctx["unknowns_and_uncertainties"]
    assert ctx["evidence_provenance"] == "phase4_structured_extractions_not_independently_verified"


def test_context_size_limit_truncates_oversized_records():
    config = LLMConfig(max_context_chars=2048)
    record = {"incident_id": "large", "text": "x" * 50000,
              "location": ["place"] * 500, "casualties": ["evidence" * 100] * 500,
              "requests": ["request" * 100] * 200}
    context = build_grounded_context(record, config=config)
    assert context["context_truncated"] is True
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= config.max_context_chars


def test_failed_pending_ambiguous_locations_never_expose_coordinates():
    for status in ("failed", "pending", "ambiguous"):
        ctx = build_grounded_context(phase5_record(geocoding_status=status))
        assert ctx["spatial"]["coordinates"] is None
        assert ctx["spatial"]["geocoding_status"] == status
        assert any("coordinates" in item for item in ctx["unknowns_and_uncertainties"])


def test_invalid_success_coordinates_are_not_claimed_verified():
    ctx = build_grounded_context(phase5_record(latitude=100, longitude=123.2))
    assert ctx["spatial"]["coordinates"] is None


def test_global_phase6_output_is_joined_by_incident_id():
    record = phase5_record()
    result = {"incidents": [record], "hotspots": [{"hotspot_id": "H1", "spatial_cluster_id": "SPATIAL_X", "incident_count": 3}],
              "spatial_priority_ranking": [{"spatial_cluster_id": "SPATIAL_X", "spatial_priority_score": 74.0}]}
    ctx = build_grounded_context(record, spatial_context=result)
    assert ctx["spatial"]["hotspot"] is True
    assert ctx["spatial"]["hotspot_id"] == "H1"
    assert ctx["spatial"]["spatial_priority_score"] == 74.0


def test_mock_summary_priority_and_recommendations_are_grounded():
    record = phase5_record()
    summary = generate_incident_summary(record)
    explanation = explain_priority(record)
    actions = generate_recommendations(record)
    assert "flood" in summary
    assert "999" not in summary and "Neverland" not in summary
    assert explanation and any("severity" in line for line in explanation)
    assert any("rescue" in action.lower() for action in actions)
    assert any("drinking water" in action for action in actions)
    assert not any("exactly" in action.lower() or "deploy 6" in action.lower() for action in actions)


def test_mock_narrative_is_deterministic_for_same_grounded_record():
    provider = MockLLMProvider()
    record = phase5_record()
    first = provider.generate("prompt", build_grounded_context(record), LLMConfig())
    second = provider.generate("prompt", build_grounded_context(record), LLMConfig())
    assert first == second


def test_unified_result_preserves_all_source_of_truth_fields():
    record = phase5_record()
    result = generate_intelligence(record)
    assert result["incident_id"] == record["incident_id"]
    for name in ("severity", "urgency", "priority", "confidence"):
        assert result["deterministic_intelligence"][f"{name}_score"] == record[f"{name}_score"]
        assert result["deterministic_intelligence"][f"{name}_level"] == record[f"{name}_level"]
    assert result["spatial_context"]["coordinates"] == {"latitude": 10.1, "longitude": 123.2}
    assert result["spatial_context"]["geocoding_status"] == "success"
    assert result["resource_summary"]["estimated_needs_type"] == "heuristic_estimate"
    assert result["resource_summary"]["explicit_requests"] != result["resource_summary"]["estimated_needs"]
    assert result["generation_metadata"]["phase7_version"] == "1.0"
    json.dumps(result)


def test_no_coordinates_when_failed_and_uncertainty_is_visible():
    result = generate_intelligence(phase5_record(geocoding_status="failed"))
    assert result["spatial_context"]["coordinates"] is None
    assert any("geocoding status: failed" in item for item in result["uncertainties"])
    assert "not verified" not in result["summary"].lower() or "Harbor Ward" in result["summary"]


def test_situation_report_has_all_sections_and_missing_data_language():
    report = generate_situation_report(phase5_record(displaced=[], infrastructure=[], rescue=[],
                                                       casualties=[], requests=[], resources=[],
                                                       confidence_level="LOW", geocoding_status="failed"))
    for heading in ("Incident Overview", "Situation Summary", "Human Impact", "Infrastructure Impact",
                    "Rescue / Immediate Danger", "Resource Requests", "Estimated Resource Needs",
                    "Severity / Urgency / Priority", "Why This Incident Is Prioritized", "Spatial Context",
                    "Recommended Actions", "Uncertainties / Information Gaps"):
        assert heading in report
    assert "Not available" in report
    assert "HEURISTIC ESTIMATES" in report
    assert "coordinates are available" in report or "coordinates" in report


def test_batch_preserves_order_and_isolates_bad_records():
    output = generate_intelligence_batch([phase5_record(), None, {"incident_id": "THIRD"}])
    assert len(output) == 3
    assert output[0]["incident_id"] == "INC_PHASE7_TEST"
    assert output[1]["incident_id"] is None
    assert output[2]["incident_id"] == "THIRD"
    assert [item["generation_metadata"]["batch_index"] for item in output] == [0, 1, 2]


class BrokenProvider:
    name = "broken"
    def generate(self, prompt, context, config):
        raise TimeoutError("not copied to output")


class MalformedProvider:
    name = "malformed"
    def generate(self, prompt, context, config):
        return {"summary": 17, "recommended_actions": "do something"}


def test_provider_timeout_uses_controlled_mock_fallback():
    result = generate_intelligence(phase5_record(), provider=BrokenProvider())
    assert result["summary"]
    assert result["generation_metadata"]["generation_mode"] == "mock_fallback"
    assert any("generation_failed:TimeoutError" in item for item in result["generation_errors"])
    assert "not copied to output" not in json.dumps(result)


def test_malformed_provider_output_is_rejected_and_falls_back():
    result = generate_intelligence(phase5_record(), provider=MalformedProvider())
    assert result["summary"]
    assert result["generation_metadata"]["generation_mode"] == "mock_fallback"
    assert any("invalid_provider_output" in item for item in result["generation_errors"])


class HallucinatingProvider:
    name = "hallucinating-test-double"
    def generate(self, prompt, context, config):
        return {"summary": "7000 people confirmed at Atlantis.",
                "situation_assessment": "Priority score 999.",
                "priority_explanation": ["Priority is 999."],
                "recommended_actions": ["Deploy exactly 900 teams at Atlantis."]}


def test_grounding_consistency_warns_on_unsupported_numbers():
    result = generate_intelligence(phase5_record(), provider=HallucinatingProvider())
    assert any("unsupported numeric claim" in item.lower() for item in result["grounding_warnings"])
    # The provider cannot overwrite deterministic scores, IDs, or coordinate values.
    assert result["incident_id"] == "INC_PHASE7_TEST"
    assert result["deterministic_intelligence"]["priority_score"] == 66.0
    assert result["spatial_context"]["coordinates"]["latitude"] == 10.1


class WrongDisasterProvider:
    name = "wrong-disaster-test-double"
    def generate(self, prompt, context, config):
        return {"summary": "A tsunami response is underway.",
                "situation_assessment": "Phase 5 scores are unchanged.",
                "priority_explanation": ["Priority is supplied by Phase 5."],
                "recommended_actions": ["Confirm reports with responders."]}


def test_grounding_consistency_warns_on_unsupported_disaster_type():
    result = generate_intelligence(phase5_record(), provider=WrongDisasterProvider())
    assert any("unsupported disaster type: tsunami" in item.lower() for item in result["grounding_warnings"])


class InventedRequestProvider:
    name = "invented-request-test-double"
    def generate(self, prompt, context, config):
        return {"summary": "Flood damage is reported.",
                "situation_assessment": "Priority is supplied by Phase 5.",
                "priority_explanation": ["No score changes."],
                "recommended_actions": ["Food was requested and should be sent."]}


def test_grounding_consistency_warns_on_unprovided_resource_request():
    record = phase5_record(requests=[], resources=[], resource_estimates={
        "explicit_requests": {"raw_mentions": []}, "estimated_needs": None})
    result = generate_intelligence(record, provider=InventedRequestProvider())
    assert any("unprovided explicit resource request: food" in item.lower()
               for item in result["grounding_warnings"])


def test_recommendations_can_be_disabled():
    result = generate_intelligence(phase5_record(), config=LLMConfig(recommendations_enabled=False))
    assert result["recommended_actions"] == []
    assert generate_recommendations(phase5_record(), config=LLMConfig(recommendations_enabled=False)) == []


def test_missing_confidence_is_not_invented():
    result = generate_intelligence({"text": "flood damage", "location": []})
    assert result["deterministic_intelligence"]["confidence_score"] is None
    assert "unavailable" in result["confidence_note"].lower()


def test_result_does_not_include_provider_credentials():
    result = generate_intelligence(phase5_record(), config=LLMConfig(api_key="secret-test-value"))
    assert "secret-test-value" not in json.dumps(result)
    assert result["generation_metadata"]["api_key_included"] is False
