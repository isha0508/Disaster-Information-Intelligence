import json
from datetime import datetime, timedelta, timezone

import pytest

from alerting import AlertConfig, AlertEvaluator, AlertStateStore, MockNotificationProvider, evaluate_alert, evaluate_alerts


class FixedClock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def __call__(self):
        current = self.value
        self.value += timedelta(seconds=1)
        return current


def event(incident_id="inc-1", event_id="evt-1", priority=5, severity=10, urgency=5,
          flags=None, event_state="NEW", **fields):
    incident = {"incident_id": incident_id, "priority_score": priority,
                "severity_score": severity, "urgency_score": urgency,
                "confidence_score": 70, "confidence_level": "MODERATE",
                "decision_flags": flags or [], "requests": [], "resource_estimates": {"explicit_requests": []}}
    phase7 = {"summary": "Grounded synthetic summary", "uncertainties": ["synthetic fixture"]}
    result = {"event_id": event_id, "event_state": event_state,
              "provenance": {"source": "synthetic_test", "source_event_id": event_id},
              "intelligence": {"phase5": incident, "phase7": phase7},
              "processing_errors": []}
    result.update(fields)
    return result


def evaluator(provider=None, **kwargs):
    return AlertEvaluator(config=AlertConfig(**kwargs), notification_provider=provider,
                          state_store=AlertStateStore(), clock=FixedClock())


def test_low_priority_produces_no_operational_alert():
    result = evaluator().evaluate(event())
    assert result["alert_status"] == "NO_ALERT"
    assert result["alert_decision"]["should_alert"] is False


@pytest.mark.parametrize("score,level", [(30, "MEDIUM"), (55, "HIGH"), (80, "CRITICAL")])
def test_priority_bands_produce_configured_alert_levels(score, level):
    result = evaluator().evaluate(event(priority=score))
    assert result["alert_decision"]["alert_level"] == level
    assert result["alert_status"] == "CREATED"


def test_immediate_rescue_and_high_urgency_trigger_alerts():
    rescue = evaluator().evaluate(event(flags=["IMMEDIATE_RESCUE"]))
    urgent = evaluator().evaluate(event(incident_id="inc-2", event_id="evt-2", urgency=60))
    assert rescue["alert_decision"]["alert_level"] == "HIGH"
    assert urgent["alert_decision"]["alert_level"] == "HIGH"


def test_phase5_critical_priority_flag_is_respected():
    result = evaluator().evaluate(event(priority=0, flags=["CRITICAL_PRIORITY"]))
    assert result["alert_decision"]["alert_level"] == "CRITICAL"


def test_high_severity_and_urgent_resource_request_trigger_alerts():
    severe = evaluator().evaluate(event(severity=60))
    resource = evaluator().evaluate(event(incident_id="inc-2", event_id="evt-2",
                                           flags=["URGENT_RESOURCE_NEED"], priority=0))
    assert severe["alert_decision"]["alert_level"] == "HIGH"
    assert resource["alert_decision"]["alert_level"] == "MEDIUM"
    assert any(x["rule"] == "urgent_resource_request" and x["triggered"]
               for x in resource["alert_decision"]["rule_results"])


def test_decision_is_explainable_and_deterministic():
    first = evaluator().evaluate(event(priority=80, severity=82))
    second = evaluator().evaluate(event(priority=80, severity=82))
    assert first["alert_decision"]["alert_id"] == second["alert_decision"]["alert_id"]
    reasons = first["alert_decision"]["trigger_reasons"]
    assert reasons and all(reason["rule"] and reason["reason"] for reason in reasons)


def test_identity_separates_conditions_without_changing_incident_id():
    engine = evaluator()
    high = engine.evaluate(event(priority=55))
    critical = engine.evaluate(event(priority=80, event_state="UPDATED"))
    assert high["alert_record"]["alert_id"] != critical["alert_record"]["alert_id"]
    assert high["incident_intelligence"]["incident_id"] == "inc-1"
    assert critical["alert_record"]["incident_id"] == "inc-1"


def test_repeated_condition_is_explicitly_suppressed_and_not_notified_again():
    provider = MockNotificationProvider()
    engine = evaluator(provider)
    first = engine.evaluate(event(priority=80))
    repeated = engine.evaluate(event(priority=80, event_state="DUPLICATE"))
    assert first["alert_status"] == "CREATED"
    assert repeated["alert_status"] == "SUPPRESSED"
    assert repeated["alert_decision"]["suppressed"] is True
    assert repeated["alert_decision"]["suppression_reason"] == "phase8_duplicate_event"
    assert len(provider.attempts) == 1


def test_updated_event_without_material_change_does_not_alert_again():
    engine = evaluator()
    engine.evaluate(event(priority=55))
    updated = engine.evaluate(event(priority=56, event_state="UPDATED"))
    assert updated["alert_status"] == "SUPPRESSED"
    assert updated["alert_decision"]["suppression_reason"] == "no_material_worsening"


def test_low_to_high_to_critical_escalates_then_stops_repeating():
    engine = evaluator()
    low = engine.evaluate(event(priority=10))
    high = engine.evaluate(event(priority=55, event_state="UPDATED"))
    critical = engine.evaluate(event(priority=82, urgency=80, flags=["IMMEDIATE_RESCUE"], event_state="UPDATED"))
    repeat = engine.evaluate(event(priority=82, urgency=80, flags=["IMMEDIATE_RESCUE"], event_state="UPDATED"))
    assert low["alert_status"] == "NO_ALERT"
    assert high["alert_status"] == "CREATED"
    assert critical["alert_status"] == "ESCALATED"
    assert critical["alert_decision"]["escalation_reasons"]
    assert repeat["alert_status"] == "SUPPRESSED"
    assert repeat["alert_decision"]["is_escalation"] is False


def test_same_level_score_delta_can_escalate():
    engine = evaluator()
    engine.evaluate(event(priority=53))
    result = engine.evaluate(event(priority=66, event_state="UPDATED"))
    assert result["alert_status"] == "ESCALATED"
    assert any("Priority rose" in reason for reason in result["alert_decision"]["escalation_reasons"])


def test_score_decrease_is_reported_as_deescalation_without_new_alert():
    engine = evaluator()
    engine.evaluate(event(priority=55))
    result = engine.evaluate(event(priority=10, event_state="UPDATED"))
    assert result["alert_status"] == "NO_ALERT"
    assert result["alert_decision"]["deescalated"] is True


def test_cooldown_and_suppression_metadata_are_visible():
    engine = evaluator(cooldown_seconds=120)
    engine.evaluate(event(priority=80))
    result = engine.evaluate(event(priority=80))
    assert result["alert_suppression"]["suppressed"] is True
    assert result["alert_suppression"]["cooldown_remaining_seconds"] == 119.0


def test_configured_repeat_after_cooldown_is_opt_in():
    engine = evaluator(cooldown_seconds=2, suppress_unchanged_alerts=False)
    first = engine.evaluate(event(priority=80))
    inside = engine.evaluate(event(priority=80))
    after = engine.evaluate(event(priority=80))
    assert first["alert_status"] == "CREATED"
    assert inside["alert_status"] == "SUPPRESSED"
    assert after["alert_status"] == "CREATED"


def test_phase6_hotspot_adds_explainable_signal_and_preserves_coordinates():
    value = event(priority=0, phase6={"incidents": [{"incident_id": "inc-1", "latitude": 12.5,
                      "longitude": 34.5, "geocoding_status": "success", "spatial_cluster_id": "sp-1"}],
                      "hotspots": [{"hotspot_id": "hot-1", "incident_ids": ["inc-1"], "incident_count": 3}]})
    result = evaluator().evaluate(value)
    assert result["alert_decision"]["alert_level"] == "MEDIUM"
    assert result["spatial_context"]["coordinates"] == {"latitude": 12.5, "longitude": 34.5}
    assert "spatial_hotspot" in [r["rule"] for r in result["alert_decision"]["trigger_reasons"]]


def test_unresolved_or_invalid_coordinates_remain_null_but_alert_can_fire():
    value = event(priority=60, spatial_context={"geocoding_status": "failed", "latitude": 12,
                                                  "longitude": 34, "normalized_location": "Unverified"})
    result = evaluator().evaluate(value)
    assert result["alert_decision"]["should_alert"] is True
    assert result["spatial_context"]["latitude"] is None
    assert result["spatial_context"]["longitude"] is None
    assert result["spatial_context"]["coordinates"] is None
    assert "failed" in " ".join(result["alert_decision"]["uncertainty"])


def test_phase6_coordinate_validation_rejects_boolean_values():
    value = event(priority=0, spatial_context={"geocoding_status": "success",
                                                "latitude": True, "longitude": 20})
    result = evaluator().evaluate(value)
    assert result["spatial_context"]["coordinates"] is None
    assert result["spatial_context"]["latitude"] is None


def test_missing_intelligence_fields_and_missing_location_are_safe():
    result = evaluator().evaluate({"event_id": "evt-empty", "intelligence": {"phase5": {"incident_id": "i-empty"}}})
    assert result["alert_status"] == "NO_ALERT"
    assert result["alert_decision"]["priority_score"] is None
    assert result["spatial_context"]["coordinates"] is None


def test_out_of_range_score_is_not_clamped_into_an_alert():
    result = evaluator().evaluate(event(priority=150, severity=200, urgency=300))
    assert result["alert_status"] == "NO_ALERT"
    assert result["alert_decision"]["priority_score"] is None


def test_unrelated_spatial_ranking_is_not_attached_to_incident():
    value = event(priority=0, phase6={"incidents": [{"incident_id": "inc-1",
                        "geocoding_status": "failed", "latitude": None, "longitude": None}],
                        "spatial_priority_ranking": [{"spatial_area_id": "other-area",
                                                       "spatial_cluster_id": "other-cluster",
                                                       "spatial_priority_score": 99}]})
    result = evaluator().evaluate(value)
    assert result["spatial_context"]["spatial_priority_score"] is None
    assert result["alert_status"] == "NO_ALERT"


def test_malformed_input_is_handled_and_identity_is_required():
    assert evaluate_alert(None)["alert_status"] == "INVALID"
    assert evaluate_alert({"priority_score": 90})["alert_status"] == "INVALID"


def test_mock_notification_success_and_failure_do_not_remove_alert():
    success = evaluator(MockNotificationProvider()).evaluate(event(priority=80))
    failed = evaluator(MockNotificationProvider(fail=True)).evaluate(event(priority=80))
    assert success["alert_record"]["notification"]["status"] == "sent"
    assert failed["alert_status"] == "CREATED"
    assert failed["alert_record"]["notification"]["status"] == "failed"
    assert failed["alert_record"]["alert_id"]


def test_notification_provider_exception_is_captured_after_alert_creation():
    class Broken:
        def notify(self, alert):
            raise OSError("delivery unavailable")
    result = evaluator(Broken()).evaluate(event(priority=80))
    assert result["alert_status"] == "CREATED"
    assert result["alert_record"]["notification"]["status"] == "failed"
    assert result["alert_record"]["notification"]["error"]["error_type"] == "OSError"


def test_credentials_are_not_added_to_alert_result():
    result = evaluator().evaluate(event(priority=80, provenance={"source": "x", "api_key": "secret"}))
    encoded = json.dumps(result)
    assert "api_key" not in result["alert_record"]["provenance"]
    assert "secret" not in encoded


def test_batch_preserves_order_and_isolates_malformed_event():
    values = [event("one", "evt-one", priority=60), None, event("two", "evt-two", priority=5)]
    results = evaluate_alerts(values, evaluator=evaluator())
    assert [item["alert_status"] for item in results] == ["CREATED", "INVALID", "NO_ALERT"]
    assert results[0]["incident_intelligence"]["incident_id"] == "one"
    assert results[2]["incident_intelligence"]["incident_id"] == "two"


def test_phase5_phase7_and_phase8_fields_are_preserved_and_json_serializable():
    original = event(priority=60, phase8_marker="keep")
    result = evaluator().evaluate(original)
    json.dumps(result, allow_nan=False)
    assert result["event_id"] == "evt-1"
    assert result["event_state"] == "NEW"
    assert result["phase8_marker"] == "keep"
    assert result["incident_intelligence"]["priority_score"] == 60
    assert result["grounded_intelligence"]["summary"] == "Grounded synthetic summary"


def test_resolved_incident_is_marked_without_alerting():
    engine = evaluator()
    engine.evaluate(event(priority=80))
    result = engine.evaluate(event(priority=0, resolved=True))
    assert result["alert_status"] == "RESOLVED"
    assert result["alert_record"] is None


def test_config_thresholds_are_validated_and_configurable():
    with pytest.raises(ValueError):
        AlertConfig(priority_high_threshold=90, priority_critical_threshold=80)
    custom = evaluator(priority_high_threshold=70)
    assert custom.evaluate(event(priority=55))["alert_decision"]["alert_level"] == "MEDIUM"
