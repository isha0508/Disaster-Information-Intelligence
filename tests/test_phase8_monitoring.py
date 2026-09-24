import json

import pytest

from monitoring.identity import event_id_for
from monitoring.normalization import normalize_content, normalize_event, normalize_timestamp
from monitoring.pipeline import IntelligencePipeline
from monitoring.runner import MonitoringRunner
from monitoring.sources import SyntheticEventSource


class StubPipeline:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def process(self, event):
        self.calls.append(event["text"])
        if self.fail:
            raise RuntimeError("expected test failure")
        return {"event_id": event_id_for(event), "source": event["source"],
                "source_event_id": event.get("source_event_id"), "provenance": event["provenance"],
                "processing_status": "PROCESSED", "processing_errors": [], "intelligence": {},
                "phase_status": {"phase4": "completed"}, "phases_completed": ["phase4"],
                "phases_failed": [], "processing_started_at": event["ingested_at"],
                "processing_completed_at": event["ingested_at"], "processing_duration_seconds": 0}


def ev(text="Flood reported.", source_id="a", **kwargs):
    value = {"source": "feed", "source_event_id": source_id, "text": text,
             "observed_at": "2026-01-02T03:00:00-05:00", "source_url": "https://example.invalid/e/1",
             "metadata": {"region": "test"}}
    value.update(kwargs)
    return value


def test_normalization_whitespace_timestamps_and_provenance():
    event = normalize_event(ev("  Flood\n  reported. "), ingested_at="2026-01-02T00:00:00Z")
    assert event["text"] == "Flood reported."
    assert event["observed_at"] == "2026-01-02T08:00:00Z"
    assert event["ingested_at"] == "2026-01-02T00:00:00Z"
    assert event["provenance"]["source_url"] == "https://example.invalid/e/1"
    assert event["original_text"] == "  Flood\n  reported. "


def test_schema_optional_fields_and_missing_content():
    value = normalize_event({"source": "x", "text": "report"})
    assert value["source_event_id"] is None and value["published_at"] is None
    with pytest.raises(ValueError):
        normalize_event({"source": "x", "text": "  "})
    with pytest.raises(ValueError):
        normalize_event(None)


def test_normalization_invalid_timestamp_is_explicit_none_and_repeatable_content():
    assert normalize_timestamp("not-a-date") is None
    assert normalize_content("a\t b\n c") == "a b c"
    assert normalize_event(ev())["content_fingerprint"] == normalize_event(ev())["content_fingerprint"]


def test_metadata_must_be_json_safe():
    with pytest.raises(ValueError):
        normalize_event(ev(metadata={"bad": object()}))


def test_source_credentials_are_not_copied_to_public_records():
    record = MonitoringRunner(pipeline=StubPipeline()).process_event(
        ev(metadata={"api_key": "do-not-store", "safe": "ok"}, authorization="Bearer secret",
           source_url="https://user:password@example.invalid/e?access_token=hide&view=public"))
    encoded = json.dumps(record)
    assert "do-not-store" not in encoded and "Bearer secret" not in encoded
    assert record["event"]["metadata"] == {"safe": "ok"}
    assert record["provenance"]["source_url"] == "https://example.invalid/e?view=public"


def test_event_id_is_deterministic_and_content_sensitive_without_source_id():
    first = normalize_event(ev(source_id=None))
    same = normalize_event(ev(source_id=None))
    changed = normalize_event(ev("Changed report", source_id=None))
    assert event_id_for(first) == event_id_for(same)
    assert event_id_for(first) != event_id_for(changed)


def test_stable_source_identity_keeps_updates_under_same_event_id():
    a = normalize_event(ev("first"))
    b = normalize_event(ev("second"))
    assert event_id_for(a) == event_id_for(b)


def test_new_duplicate_and_update_lifecycle_is_idempotent():
    stub = StubPipeline()
    runner = MonitoringRunner(pipeline=stub)
    one = runner.process_event(ev("first"))
    duplicate = runner.process_event(ev("first"))
    updated = runner.process_event(ev("second"))
    assert [one["event_state"], duplicate["event_state"], updated["event_state"]] == ["NEW", "DUPLICATE", "UPDATED"]
    assert len(stub.calls) == 2
    assert updated["version"] == 2 and updated["previous_version_fingerprint"]
    assert updated["processing_count"] == 2


def test_distinct_stable_ids_are_not_merged_on_same_content():
    runner = MonitoringRunner(pipeline=StubPipeline())
    first = runner.process_event(ev("same", source_id="one"))
    second = runner.process_event(ev("same", source_id="two"))
    assert first["event_id"] != second["event_id"]
    assert second["event_state"] == "NEW"


def test_invalid_record_does_not_crash_batch_and_is_structured():
    results = MonitoringRunner(pipeline=StubPipeline()).process_events([ev(), None, {"source": "x"}])
    assert [r["event_state"] for r in results] == ["NEW", "INVALID", "INVALID"]
    assert results[1]["processing_errors"][0]["phase"] == "normalization"


def test_pipeline_failure_is_isolated_and_state_failed():
    runner = MonitoringRunner(pipeline=StubPipeline(fail=True))
    result = runner.process_event(ev())
    assert result["processing_status"] == "FAILED"
    assert result["processing_errors"][0]["error_type"] == "RuntimeError"
    assert runner.store.get(result["event_id"])["processing_status"] == "FAILED"


def test_phase3_to_7_adapters_preserve_partial_results_and_errors():
    pipeline = IntelligencePipeline(run_phase3=False)
    result = pipeline.process(normalize_event(ev("Flooding in Kathmandu. Need water and shelter.")))
    assert result["phase_status"]["phase3"] == "disabled"
    assert "phase4" in result["intelligence"]
    assert "phase5" in result["intelligence"]
    assert "phase6" in result["intelligence"]
    assert "phase7" in result["intelligence"]
    assert result["processing_status"] in {"PROCESSED", "FAILED"}


def test_source_failure_returns_structured_cycle_result():
    class Broken:
        name = "broken"
        def fetch(self):
            raise OSError("offline")
    result = MonitoringRunner(pipeline=StubPipeline()).run_monitoring_cycle(Broken())[0]
    assert result["event_state"] == "FAILED"
    assert result["processing_errors"][0]["phase"] == "source"


def test_iterable_source_failure_keeps_preceding_event_result():
    def partial():
        yield ev("arrived first")
        raise OSError("stream interrupted")
    results = MonitoringRunner(pipeline=StubPipeline()).process_events(partial())
    assert [item["event_state"] for item in results] == ["NEW", "FAILED"]
    assert results[-1]["processing_errors"][0]["phase"] == "source"


def test_finite_multiple_cycles_duplicate_update_and_invalid():
    source = SyntheticEventSource()
    runner = MonitoringRunner(pipeline=StubPipeline())
    cycles = runner.run_monitoring(source, cycles=2, interval_seconds=0)
    assert len(cycles) == 2
    assert [x["event_state"] for x in cycles[0]] == ["NEW", "NEW"]
    assert [x["event_state"] for x in cycles[1]] == ["DUPLICATE", "UPDATED", "NEW", "INVALID"]


def test_empty_source_and_empty_batch():
    assert MonitoringRunner(pipeline=StubPipeline()).process_events([]) == []
    class Empty:
        def fetch(self): return []
    assert MonitoringRunner(pipeline=StubPipeline()).run_monitoring_cycle(Empty()) == []


def test_operational_results_are_json_serializable_and_retain_provenance():
    result = MonitoringRunner(pipeline=StubPipeline()).process_event(ev())
    serialized = json.dumps(result)
    restored = json.loads(serialized)
    assert restored["provenance"]["source"] == "feed"
    assert restored["processing_status"] == "PROCESSED"


def test_same_synthetic_source_runs_with_stable_event_identity():
    source_one, source_two = SyntheticEventSource(), SyntheticEventSource()
    first_a = source_one.fetch()[0]
    first_b = source_two.fetch()[0]
    assert event_id_for(normalize_event(first_a)) == event_id_for(normalize_event(first_b))
