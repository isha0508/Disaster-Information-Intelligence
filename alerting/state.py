"""In-memory alert state, suppression history, and incident snapshots."""

from copy import deepcopy


class AlertStateStore:
    """Runtime-only state; persistent alert history belongs to Phase 11."""
    def __init__(self):
        self._subjects = {}

    def get(self, subject_id):
        return deepcopy(self._subjects.get(str(subject_id)))

    def save_observation(self, subject_id, snapshot):
        subject_id = str(subject_id)
        current = self._subjects.setdefault(subject_id, {"alert_history": [], "last_alert_level": None,
                                                          "last_alert_at": None, "active_alert_id": None,
                                                          "resolved": False})
        current["last_observed"] = deepcopy(snapshot)
        current["last_condition"] = deepcopy(snapshot.get("condition"))
        return deepcopy(current)

    def add_alert(self, subject_id, record, now):
        subject_id = str(subject_id)
        current = self._subjects.setdefault(subject_id, {"alert_history": [], "last_alert_level": None,
                                                          "last_alert_at": None, "active_alert_id": None,
                                                          "resolved": False})
        current["alert_history"].append(deepcopy(record))
        current["last_alert_level"] = record["alert_level"]
        current["last_alert_at"] = now
        current["active_alert_id"] = record["alert_id"]
        current["resolved"] = False
        return deepcopy(current)

    def resolve(self, subject_id):
        current = self._subjects.get(str(subject_id))
        if current is None:
            return False
        current["resolved"] = True
        current["active_alert_id"] = None
        current["last_alert_level"] = None
        return True

    def history(self, subject_id):
        current = self._subjects.get(str(subject_id), {})
        return deepcopy(current.get("alert_history", []))

    def __len__(self):
        return len(self._subjects)
