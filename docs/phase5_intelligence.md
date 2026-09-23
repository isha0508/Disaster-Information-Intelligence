# Phase 5 — Incident Intelligence & Decision Support

## Overview

Phase 5 implements the **Incident Intelligence & Decision Support Layer**, which transforms Phase 4 structured incident records into enriched operational intelligence records containing severity, urgency, priority, evidence indicators, deduplication/clustering information, resource estimates, and decision-support flags.

## Key Features

### Scoring Engines

- **Severity Scoring (0-100)**: Rule-based scoring considering casualties, displacement, infrastructure damage, rescue involvement, and disaster type context
- **Urgency Scoring (0-100)**: Time-critical assessment based on rescue signals, immediate danger, and resource urgency
- **Priority Scoring (0-100)**: Operational priority combining severity and urgency for human review ranking
- **Confidence Scoring (0-100)**: Evidence quality indicator based on information completeness

### Intelligence Features

- **Deterministic Incident IDs**: Hash-based IDs ensuring same incidents produce same IDs
- **Deduplication**: Text/entity-based duplicate detection using similarity thresholds
- **Clustering**: Lightweight incident clustering for future GIS integration
- **Resource Estimation**: Conservative heuristic estimates distinguishing explicit requests from estimated needs
- **Decision-Support Flags**: Machine-readable flags (CRITICAL_PRIORITY, IMMEDIATE_RESCUE, etc.)

## Architecture

```
Phase 4 Structured Incident → Phase 5 Intelligence → Phase 6 GIS
        (entities, fields)     (severity, urgency,    (geocoding,
                               priority, clustering)   spatial analysis)
```

## Public API

```python
from intelligence import enrich_incident, enrich_incidents

# Single incident enrichment
enriched = enrich_incident(phase4_record)
# Returns enriched incident with severity, urgency, priority, etc.

# Batch processing
batch_enriched = enrich_incidents(phase4_records)
# Returns list of enriched incidents with clustering
```

## Configuration

All scoring weights and thresholds are centralized in `intelligence/config.py`:

- **Severity weights**: casualties (0.35), displaced (0.25), infrastructure (0.20), rescue (0.15), disaster_type (0.05)
- **Urgency weights**: rescue (0.40), casualty_urgency (0.25), immediate_danger (0.20), resource_urgency (0.15)
- **Priority weights**: severity (0.60), urgency (0.40)
- **Thresholds**: CRITICAL (75), HIGH (50), MODERATE (25)

## Design Principles

- **Interpretable**: Every score has explanatory factors showing contribution
- **Deterministic**: Same input produces same output (no random sampling)
- **Conservative**: Heuristic estimates clearly labeled, not presented as exact requirements
- **Evidence-based**: Confidence reflects information quality, not calibrated probability
- **Phase 6 Ready**: Preserves location text, incident IDs, cluster IDs for future GIS integration

## Limitations

- No supervised ML validation (rule-based only due to absence of labeled severity/urgency datasets)
- Heuristic resource estimates not calibrated against real-world logistics
- No geographic clustering (Phase 6 responsibility)
- Confidence scores represent evidence quality, not calibrated probability
- Thresholds are engineering judgments, not scientifically derived

## Files

- `intelligence/__init__.py`: Package initialization and public API
- `intelligence/config.py`: Scoring weights, thresholds, and configuration
- `intelligence/scoring.py`: Severity, urgency, priority, and confidence engines
- `intelligence/clustering.py`: Deduplication and incident clustering
- `intelligence/resources.py`: Resource estimation and prioritization
- `intelligence/incident.py`: Main enrichment functions and utilities
- `tests/test_phase5_intelligence.py`: Comprehensive unit tests
- `evaluation/run_phase5_demonstration.py`: Rule-based demonstration script
- `evaluation/phase5_demonstration_results.json`: Demonstration results artifact

## Dependencies

- Python 3.10+
- No new external dependencies beyond existing project requirements
- Does not require transformer model downloads for basic operation
