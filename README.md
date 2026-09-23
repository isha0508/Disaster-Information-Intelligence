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

---

## 6. Current System Pipeline

The completed Phase 1–4 components form the following processing pipeline:

Raw Disaster Text
        ↓
Data Preprocessing
        ↓
Relevance Classification
        ↓
Humanitarian Category Classification
        ↓
Disaster Type Classification
        ↓
Hybrid Information Extraction
        ↓
Structured Disaster Information
        ↓
Phase 5: Intelligence & Decision Support
        ↓
Phase 6: GIS & Spatial Intelligence
        ↓
Phase 7: LLM / Generative Intelligenced.