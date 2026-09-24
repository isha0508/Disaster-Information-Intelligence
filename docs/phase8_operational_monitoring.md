# Phase 8 — Operational Monitoring

## Overview

Phase 8 adds a local operational ingestion and orchestration layer over existing Phase 3–7 APIs. It normalizes source events, preserves source provenance, assigns deterministic event identity, tracks repetition and versions, invokes available intelligence stages, and emits JSON-safe operational records. The included fixture and demonstration are synthetic and finite.

## Architecture and event lifecycle

`EventSource.fetch()` yields raw mappings. `MonitoringRunner` normalizes one item at a time, records it in the replaceable in-memory `EventStateStore`, and invokes `IntelligencePipeline` only for new or changed content.

- `NEW → PROCESSING → PROCESSED` for first-seen valid events.
- `DUPLICATE` for unchanged content under the same event identity; cached intelligence is returned without rerunning phases.
- `UPDATED → PROCESSING → PROCESSED` when content changes under a stable source event ID. The prior fingerprint and incremented version are retained.
- `INVALID` for records that cannot be normalized (for example, missing source/content).
- `FAILED` for source or whole-pipeline failures. Individual phase errors are recorded while later independent phases continue when their prerequisites exist.

Without a stable source event ID, identity is a SHA-256-derived key over normalized source and case-folded, whitespace-normalized content. With a stable ID, identity is derived from normalized source plus that ID; a changed fingerprint then becomes an update. Two different stable source IDs are never merged just because their content matches. Similarity-based matching is intentionally absent.

The Phase 8 `event_id` represents a source event/version lineage. It is distinct from Phase 5 `incident_id`, Phase 5 `cluster_id`, and Phase 6 `spatial_cluster_id`.

## Source adapters and provenance

Implement `EventSource.fetch()` to provide an iterable of raw mappings. `SyntheticEventSource` is the guaranteed no-network fixture; its names, event content, and metadata are explicitly synthetic. No live/RSS adapter is included. An external adapter can later implement the same interface while retaining source URLs and retrieval metadata; it must handle its own credentials safely.

Normalized records preserve source, source event ID, original and whitespace-normalized text, observed/published raw values and normalized UTC values, ingestion time, source URL, metadata, and raw input. Invalid timestamps become `null`; naive timestamps are interpreted as UTC. Missing optional values remain null. Processing timestamps are separate from source event timestamps.

## Phase 3–7 integration

The orchestration invokes `models.predict.predict_all(text)` for Phase 3. Missing models or dependencies are reported and no prediction is fabricated. It uses `nlp.extractor.extract_hybrid` and `build_structured_record` for Phase 4, `intelligence.enrich_incident` for Phase 5, `gis.run_spatial_pipeline` for Phase 6, and `llm.generate_intelligence` with the deterministic `MockLLMProvider` by default for Phase 7. Every stage records completion/failure status and structured errors. Phase 6 uses the existing offline geocoder default unless one is injected; no coordinates are invented.

Phase 5 event/incident deduplication and clustering are downstream incident intelligence. Phase 8 deduplication only prevents repeated processing of source events. Phase 6 spatial clusters are geographic groupings, not source-event identity.

## Continuous monitoring and public API

Use `MonitoringRunner.process_event`, `process_events`, `run_monitoring_cycle(source)`, or `run_monitoring(source, cycles=N, interval_seconds=...)`. Omit `cycles` for continued polling, and call `stop()` from another thread for graceful shutdown. Finite cycles are preferred in tests and demonstrations. Per-event exceptions do not stop the batch; source fetch errors become a structured cycle result.

The public package exports `MonitoringConfig`, `MonitoringRunner`, `EventSource`, `SyntheticEventSource`, `EventStateStore`, and `run_monitoring_cycle`. Configuration currently covers polling interval, maximum events per cycle, and whether Phase 3 prediction is enabled.

## Determinism, errors, and serialization

Identity and content normalization are deterministic. Rule/model outputs retain their original phase semantics. Processing and ingestion timestamps and elapsed times are intentionally dynamic. Output timestamps are ISO-8601 strings; operational results use JSON-safe structures. A phase failure is associated with its name, exception class, and bounded message. Later phases continue when their inputs are available, preserving partial results.

## Limitations and future phases

- The synthetic source demonstrates mechanics, not real-world ingestion or source quality.
- State is in memory and is lost when the process exits; Phase 11 should replace it with persistent storage.
- External/live source adapters are not included or validated.
- Phase 3 model artifacts/dependencies may be unavailable; this is surfaced in its phase status.
- Phase 4 may fall back to its existing rule extraction when optional NER resources are unavailable.
- Polling is a local operational foundation, not alerting (Phase 9), a dashboard (Phase 10), or production deployment (Phase 11).
- Phase 12 is responsible for end-to-end production evaluation; no real-world accuracy claims are made here.

Phase 9 can consume the operational stream for alert policies, Phase 10 can present records in a dashboard, and Phase 11 can add durable state, APIs, and deployment. Phase 7 can consume the preserved Phase 5/6 intelligence alongside source provenance.

## Run and test

```powershell
python evaluation\run_phase8_demonstration.py
pytest tests\test_phase8_monitoring.py -v
```
