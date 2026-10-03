"""Location-evidence propagation tests across the existing live data path."""

import sys
from types import ModuleType

import pytest

from api.routes.events import event_detail
from dashboard.live_data import _adapt_persisted_event
from database import DatabaseRepository
from gis.geocoding import OfflineGeocoder
from monitoring.pipeline import IntelligencePipeline, process_raw_event


@pytest.mark.parametrize("place", ["Kozan, Türkiye", "Cruz Bay, U.S. Virgin Islands"])
def test_mastodon_location_evidence_reaches_phase6_sqlite_api_and_dashboard(
        tmp_path, monkeypatch, place):
    """A Phase 4 location mention remains evidence without becoming coordinates."""
    from nlp import extractor

    text = f"An earthquake was reported near {place}."
    start = text.index(place)

    class StubNER:
        def __call__(self, _text):
            return [{"entity_group": "LOCATION", "start": start, "end": start + len(place),
                     "score": 0.99}]

    monkeypatch.setattr(extractor, "_get_ner_pipeline", lambda: StubNER())
    predictor = ModuleType("models.predict")
    predictor.predict_all = lambda _text: {"disaster_type": {"label": "earthquake", "confidence": 0.8}}
    monkeypatch.setitem(sys.modules, "models.predict", predictor)

    processed = process_raw_event({
        "source": "mastodon", "source_event_id": f"fixture-{place}", "text": text,
        "metadata": {"post_uri": f"https://example.invalid/{place}"},
    }, IntelligencePipeline(geocoder=OfflineGeocoder(), run_phase3=True))

    phase4 = processed["intelligence"]["phase4"]
    assert place in phase4["location"]
    assert any(entity["label"] == "LOCATION" and entity["source"] == "general_ner"
               for entity in phase4["entities"])
    phase6 = processed["intelligence"]["phase6"]["incidents"][0]
    assert phase6["nlp_location_entities"] == [place]
    assert phase6["location_text"] == place
    assert phase6["geocoding_status"] == "failed"
    assert phase6["geocoding_source"] == "offline_gazetteer"
    assert phase6["coordinate_source"] is None
    assert phase6["latitude"] is None and phase6["longitude"] is None

    # All writes here are to the test's isolated temporary SQLite file.
    repository = DatabaseRepository(tmp_path / "location-evidence.sqlite3")
    saved = repository.upsert_event(processed)
    persisted = repository.get_event(saved.event_id)
    evidence = persisted["location_evidence"]
    assert evidence["phase4_location_entities"] == [place]
    assert evidence["geocoding_status"] == "failed"
    assert evidence["coordinate_source"] is None
    assert evidence["geocoded_latitude"] is None and evidence["geocoded_longitude"] is None

    api_result = event_detail(repository, saved.event_id)
    assert api_result["item"]["location_evidence"]["phase4_location_entities"] == [place]
    dashboard_view = _adapt_persisted_event(persisted)
    assert dashboard_view["location_text"] is None
    assert dashboard_view["location_evidence_text"] == place
    assert dashboard_view["latitude"] is None and dashboard_view["longitude"] is None


def test_dashboard_preserves_authoritative_coordinates_with_nlp_location(tmp_path):
    """NLP evidence cannot override persisted authoritative source coordinates."""
    from gis.pipeline import enrich_spatial_record

    enriched = enrich_spatial_record({
        "incident_id": "incident-authoritative", "location": ["NLP place"],
        "nlp_location_entities": ["NLP place"],
        "source_location": {"text": "Source place", "latitude": 19.1, "longitude": 72.8,
                             "provenance": "authoritative_structured_metadata"},
    }, OfflineGeocoder())
    assert enriched["coordinate_source"] == "source_metadata"
    assert (enriched["latitude"], enriched["longitude"]) == (19.1, 72.8)


def test_dashboard_row_shows_extracted_evidence_without_claiming_resolution():
    from dashboard.live_view import render_live_dashboard

    page = render_live_dashboard()
    assert "Unresolved')}${x.location_evidence_text?" in page
    assert "coordinates unavailable" in page
