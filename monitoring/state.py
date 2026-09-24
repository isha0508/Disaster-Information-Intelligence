"""Replaceable in-memory event lifecycle store."""

from copy import deepcopy


class EventStateStore:
    """Tracks latest source event versions in memory; Phase 11 can replace it."""
    def __init__(self):
        self._events = {}

    def observe(self, event_id, event, now):
        current = self._events.get(event_id)
        fingerprint = event["content_fingerprint"]
        if current is None:
            entry = {"event_id": event_id, "first_seen": now, "last_seen": now,
                     "content_fingerprint": fingerprint, "previous_fingerprint": None,
                     "processing_status": "NEW", "processing_count": 0,
                     "version": 1, "latest_event": deepcopy(event)}
            self._events[event_id] = entry
            return "NEW", deepcopy(entry)
        current["last_seen"] = now
        if current["content_fingerprint"] == fingerprint:
            return "DUPLICATE", deepcopy(current)
        current["previous_fingerprint"] = current["content_fingerprint"]
        current["content_fingerprint"] = fingerprint
        current["latest_event"] = deepcopy(event)
        current["version"] += 1
        current["processing_status"] = "UPDATED"
        return "UPDATED", deepcopy(current)

    def set_status(self, event_id, status, increment_processing=False):
        entry = self._events[event_id]
        entry["processing_status"] = status
        if increment_processing:
            entry["processing_count"] += 1
        return deepcopy(entry)

    def set_operational_record(self, event_id, record):
        """Cache the latest successful/partial result for duplicate replay."""
        self._events[event_id]["operational_record"] = deepcopy(record)

    def get(self, event_id):
        return deepcopy(self._events.get(event_id))

    def __len__(self):
        return len(self._events)
