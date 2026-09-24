"""Stable event identities; intentionally distinct from incident/cluster IDs."""

import hashlib


def event_id_for(event):
    source = str(event["source"]).strip().casefold()
    source_event_id = event.get("source_event_id")
    identity = ("source-id\0" + source + "\0" + str(source_event_id)) if source_event_id else (
        "content\0" + source + "\0" + event["content_fingerprint"])
    return "evt_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
