"""Serializable Phase 9 alert decision and emitted alert structures."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AlertDecision:
    should_alert: bool
    alert_level: str
    alert_type: str
    incident_id: str | None = None
    event_id: str | None = None
    priority_score: float | None = None
    severity_score: float | None = None
    urgency_score: float | None = None
    confidence_score: float | None = None
    relevant_flags: list[str] = field(default_factory=list)
    rule_results: list[dict[str, Any]] = field(default_factory=list)
    trigger_reasons: list[dict[str, Any]] = field(default_factory=list)
    spatial_context: dict[str, Any] = field(default_factory=dict)
    uncertainty: list[str] = field(default_factory=list)
    suppressed: bool = False
    suppression_reason: str | None = None
    is_escalation: bool = False
    escalation_reasons: list[str] = field(default_factory=list)
    deescalated: bool = False
    alert_id: str | None = None

    def to_dict(self):
        return asdict(self)


@dataclass
class AlertRecord:
    alert_id: str
    incident_id: str | None
    event_id: str | None
    alert_level: str
    alert_type: str
    status: str
    created_at: str
    priority_score: float | None
    severity_score: float | None
    urgency_score: float | None
    confidence_score: float | None
    trigger_reasons: list[dict[str, Any]]
    provenance: dict[str, Any] = field(default_factory=dict)
    spatial_context: dict[str, Any] = field(default_factory=dict)
    uncertainty: list[str] = field(default_factory=list)
    escalation: dict[str, Any] = field(default_factory=dict)
    suppression: dict[str, Any] = field(default_factory=dict)
    notification: dict[str, Any] = field(default_factory=dict)
    grounded_intelligence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)
