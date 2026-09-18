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
> **Status:** Phase 1 — Environment Setup
> 
> * Initial workspace structure initialized.
> * Python 3.10 virtual environment configured.
> * Foundational libraries installed for baseline verification.
> * Git safety rules and repository tracking structure established.
