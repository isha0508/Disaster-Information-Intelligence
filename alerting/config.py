"""Configurable deterministic Phase 9 alert thresholds and policies."""

from dataclasses import dataclass, field

from intelligence.config import PRIORITY_CONFIG, SEVERITY_CONFIG, URGENCY_CONFIG


@dataclass(frozen=True)
class AlertConfig:
    """Alert thresholds default from the live Phase 5 configuration objects."""

    priority_medium_threshold: float = field(default_factory=lambda: PRIORITY_CONFIG.MODERATE_THRESHOLD)
    priority_high_threshold: float = field(default_factory=lambda: PRIORITY_CONFIG.HIGH_THRESHOLD)
    priority_critical_threshold: float = field(default_factory=lambda: PRIORITY_CONFIG.CRITICAL_THRESHOLD)
    urgency_high_threshold: float = field(default_factory=lambda: URGENCY_CONFIG.HIGH_THRESHOLD)
    urgency_critical_threshold: float = field(default_factory=lambda: URGENCY_CONFIG.CRITICAL_THRESHOLD)
    severity_high_threshold: float = field(default_factory=lambda: SEVERITY_CONFIG.HIGH_THRESHOLD)
    severity_critical_threshold: float = field(default_factory=lambda: SEVERITY_CONFIG.CRITICAL_THRESHOLD)
    urgent_resource_threshold: float = field(default_factory=lambda: URGENCY_CONFIG.URGENT_RESOURCE_FLOOR)
    hotspot_priority_threshold: float = field(default_factory=lambda: PRIORITY_CONFIG.HIGH_THRESHOLD)
    immediate_rescue_level: str = "HIGH"
    critical_flag_level: str = "CRITICAL"
    resource_alert_level: str = "MEDIUM"
    hotspot_alert_level: str = "MEDIUM"
    escalation_score_delta: float = 10.0
    cooldown_seconds: float = 300.0
    suppress_unchanged_alerts: bool = True
    notification_enabled: bool = True
    notify_on_escalation: bool = True
    include_phase7_context: bool = True

    def __post_init__(self):
        values = (self.priority_medium_threshold, self.priority_high_threshold,
                  self.priority_critical_threshold, self.urgency_high_threshold,
                  self.urgency_critical_threshold, self.severity_high_threshold,
                  self.severity_critical_threshold, self.urgent_resource_threshold,
                  self.hotspot_priority_threshold)
        if any(not 0 <= value <= 100 for value in values):
            raise ValueError("alert score thresholds must be within 0..100")
        if not (self.priority_medium_threshold <= self.priority_high_threshold <= self.priority_critical_threshold):
            raise ValueError("priority alert thresholds must be ordered")
        if self.urgency_high_threshold > self.urgency_critical_threshold:
            raise ValueError("urgency thresholds must be ordered")
        if self.severity_high_threshold > self.severity_critical_threshold:
            raise ValueError("severity thresholds must be ordered")
        if self.escalation_score_delta < 0 or self.cooldown_seconds < 0:
            raise ValueError("escalation delta and cooldown cannot be negative")
        valid_levels = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        for level in (self.immediate_rescue_level, self.critical_flag_level,
                      self.resource_alert_level, self.hotspot_alert_level):
            if level not in valid_levels:
                raise ValueError(f"unsupported alert level: {level}")
