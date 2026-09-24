"""Isolated orchestration adapters around the existing Phase 3–7 APIs."""

from datetime import datetime, timezone
import json
import re
import time

from monitoring.identity import event_id_for
from monitoring.normalization import normalize_event, utc_now


def _duration(start):
    return round(time.perf_counter() - start, 6)


class IntelligencePipeline:
    """Run available Phase 3–7 APIs while retaining partial results/errors."""
    def __init__(self, geocoder=None, llm_provider=None, run_phase3=True):
        self.geocoder = geocoder
        self.llm_provider = llm_provider
        self.run_phase3 = run_phase3

    def process(self, event):
        started = utc_now()
        timer = time.perf_counter()
        errors, phase_status = [], {}
        result = {"event": event, "event_id": event_id_for(event),
                  "source": event["source"], "source_event_id": event.get("source_event_id"),
                  "provenance": event["provenance"], "intelligence": {},
                  "processing_errors": errors, "phase_status": phase_status,
                  "processing_status": "PROCESSING", "processing_started_at": started,
                  "pipeline_version": "phase8-v1"}

        if self.run_phase3:
            try:
                from models.predict import predict_all
                classification = predict_all(event["text"])
                result["intelligence"]["phase3"] = classification
                phase_status["phase3"] = "completed_with_unavailable_models" if _contains_model_error(classification) else "completed"
            except Exception as exc:
                phase_status["phase3"] = "failed"
                errors.append(_error("phase3", exc))
        else:
            phase_status["phase3"] = "disabled"

        structured = None
        try:
            from nlp.extractor import extract_hybrid, build_structured_record
            structured = build_structured_record(extract_hybrid(event["text"]))
            structured["text"] = event["text"]
            phase_status["phase4"] = "completed"
            result["intelligence"]["phase4"] = structured
        except Exception as exc:
            phase_status["phase4"] = "failed"
            errors.append(_error("phase4", exc))

        enriched = None
        if structured is not None:
            try:
                from intelligence import enrich_incident
                enriched = enrich_incident(structured)
                result["intelligence"]["phase5"] = enriched
                phase_status["phase5"] = "completed"
            except Exception as exc:
                phase_status["phase5"] = "failed"
                errors.append(_error("phase5", exc))
        else:
            phase_status["phase5"] = "skipped_dependency_failed"

        spatial = None
        if enriched is not None:
            try:
                from gis import run_spatial_pipeline
                spatial_result = run_spatial_pipeline([enriched], geocoder=self.geocoder, enrich_phase5=False)
                spatial = spatial_result["incidents"][0]
                result["intelligence"]["phase6"] = spatial_result
                phase_status["phase6"] = "completed"
            except Exception as exc:
                phase_status["phase6"] = "failed"
                errors.append(_error("phase6", exc))
        else:
            phase_status["phase6"] = "skipped_dependency_failed"

        grounding_record = spatial or enriched
        if grounding_record is not None:
            try:
                from llm import LLMConfig, MockLLMProvider, generate_intelligence
                provider = self.llm_provider or MockLLMProvider()
                generated = generate_intelligence(grounding_record, provider=provider, config=LLMConfig(provider="mock"))
                result["intelligence"]["phase7"] = generated
                phase_status["phase7"] = "completed_with_errors" if generated.get("generation_errors") else "completed"
            except Exception as exc:
                phase_status["phase7"] = "failed"
                errors.append(_error("phase7", exc))
        else:
            phase_status["phase7"] = "skipped_dependency_failed"

        result["processing_status"] = "FAILED" if errors and all(v in {"failed", "skipped_dependency_failed"} for v in phase_status.values()) else "PROCESSED"
        result["phases_completed"] = [name for name, status in phase_status.items() if status.startswith("completed")]
        result["phases_failed"] = [name for name, status in phase_status.items() if status == "failed"]
        result["processing_completed_at"] = utc_now()
        result["processing_duration_seconds"] = _duration(timer)
        # Public records are JSON-friendly even when a phase returns unusual scalar types.
        return json.loads(json.dumps(result, default=_json_default, allow_nan=False))


def process_raw_event(raw, pipeline=None):
    """Normalize and process one event, returning an INVALID record on bad input."""
    try:
        event = normalize_event(raw)
    except Exception as exc:
        return {"event_id": None, "raw_event": raw, "event_state": "INVALID",
                "processing_status": "INVALID", "processing_errors": [_error("normalization", exc)],
                "provenance": {}, "intelligence": {}, "phases_completed": [], "phases_failed": []}
    return (pipeline or IntelligencePipeline()).process(event)


def _contains_model_error(value):
    if isinstance(value, dict):
        return any(k in {"CHECKPOINT_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE"} or
                   (k == "error" and isinstance(v, str) and v in {"CHECKPOINT_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE"})
                   for k, v in value.items()) or any(_contains_model_error(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_model_error(item) for item in value)
    return False


def _error(phase, exc):
    message = str(exc)
    message = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", message)
    message = re.sub(r"(?i)(api[_-]?key|token|password|secret)(\s*[:=]\s*)[^\s,;]+",
                     r"\1\2[REDACTED]", message)
    return {"phase": phase, "error_type": type(exc).__name__, "message": message[:500]}


def _json_default(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if hasattr(value, "item"):
        return value.item()
    return str(value)
