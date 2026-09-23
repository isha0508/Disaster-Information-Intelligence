# Disaster Information Intelligence and Decision Support System

## 1. Project Description
The **Disaster Information Intelligence and Decision Support System** is an NLP-driven emergency response and situational awareness platform designed for disaster management. The system ingests and analyzes unstructured text streams—including social media updates, news feeds, and public incident reports—to extract actionable intelligence during catastrophic events.

## 2. Main Objective
To transform chaotic, high-volume real-time unstructured text into structured, geocoded, and prioritized crisis intelligence. This enables emergency response agencies, relief organizations, and decision-makers to rapidly allocate resources, prioritize rescue operations, and identify high-risk zones during disasters.

## 3. Planned Technology Stack
* **Programming Language:** Python 3.10
* **Data Processing & Scientific Computing:** NumPy, Pandas
* **Machine Learning & Classical NLP:** Scikit-learn, SpaCy, NLTK
* **Deep Learning & Transformers (Future Phases):** PyTorch, Hugging Face Transformers
* **GIS & Spatial Analysis (Future Phases):** GeoPandas, Shapely, Folium / Leaflet
* **Database & Caching (Future Phases):** SQLite / PostgreSQL (PostGIS)
* **Backend API (Future Phases):** FastAPI / Flask
* **Dashboard & Visualizations (Future Phases):** Streamlit / React, Matplotlib, Seaborn

## 4. Planned Modules
1. **Data Ingestion & Preprocessing:** Noise filtering, tokenization, lemmatization, and text normalization.
2. **Disaster Relevance Classification:** Binary filtering to distinguish genuine crisis alerts from irrelevant chatter.
3. **Disaster Type Classification:** Multi-class classification (e.g., Flood, Earthquake, Hurricane, Wildfire).
4. **Disaster-Specific Information Extraction (NER):**
   * Casualties and injured count extraction
   * Infrastructure and structural damage reports
   * Emergency resource requests (food, water, medical supplies, boats)
   * Urgent rescue-request detection
5. **Urgency & Severity Scoring:** Priority ranking engine to flag time-critical emergencies.
6. **Location Extraction & Geocoding:** Extraction of toponyms and geocoding to geographic coordinates.
7. **GIS & Spatial Intelligence:** Hazard mapping, high-risk zone clustering, and spatial density heatmaps.
8. **Resource Estimation & Decision Support:** Automated logistics recommendations based on incident intensity.
9. **Dashboard & Alert Notification Engine:** Interactive situational awareness UI and automated emergency alerts.

## 5. Current Development Status

### Phase 1 — Environment & Project Setup
- Initial repository and workspace structure established.
- Python 3.10 development environment configured.
- Foundational libraries installed and verified.
- Git safety rules and repository tracking structure established.

**Status:** Complete

### Phase 2 — Data Preprocessing & Dataset Preparation
- HumAID disaster-related textual data investigated and prepared.
- Reusable dataset loading and text-cleaning utilities implemented.
- Exploratory data analysis performed.
- Automated tests developed for the preprocessing and text-cleaning pipeline.

**Status:** Complete

### Phase 3 — Disaster Information Classification
Phase 3 established the classification layer of the system.

#### Relevance Classification
A TF-IDF + Logistic Regression baseline was developed to distinguish relevant disaster/humanitarian information from irrelevant content.

Reported test performance:
- Accuracy: **0.895**
- Macro F1: **0.741**

#### Humanitarian Category Classification
Both classical and transformer-based approaches were investigated:
- TF-IDF + Logistic Regression baseline
- DistilBERT transformer classifier

The full DistilBERT experiment achieved:
- Test Accuracy: **0.7633**
- Test Macro F1: **0.749**

The TF-IDF humanitarian baseline achieved:
- Test Macro F1: **0.712**

#### Disaster Type Classification
A DistilBERT classifier was trained using the **HumAID-event-type** dataset for disaster-type classification.

The implemented disaster-type categories include:
- Earthquake
- Fire
- Flood
- Hurricane

The trained disaster-type model is retained as the canonical Phase 3 disaster-type experiment.

> Verified disaster-type test metrics are not currently reported because a corresponding verified metrics artifact was not available in the completed experiment outputs.

**Status:** Complete

### Phase 4 — Hybrid NER & Information Extraction
Phase 4 established a hybrid information extraction pipeline for extracting disaster-specific information from unstructured text.

The approach combines:
- General-purpose Named Entity Recognition
- Disaster-specific rules
- Regular expressions
- Gazetteers
- Entity normalization
- Entity merging
- Entity linking
- Structured incident generation

The approved extraction schema includes:
- `LOCATION`
- `CASUALTY`
- `DISPLACED`
- `REQUEST`
- `RESOURCE`
- `RESCUE`
- `DISASTER_TYPE`
- `ORGANIZATION`
- `PERSON`
- `NUMBER`
- `INFRASTRUCTURE`

The Phase 4 pipeline produced structured entities and incident-level JSON outputs through experimental extraction runs.

A gold-standard annotation template was also prepared for future formal evaluation.

> A completed gold-standard annotation dataset and verified gold-standard precision, recall, and F1 results are not currently available. Therefore, no gold-standard NER performance values are reported.

**Status:** Complete

### Phase 5 — Incident Intelligence & Decision Support
Phase 5 implements the Incident Intelligence & Decision Support Layer, which transforms Phase 4 structured incident records into enriched operational intelligence records.

The implementation includes:
- **Severity Scoring (0-100)**: Rule-based scoring considering casualties, displacement, infrastructure damage, rescue involvement, and disaster type context
- **Urgency Scoring (0-100)**: Time-critical assessment based on rescue signals, immediate danger, and resource urgency
- **Priority Scoring (0-100)**: Operational priority combining severity and urgency for human review ranking
- **Confidence Scoring (0-100)**: Evidence quality indicator based on information completeness
- **Deduplication**: Text/entity-based duplicate detection using similarity thresholds
- **Clustering**: Lightweight incident clustering for future GIS integration
- **Resource Estimation**: Conservative heuristic estimates distinguishing explicit requests from estimated needs
- **Decision-Support Flags**: Machine-readable flags (CRITICAL_PRIORITY, IMMEDIATE_RESCUE, etc.)

All scoring is rule-based and interpretable. No supervised ML models are used due to the absence of labeled severity/urgency datasets.

> The Phase 5 intelligence system uses interpretable rule-based scoring. No supervised validation is performed due to the absence of labeled severity/urgency datasets. Confidence scores represent evidence quality, not calibrated probability.

**Status:** Complete

### Phase 6 — GIS & Spatial Intelligence
Phase 6 adds conservative location normalization, a provider-neutral geocoding interface, WGS84 coordinate validation, Haversine distance, configurable spatial clustering, interpretable hotspot and area-priority summaries, and GeoJSON-compatible incident points. It preserves Phase 4/5 fields and uses an offline geocoder by default; unresolved locations receive no fabricated coordinates.

The Phase 6 demonstration uses synthetic coordinates to exercise the algorithms. It does not validate real-world geocoding accuracy. See [Phase 6 GIS & Spatial Intelligence](docs/phase6_gis_spatial_intelligence.md) for configuration, formulas, limitations, tests, and integration guidance.

**Status:** Complete

---

## 6. Complete System Pipeline
```text
PHASE 1
Project Foundation
        ↓
PHASE 2
Data Preprocessing & EDA
        ↓
PHASE 3
Disaster Classification
        ↓
PHASE 4
Hybrid Information Extraction
        ↓
Structured Disaster Information
        ↓
PHASE 5
Incident Intelligence & Decision Support
        ↓
PHASE 6
GIS & Spatial Intelligence
        ↓
PHASE 7
LLM / Generative Intelligence
        ↓
PHASE 8
Live Data Ingestion & Continuous Monitoring
        ↓
PHASE 9
Real-Time Alerting & Early Warning
        ↓
PHASE 10
Operational Dashboard & Visualization
        ↓
PHASE 11
Database + API + Integration + Deployment
        ↓
PHASE 12
End-to-End Evaluation & Production Readiness
End-to-End Evaluation & Production Readiness
```