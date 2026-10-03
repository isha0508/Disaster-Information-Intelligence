"""State-aware deterministic alert evaluation over Phase 5–8 records."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from alerting.config import AlertConfig
from alerting.escalation import assess_escalation
from alerting.identity import alert_identity
from alerting.models import AlertDecision, AlertRecord
from alerting.notifications import MockNotificationProvider
from alerting.rules import LEVEL_RANK, _score, evaluate_rules
from alerting.state import AlertStateStore
from gis.schemas import validate_coordinates


class AlertEvaluator:
    """Evaluate incidents and maintain alert suppression/escalation state."""
    def __init__(self, config=None, state_store=None, notification_provider=None, clock=None):
        self.config = config or AlertConfig()
        self.state_store = state_store or AlertStateStore()
        self.notification_provider = notification_provider or MockNotificationProvider()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def evaluate(self, item):
        if not isinstance(item, dict):
            return _invalid("alert input must be a mapping")
        source = deepcopy(item)
        incident, spatial, phase7 = _extract_context(source)
        event_id = source.get("event_id")
        incident_id = incident.get("incident_id") or source.get("incident_id")
        subject_id = incident_id or event_id
        if subject_id is None:
            return _invalid("incident_id or event_id is required")
        now = _iso(self.clock())
        flags = incident.get("decision_flags") or []
        flags = [str(flag) for flag in flags] if isinstance(flags, (list, tuple, set)) else []
        level, rule_results, triggered_rules, alert_type = evaluate_rules(incident, spatial, self.config)
        trigger_reasons = [entry for entry in rule_results if entry["triggered"]]
        priority = _score(incident.get("priority_score"))
        severity = _score(incident.get("severity_score"))
        urgency = _score(incident.get("urgency_score"))
        confidence = _score(incident.get("confidence_score"))
        uncertainties = _uncertainties(incident, spatial)
        resolved = bool(source.get("resolved") or str(source.get("incident_status", "")).upper() in {"RESOLVED", "CLOSED"})
        previous = self.state_store.get(subject_id)
        snapshot = {"alert_level": level, "priority_score": priority, "severity_score": severity,
                    "urgency_score": urgency, "confidence_score": confidence,
                    "decision_flags": flags, "hotspot": spatial.get("hotspot", False)}

        if resolved:
            self.state_store.resolve(subject_id)
            self.state_store.save_observation(subject_id, snapshot)
            decision = AlertDecision(False, level, "RESOLVED", incident_id, event_id, priority,
                                     severity, urgency, confidence, flags, rule_results, trigger_reasons,
                                     spatial, uncertainties, suppression_reason="incident_resolved")
            return _result(source, incident, spatial, phase7, decision, None, "RESOLVED", now)

        escalated, deescalated, escalation_reasons = assess_escalation(previous, snapshot, self.config)
        candidate_exists = bool(triggered_rules)
        condition = {"rules": triggered_rules, "priority_score": priority,
                     "severity_score": severity, "urgency_score": urgency,
                     "flags": sorted(flags), "hotspot": bool(spatial.get("hotspot"))}
        snapshot["condition"] = condition
        alert_id = alert_identity(incident_id, event_id, level, alert_type, condition) if candidate_exists else None
        should_create = candidate_exists
        suppression_reason = None
        event_state = str(source.get("event_state", "NEW")).upper()

        if candidate_exists and event_state in {"INVALID", "FAILED"}:
            should_create, suppression_reason = False, f"phase8_event_state_{event_state.lower()}"
        elif candidate_exists and event_state == "DUPLICATE":
            should_create, suppression_reason = False, "phase8_duplicate_event"
        elif candidate_exists and previous and previous.get("last_alert_level"):
            prior_level = previous["last_alert_level"]
            if LEVEL_RANK[level] < LEVEL_RANK[prior_level]:
                should_create, suppression_reason = False, "existing_higher_level_alert_active"
            elif LEVEL_RANK[level] == LEVEL_RANK[prior_level] and not escalated:
                same_condition = _same_condition(previous, condition)
                cooldown_remaining = _cooldown_remaining(previous, now, self.config)
                if same_condition and not self.config.suppress_unchanged_alerts and cooldown_remaining == 0:
                    should_create = True
                else:
                    should_create = False
                    suppression_reason = "unchanged_condition" if same_condition else "no_material_worsening"
            elif LEVEL_RANK[level] > LEVEL_RANK[prior_level] and not escalated:
                # A higher rule-derived level is itself a material escalation.
                escalated = True
                escalation_reasons = [f"Alert level increased from {prior_level} to {level}."]

        if not candidate_exists:
            should_create = False
        suppressed = candidate_exists and not should_create
        if suppressed:
            suppression_reason = suppression_reason or "repeat_suppressed"
        status = "ESCALATED" if should_create and escalated else ("CREATED" if should_create else
                 ("SUPPRESSED" if suppressed else "NO_ALERT"))

        decision = AlertDecision(
            should_alert=should_create, alert_level=level, alert_type=alert_type,
            incident_id=incident_id, event_id=event_id, priority_score=priority,
            severity_score=severity, urgency_score=urgency, confidence_score=confidence,
            relevant_flags=flags, rule_results=rule_results, trigger_reasons=trigger_reasons,
            spatial_context=spatial, uncertainty=uncertainties, suppressed=suppressed,
            suppression_reason=suppression_reason, is_escalation=bool(should_create and escalated),
            escalation_reasons=escalation_reasons if should_create and escalated else [],
            deescalated=deescalated, alert_id=alert_id)

        alert_record = None
        if should_create:
            notification = {"attempted": False, "status": "not_attempted"}
            record = AlertRecord(
                alert_id=alert_id, incident_id=incident_id, event_id=event_id,
                alert_level=level, alert_type=alert_type,
                status="ESCALATED" if escalated else "ACTIVE", created_at=now,
                priority_score=priority, severity_score=severity, urgency_score=urgency,
                confidence_score=confidence, trigger_reasons=trigger_reasons,
                provenance=_provenance(source), spatial_context=spatial,
                uncertainty=uncertainties,
                escalation={"is_escalation": bool(escalated), "reasons": escalation_reasons,
                            "previous_alert_level": previous.get("last_alert_level") if previous else None},
                suppression={}, notification=notification,
                grounded_intelligence=phase7 if self.config.include_phase7_context else {})
            alert_record = record.to_dict()
            if self.config.notification_enabled and (not escalated or self.config.notify_on_escalation):
                notification = self._notify(alert_record)
                alert_record["notification"] = notification
            self.state_store.add_alert(subject_id, alert_record, now)
        else:
            # Suppression is visible in the result; it never creates another active alert.
            alert_record = None

        self.state_store.save_observation(subject_id, snapshot)
        if suppressed and previous:
            decision.suppression_reason = suppression_reason
        return _result(source, incident, spatial, phase7, decision, alert_record, status, now,
                       _suppression(previous, now, self.config) if suppressed else {})

    def _notify(self, alert):
        try:
            response = self.notification_provider.notify(alert)
            if not isinstance(response, dict):
                return {"attempted": True, "status": "failed", "error": "invalid_provider_response"}
            return _sanitize({"attempted": True, **response})
        except Exception as exc:
            message = re.sub(r"(?i)(api[_-]?key|token|password|secret)(\s*[:=]\s*)[^\s,;]+",
                             r"\1\2[REDACTED]", str(exc))
            return {"attempted": True, "status": "failed",
                    "error": {"error_type": type(exc).__name__, "message": message[:250]}}


def _extract_context(source):
    intelligence = source.get("intelligence") if isinstance(source.get("intelligence"), dict) else {}
    incident = source.get("incident_record") or source.get("incident") or intelligence.get("phase5")
    if not isinstance(incident, dict):
        incident = source
    else:
        incident = deepcopy(incident)
    phase6 = intelligence.get("phase6") or source.get("phase6")
    spatial = source.get("spatial_context") or source.get("spatial")
    if not isinstance(spatial, dict):
        spatial = {}
    if isinstance(phase6, dict):
        rows = phase6.get("incidents") or []
        row = next((x for x in rows if isinstance(x, dict) and x.get("incident_id") == incident.get("incident_id")), None)
        if row is None and len(rows) == 1 and isinstance(rows[0], dict):
            row = rows[0]
        if isinstance(row, dict):
            spatial = {**row, **spatial}
        hotspots = phase6.get("hotspots") or []
        for hotspot in hotspots:
            if isinstance(hotspot, dict) and incident.get("incident_id") in (hotspot.get("incident_ids") or []):
                spatial.update({"hotspot": True, "hotspot_id": hotspot.get("hotspot_id"),
                                "hotspot_score": hotspot.get("hotspot_score"),
                                "hotspot_incident_count": hotspot.get("incident_count")})
                break
        rankings = phase6.get("spatial_priority_ranking") or []
        if spatial.get("spatial_priority_score") is None:
            area_id, cluster_id = spatial.get("spatial_area_id"), spatial.get("spatial_cluster_id")
            match = next((entry for entry in rankings if isinstance(entry, dict) and
                          ((area_id is not None and entry.get("spatial_area_id") == area_id) or
                           (cluster_id is not None and entry.get("spatial_cluster_id") == cluster_id))), None)
            if match:
                spatial["spatial_priority_score"] = match.get("spatial_priority_score")
    # Direct Phase 6 fields are accepted, while non-success geocoding never yields coordinates.
    if not spatial:
        spatial = {key: source[key] for key in (
            "location_text", "normalized_location", "latitude", "longitude", "geocoding_status",
            "geocoding_source", "spatial_cluster_id", "spatial_area_id", "spatial_metadata",
            "spatial_priority_score", "hotspot", "hotspot_id") if key in source}
    status = spatial.get("geocoding_status", "unknown")
    coordinates = None
    if status == "success":
        try:
            latitude, longitude = spatial.get("latitude"), spatial.get("longitude")
            valid, _ = validate_coordinates(latitude, longitude)
            if valid:
                latitude, longitude = float(latitude), float(longitude)
                coordinates = {"latitude": latitude, "longitude": longitude}
        except (TypeError, ValueError):
            pass
    spatial_context = {
        "location_text": spatial.get("location_text", incident.get("location_text")),
        "normalized_location": spatial.get("normalized_location"),
        "latitude": coordinates["latitude"] if coordinates else None,
        "longitude": coordinates["longitude"] if coordinates else None,
        "coordinates": coordinates,
        "geocoding_status": status,
        "geocoding_source": spatial.get("geocoding_source"),
        "spatial_cluster_id": spatial.get("spatial_cluster_id"),
        "spatial_area_id": spatial.get("spatial_area_id"),
        "spatial_priority_score": _score(spatial.get("spatial_priority_score")),
        "hotspot": bool(spatial.get("hotspot") or spatial.get("hotspot_id")),
        "hotspot_id": spatial.get("hotspot_id"),
        "hotspot_score": _score(spatial.get("hotspot_score")),
        "hotspot_incident_count": spatial.get("hotspot_incident_count"),
        "spatial_metadata": spatial.get("spatial_metadata") or {},
    }
    phase7 = intelligence.get("phase7") or source.get("grounded_intelligence") or {}
    return incident, spatial_context, phase7 if isinstance(phase7, dict) else {}


def _uncertainties(incident, spatial):
    uncertainty = []
    confidence = incident.get("confidence_level")
    if confidence == "LOW" or "LOW_EVIDENCE" in (incident.get("decision_flags") or []):
        uncertainty.append("Phase 5 evidence confidence is low; alert remains a decision-support signal.")
    elif confidence is None:
        uncertainty.append("Phase 5 confidence is unavailable.")
    if spatial.get("geocoding_status") != "success" or spatial.get("coordinates") is None:
        uncertainty.append(f"Verified coordinates are unavailable (geocoding status: {spatial.get('geocoding_status')}).")
    for field in ("priority_score", "severity_score", "urgency_score"):
        if incident.get(field) is None:
            uncertainty.append(f"{field} is unavailable; no value was inferred.")
    return uncertainty


def _provenance(source):
    provenance = source.get("provenance")
    return _sanitize(provenance) if isinstance(provenance, dict) else {}


def _same_condition(previous, condition):
    observed = previous.get("last_condition")
    return observed == condition


def _suppression(previous, now, config):
    if not previous or not previous.get("last_alert_at"):
        return {"suppressed": True, "cooldown_seconds": config.cooldown_seconds,
                "cooldown_remaining_seconds": None}
    remaining = _cooldown_remaining(previous, now, config)
    return {"suppressed": True, "cooldown_seconds": config.cooldown_seconds,
            "cooldown_remaining_seconds": round(remaining, 3) if remaining is not None else None}


def _cooldown_remaining(previous, now, config):
    if not previous or not previous.get("last_alert_at"):
        return None
    try:
        then = datetime.fromisoformat(previous["last_alert_at"].replace("Z", "+00:00"))
        current = datetime.fromisoformat(now.replace("Z", "+00:00"))
        return max(0.0, config.cooldown_seconds - (current - then).total_seconds())
    except (ValueError, TypeError):
        return None


def _result(source, incident, spatial, phase7, decision, alert_record, status, now, suppression=None):
    result = _sanitize(source)
    result.update({"incident_intelligence": deepcopy(incident), "spatial_context": deepcopy(spatial),
                   "grounded_intelligence": deepcopy(phase7),
                   "alert_decision": decision.to_dict(), "alert_record": alert_record,
                   "alert_status": status, "alert_evaluated_at": now,
                   "alert_suppression": suppression or {}})
    return json.loads(json.dumps(_sanitize(result), default=str, allow_nan=False))


def _sanitize(value):
    """Remove credential-like fields and redact URL user-info/query secrets."""
    sensitive = ("password", "secret", "token", "credential", "authorization", "api_key", "apikey")
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            name = str(key)
            if any(part in name.casefold() for part in sensitive):
                continue
            if "url" in name.casefold() and isinstance(item, str):
                try:
                    parts = urlsplit(item)
                    host = parts.hostname or ""
                    if parts.port:
                        host += f":{parts.port}"
                    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not any(part in k.casefold() for part in sensitive)]
                    cleaned[name] = urlunsplit((parts.scheme, host, parts.path, urlencode(query), parts.fragment))
                except ValueError:
                    cleaned[name] = item
            else:
                cleaned[name] = _sanitize(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, set):
        return [_sanitize(item) for item in sorted(value, key=str)]
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    return value


def _invalid(message):
    return {"event_state": "INVALID", "alert_status": "INVALID", "alert_record": None,
            "alert_decision": {"should_alert": False, "alert_level": "LOW", "alert_type": "INVALID",
                               "trigger_reasons": [], "rule_results": [], "suppressed": False},
            "processing_errors": [{"phase": "phase9", "error_type": "ValueError", "message": message}]}


def _iso(value):
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
