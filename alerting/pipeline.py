"""Convenient single-event and batch entry points for Phase 9."""

from alerting.config import AlertConfig
from alerting.evaluator import AlertEvaluator


def evaluate_alert(event, config=None, state_store=None, notification_provider=None, evaluator=None):
    """Evaluate one Phase 8 operational record or Phase 5/6 incident mapping."""
    engine = evaluator or AlertEvaluator(config=config or AlertConfig(), state_store=state_store,
                                        notification_provider=notification_provider)
    try:
        return engine.evaluate(event)
    except Exception as exc:
        return {"event_state": "INVALID", "alert_status": "INVALID", "alert_record": None,
                "alert_decision": {"should_alert": False, "alert_level": "LOW", "alert_type": "INVALID",
                                   "trigger_reasons": [], "rule_results": [], "suppressed": False},
                "processing_errors": [{"phase": "phase9", "error_type": type(exc).__name__,
                                        "message": str(exc)[:500]}]}


def evaluate_alerts(events, config=None, state_store=None, notification_provider=None, evaluator=None):
    """Evaluate a batch in order; isolate malformed items and retain state between them."""
    engine = evaluator or AlertEvaluator(config=config or AlertConfig(), state_store=state_store,
                                        notification_provider=notification_provider)
    if events is None:
        return []
    try:
        iterator = iter(events)
    except Exception as exc:
        return [evaluate_alert(None, evaluator=engine)]
    results = []
    for item in iterator:
        results.append(evaluate_alert(item, evaluator=engine))
    return results
