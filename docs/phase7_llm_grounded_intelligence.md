# Phase 7 — LLM / Generative Intelligence & Grounded Decision Support

## Objective and source of truth

Phase 7 turns Phase 5 deterministic incident intelligence and optional Phase 6 spatial intelligence into concise explanations, recommendations, and human-readable reports. Provider output is narrative only. Incident identity, Phase 5 scores, evidence lists, resource data, coordinates, geocoding status, spatial membership, and uncertainty are assembled from the supplied records by deterministic code. Phase 7 does not re-extract facts from raw text.

## Architecture and public API

- `llm/config.py`: environment-backed settings; mock is the default.
- `llm/schemas.py`: stable result fields and runtime validators.
- `llm/grounding.py`: bounded context construction with provenance categories.
- `llm/prompts.py`: centralized task prompts and grounding rules.
- `llm/providers.py`: provider interface, deterministic mock, optional OpenAI-compatible HTTP adapter.
- `llm/generator.py`: orchestration, error handling, grounding checks, batch support.
- `llm/reports.py`: human-readable Markdown report renderer.

```python
from llm import (
    build_grounded_context, generate_intelligence,
    generate_intelligence_batch, generate_incident_summary,
    explain_priority, generate_recommendations, generate_situation_report,
)
```

`generate_intelligence(record, provider=None, config=None, spatial_context=None)` returns a mapping containing incident ID; narrative summary and assessment; priority explanation; structured key evidence; recommendations; separate resource request and estimate fields; spatial context; deterministic intelligence values; uncertainties; confidence note; grounding warnings; generation errors; and metadata. `generate_intelligence_batch` returns one result per input in input order and isolates item failures.

Phase 4 records can be passed as Phase 5/6-compatible records, but Phase 7 will not score or enrich them. Run Phase 5/6 upstream first when those scores/spatial fields are needed. A complete Phase 6 pipeline result may be passed as `spatial_context`; it is joined by incident ID.

## Grounded context and evidence categories

The context builder exposes `evidence` (Phase 4 structured mentions), `computed_intelligence` (Phase 5 scores, factors, flags and identifiers), `resources` (explicit requests separate from heuristic estimates), `spatial` (Phase 6 status and summaries), and `unknowns_and_uncertainties`. Raw text may be carried as `source_text`, marked `unvalidated_raw_text_not_reextracted_by_phase7`; prompts forbid treating it as newly extracted structured evidence. Missing fields are recorded where they affect interpretation. Oversized context is bounded and marked truncated.

Phase 4 extracted fields are reported as structured/extracted evidence, not independently fact-checked truth. Phase 5 confidence remains an evidence-quality measure, not probability. Spatial coordinates are provided to the model only when geocoding status is `success` and WGS84 range validation succeeds.

## Prompts and providers

All prompt templates are centralized in `llm/prompts.py` and prohibit invention or score changes, preserve uncertainty, and constrain recommended actions to decision support. `MockLLMProvider` is deterministic and needs no credentials, network, or model download. It is the default for tests and the demonstration.

An optional `OpenAICompatibleProvider` uses Python's standard-library HTTP client against a chat-completions-compatible endpoint. It is selected with `LLM_PROVIDER=openai_compatible` (or `openai`) and requires `LLM_API_KEY`; no key is stored in code or generation metadata. Set `LLM_MODEL` and optionally `LLM_API_BASE_URL` for the selected endpoint. Without a key or on provider errors, generation returns a controlled mock fallback and reports the failure in `generation_errors`. External calls were not required for tests or demos.

Supported settings include `LLM_MAX_CONTEXT_CHARS` (minimum 2,048 characters), `LLM_TEMPERATURE`, `LLM_MAX_OUTPUT_TOKENS`, `LLM_GROUNDING_ENABLED`, `LLM_RECOMMENDATIONS_ENABLED`, `LLM_SPATIAL_CONTEXT_ENABLED`, and `LLM_TIMEOUT_SECONDS`. Invalid values fall back to safe defaults or are bounded. API keys never appear in output.

## Output validation and grounding consistency

Provider-authored fields are limited to narrative summary, assessment, priority explanation, and recommendations. Required narrative fields are type-checked; malformed output triggers a mock fallback. All operational values are code-owned and copied from Phase 5/6, so model output cannot replace them. Lightweight grounding consistency checks flag unsupported numeric claims and coordinate-confirmation language when geocoding is unresolved. These checks do not prove factual correctness or detect every hallucination.

## Resource interpretation

`resource_summary.explicit_requests` copies explicit request mentions. `estimated_needs` copies Phase 5 estimates in a separate field and is labeled `heuristic_estimate` with a planning-only disclaimer. Reports render them in different sections. Estimates are not confirmed requests, and requests are not converted into verified supply quantities.

## Spatial interpretation

The spatial result carries normalized/source location text, coordinates only when geocoding succeeded, geocoding status/source, spatial cluster ID/size, hotspot membership/count, and spatial area priority when provided. Failed, pending, ambiguous, or absent geocoding stays unresolved and contributes an uncertainty. Phase 5 `cluster_id` is not treated as a geographic cluster. Coordinates supplied by a provider are only as reliable as that provider; range validation is not geographic truth verification.

## Situation reports

`generate_situation_report` renders a Markdown report with overview, summary, human/infrastructure impact, rescue, explicit resource requests, heuristic estimates, scores, priority explanation, spatial context, recommendations, and uncertainties. Missing information is labeled unavailable. Recommendations are explicitly for human review and do not dispatch resources.

## Configuration and versioning

Generation metadata contains Phase 7 version `1.0`, provider, model, generation mode, UTC timestamp, grounding setting, and a false `api_key_included` marker. Credentials are never recorded. Default model name is `mock-phase7-v1`.

## Testing and demonstration

```powershell
pytest tests/test_phase7_llm.py -v
pytest tests/test_phase5_intelligence.py -v
pytest tests/test_phase6_gis.py -v
python evaluation/run_phase7_demonstration.py
```

The demonstration fixture uses synthetic coordinate mappings and mock generation; its artifact explicitly says that geographic accuracy and real LLM quality were not evaluated.

## Limitations

- LLM outputs are not guaranteed to be hallucination-free.
- Grounding checks are lightweight consistency checks, not formal verification.
- Recommendations support human decisions; they are not autonomous commands.
- Heuristic resource estimates are not confirmed requirements.
- Confidence scores are not calibrated probabilities.
- Spatial coordinate reliability depends on the geocoder and its provenance.
- The mock provider demonstrates integration behavior, not production LLM quality.
- No autonomous dispatch, live monitoring, real-time alerting, or provider-specific fine-tuning is implemented.
- The optional HTTP adapter depends on an externally configured compatible service, credentials, and network access; this was not tested against a live service.
