"""Finite-cycle and stoppable polling runner for Phase 8 sources."""

from copy import deepcopy
from threading import Event
import time

from monitoring.config import MonitoringConfig
from monitoring.identity import event_id_for
from monitoring.normalization import normalize_event, sanitize_source_value
from monitoring.pipeline import IntelligencePipeline, _error
from monitoring.state import EventStateStore


class MonitoringRunner:
    def __init__(self, config=None, store=None, pipeline=None):
        self.config = config or MonitoringConfig()
        self.store = store or EventStateStore()
        self.pipeline = pipeline or IntelligencePipeline(run_phase3=self.config.run_phase3)
        self._stop = Event()

    def process_event(self, raw):
        try:
            event = normalize_event(raw)
            event_id = event_id_for(event)
            state, snapshot = self.store.observe(event_id, event, event["ingested_at"])
        except Exception as exc:
            return {"event_id": None, "raw_event": sanitize_source_value(raw), "event_state": "INVALID",
                    "processing_status": "INVALID", "processing_errors": [_error("normalization", exc)],
                    "provenance": {}, "intelligence": {}, "phases_completed": [], "phases_failed": []}
        if state == "DUPLICATE":
            result = deepcopy(snapshot.get("operational_record") or {
                "event_id": event_id, "source": event["source"],
                "source_event_id": event.get("source_event_id"), "provenance": event["provenance"],
                "intelligence": {}, "processing_errors": [], "phases_completed": [], "phases_failed": []})
            result.update({"event_state": "DUPLICATE", "processing_status": "DUPLICATE",
                           "first_seen": snapshot["first_seen"], "last_seen": snapshot["last_seen"],
                           "version": snapshot["version"], "processing_count": snapshot["processing_count"]})
            return result
        self.store.set_status(event_id, "PROCESSING", increment_processing=True)
        try:
            result = self.pipeline.process(event)
            result.update({"event": event, "event_state": state, "first_seen": snapshot["first_seen"],
                           "last_seen": event["ingested_at"], "version": snapshot["version"],
                           "previous_version_fingerprint": snapshot.get("previous_fingerprint"),
                           "processing_count": self.store.get(event_id)["processing_count"]})
            status = "FAILED" if result["processing_status"] == "FAILED" else "PROCESSED"
            self.store.set_status(event_id, status)
            self.store.set_operational_record(event_id, result)
            return result
        except Exception as exc:
            self.store.set_status(event_id, "FAILED")
            return {"event_id": event_id, "source": event["source"], "source_event_id": event.get("source_event_id"),
                    "provenance": event["provenance"], "event_state": state, "processing_status": "FAILED",
                    "processing_errors": [_error("pipeline", exc)], "intelligence": {},
                    "phases_completed": [], "phases_failed": []}

    def process_events(self, events):
        results = []
        if events is None:
            return results
        try:
            iterator = iter(events)
        except Exception as exc:
            return [{"event_id": None, "event_state": "FAILED", "processing_status": "FAILED",
                     "processing_errors": [_error("source", exc)], "intelligence": {},
                     "phases_completed": [], "phases_failed": ["source"]}]
        for _ in range(self.config.max_events_per_cycle):
            try:
                raw = next(iterator)
            except StopIteration:
                break
            except Exception as exc:
                results.append({"event_id": None, "event_state": "FAILED", "processing_status": "FAILED",
                                "processing_errors": [_error("source", exc)], "intelligence": {},
                                "phases_completed": [], "phases_failed": ["source"]})
                break
            try:
                results.append(self.process_event(raw))
            except Exception as exc:  # protect the rest of the batch from unforeseen adapter/pipeline faults
                results.append({"event_id": None, "event_state": "INVALID", "processing_status": "FAILED",
                                "processing_errors": [_error("event", exc)], "intelligence": {},
                                "phases_completed": [], "phases_failed": []})
        return results

    def run_monitoring_cycle(self, source):
        try:
            incoming = source.fetch()
            if incoming is None:
                incoming = []
            return self.process_events(incoming)
        except Exception as exc:
            return [{"event_id": None, "event_state": "FAILED", "processing_status": "FAILED",
                     "source": getattr(source, "name", source.__class__.__name__),
                     "processing_errors": [_error("source", exc)], "intelligence": {},
                     "phases_completed": [], "phases_failed": ["source"]}]

    def run_monitoring(self, source, cycles=None, interval_seconds=None):
        """Poll until stopped or `cycles` is reached; finite cycles are test-safe."""
        interval = self.config.polling_interval_seconds if interval_seconds is None else interval_seconds
        all_cycles, completed = [], 0
        self._stop.clear()
        while not self._stop.is_set() and (cycles is None or completed < cycles):
            all_cycles.append(self.run_monitoring_cycle(source))
            completed += 1
            if cycles is None or completed < cycles:
                self._stop.wait(max(0.0, interval))
        return all_cycles

    def stop(self):
        self._stop.set()


def run_monitoring_cycle(source, runner=None):
    return (runner or MonitoringRunner()).run_monitoring_cycle(source)
