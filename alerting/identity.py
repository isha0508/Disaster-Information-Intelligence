"""Stable alert identities, separate from Phase 5 incident identities."""

import hashlib
import json


def alert_identity(incident_id, event_id, alert_level, alert_type, condition):
    subject = incident_id or event_id
    if subject is None:
        raise ValueError("incident_id or event_id is required for an alert identity")
    payload = {"subject": str(subject), "alert_level": alert_level,
               "alert_type": alert_type, "condition": condition}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "alt_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
