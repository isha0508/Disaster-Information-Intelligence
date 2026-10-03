"""Regression tests for structured source evidence alongside Phase 3 predictions."""

from monitoring.type_resolution import canonicalize_disaster_type, resolve_disaster_type
from gis.pipeline import enrich_spatial_record


def test_canonical_disaster_type_aliases_and_unknowns():
    assert canonicalize_disaster_type("EQ") == "earthquake"
    assert canonicalize_disaster_type("flash flood") == "flood"
    assert canonicalize_disaster_type("Hurricane") == "cyclone"
    assert canonicalize_disaster_type("forest fire") == "wildfire"
    assert canonicalize_disaster_type("unclassified event") is None


def test_structured_usgs_earthquake_wins_without_discarding_ml_hurricane():
    event = {"source": "usgs", "metadata": {"feature_type": "earthquake"}}
    prediction = {"disaster_type": {"label": "hurricane", "confidence": 0.9986,
                                    "model": "distilbert_disaster_type"}}
    resolved = resolve_disaster_type(event, prediction)
    assert resolved["canonical_disaster_type"] == "earthquake"
    assert resolved["source_disaster_type"]["value"] == "earthquake"
    assert resolved["ml_disaster_type"]["value"] == "hurricane"
    assert resolved["ml_disaster_type"]["confidence"] == 0.9986
    assert resolved["selected_from"] == "authoritative_source"
    assert resolved["type_agreement_status"] == "DISAGREEMENT"
    assert resolved["type_agreement"] is False
    assert resolved["model_disagreement"] is True
    assert resolved["resolution_method"] == "authoritative_source"


def test_unstructured_event_uses_phase3_prediction_as_canonical():
    resolved = resolve_disaster_type(
        {"source": "news_rss", "metadata": {}},
        {"disaster_type": {"label": "flood", "confidence": 0.81}},
    )
    assert resolved["source_disaster_type"] is None
    assert resolved["canonical_disaster_type"] == "flood"
    assert resolved["selected_from"] == "phase3_ml_fallback"
    assert resolved["type_agreement_status"] == "ML_ONLY"


def test_gdacs_type_is_canonicalized_and_unknown_is_not_fabricated():
    known = resolve_disaster_type({"source": "gdacs", "disaster_type": "EQ"}, {})
    unknown = resolve_disaster_type({"source": "gdacs", "disaster_type": "XYZ"},
                                    {"disaster_type": {"label": "hurricane", "confidence": .99}})
    assert known["source_disaster_type"]["value"] == "EQ"
    assert known["canonical_disaster_type"] == "earthquake"
    assert unknown["source_disaster_type"]["value"] == "XYZ"
    assert unknown["canonical_disaster_type"] == "cyclone"
    assert unknown["ml_disaster_type"]["value"] == "hurricane"
    assert unknown["selected_from"] == "phase3_ml_fallback"
    assert unknown["source_disaster_type"]["canonical_value"] is None
    gdacs_flood = resolve_disaster_type({"source": "gdacs", "disaster_type": "flood"},
        {"disaster_type": {"label": "hurricane", "confidence": .9996}})
    assert gdacs_flood["canonical_disaster_type"] == "flood"
    assert gdacs_flood["type_agreement_status"] == "DISAGREEMENT"


def test_source_ml_agreement_retains_source_priority():
    resolved = resolve_disaster_type({"source": "usgs", "metadata": {"feature_type": "earthquake"}},
                                    {"disaster_type": {"label": "earthquakes", "confidence": .72}})
    assert resolved["canonical_disaster_type"] == "earthquake"
    assert resolved["type_agreement"] is True
    assert resolved["type_agreement_status"] == "AGREEMENT"
    assert resolved["model_disagreement"] is False


def test_phase4_type_is_preserved_and_used_before_ml_fallback():
    resolved = resolve_disaster_type({"source": "news_rss"},
        {"disaster_type": {"label": "hurricane", "confidence": .9}},
        ["Flooding"])
    assert resolved["phase4_disaster_type"]["value"] == ["Flooding"]
    assert resolved["canonical_disaster_type"] == "flood"
    assert resolved["selected_from"] == "phase4_nlp_fallback"
    assert resolved["type_agreement_status"] == "ML_ONLY"


def test_authoritative_source_type_takes_precedence_over_phase4_type():
    resolved = resolve_disaster_type(
        {"source": "usgs", "metadata": {"feature_type": "earthquake"}},
        {"disaster_type": {"label": "hurricane", "confidence": .99}},
        "flood",
    )
    assert resolved["phase4_disaster_type"]["canonical_value"] == "flood"
    assert resolved["canonical_disaster_type"] == "earthquake"
    assert resolved["type_agreement_status"] == "DISAGREEMENT"


def test_unmappable_authoritative_type_emits_warning_without_crashing():
    resolved = resolve_disaster_type({"source": "gdacs", "disaster_type": {"unexpected": "value"}}, {})
    assert resolved["source_disaster_type"]["status"] == "unresolved"
    assert resolved["canonical_disaster_type"] is None
    assert resolved["warning"]


def test_missing_source_and_ml_types_are_explicitly_unresolved():
    resolved = resolve_disaster_type({"source": "news_rss"}, {})
    assert resolved["canonical_disaster_type"] is None
    assert resolved["type_agreement_status"] == "UNRESOLVED"
    assert resolved["resolution_method"] == "unresolved"


def test_type_normalization_supports_requested_source_variants():
    cases = {"earthquakes": "earthquake", "flooding": "flood",
             "tropical cyclone": "cyclone", "volcano": "volcanic_eruption",
             "fire": "fire", "wildfire": "wildfire"}
    assert {value: canonicalize_disaster_type(value) for value in cases} == cases


def test_structured_source_coordinates_skip_geocoder_and_keep_provenance():
    class MustNotGeocode:
        source = "must_not_be_called"

        def geocode(self, location):
            raise AssertionError("source coordinates should be used directly")

    record = {"incident_id": "i-1", "location": [], "location_text": "old",
              "source_location": {"text": "1 km WSW of Pāhala, Hawaii", "latitude": 19.196,
                                  "longitude": -155.494, "source": "usgs",
                                  "provenance": "authoritative_structured_metadata"},
              "nlp_location_entities": []}
    result = enrich_spatial_record(record, MustNotGeocode())
    assert result["latitude"] == 19.196 and result["longitude"] == -155.494
    assert result["coordinate_source"] == "source_metadata"
    assert result["geocoding_status"] == "success"
    assert result["location_text"] == "1 km WSW of Pāhala, Hawaii"
    assert result["nlp_location_entities"] == []


def test_authoritative_coordinates_win_when_phase4_has_location_evidence():
    class MustNotGeocode:
        source = "must_not_be_called"

        def geocode(self, location):
            raise AssertionError("authoritative coordinates must not be geocoded over")

    result = enrich_spatial_record({
        "incident_id": "i-2", "location": ["NLP place"],
        "nlp_location_entities": ["NLP place"],
        "source_location": {"latitude": 12.5, "longitude": 45.6,
                             "provenance": "authoritative_structured_metadata"},
    }, MustNotGeocode())
    assert (result["latitude"], result["longitude"]) == (12.5, 45.6)
    assert result["coordinate_source"] == "source_metadata"
    assert result["geocoding_status"] == "success"


def test_phase4_location_candidate_is_used_without_fabricating_coordinates():
    from gis.pipeline import enrich_spatial_record
    from gis.geocoding import OfflineGeocoder

    result = enrich_spatial_record({"location": [], "nlp_location_entities": ["Kozan, Türkiye"]},
                                   OfflineGeocoder())
    assert result["location_text"] == "Kozan, Türkiye"
    assert result["normalized_location"] == "Kozan, Türkiye"
    assert result["geocoding_status"] == "failed"
    assert result["geocoding_source"] == "offline_gazetteer"
    assert result["latitude"] is None and result["longitude"] is None


def test_no_location_evidence_is_unresolved_without_geocoding_attempt():
    result = enrich_spatial_record({"location": [], "nlp_location_entities": []})
    assert result["geocoding_status"] == "unresolved"
    assert result["geocoding_source"] is None
    assert result["latitude"] is None and result["longitude"] is None


def test_offset_description_does_not_geocode_the_reference_place_as_epicenter():
    class MustNotGeocode:
        source = "test"

        def geocode(self, location):
            raise AssertionError("offset reference must not be plotted as epicenter")

    result = enrich_spatial_record({"nlp_location_entities": [
        "109 km NE of Cruz Bay, U.S. Virgin Islands"]}, MustNotGeocode())
    assert result["location_text"] == "109 km NE of Cruz Bay, U.S. Virgin Islands"
    assert result["geocoding_status"] == "unresolved"
    assert result["latitude"] is None and result["longitude"] is None


def test_mastodon_explicit_gps_coordinate_wins_over_nlp_geocoder():
    class MustNotGeocode:
        source = "must_not_be_called"

        def geocode(self, location):
            raise AssertionError("source GPS coordinates must take precedence")

    result = enrich_spatial_record({"nlp_location_entities": ["Kamchatka Peninsula, Russia"],
        "source_location": {"latitude": 52.3167, "longitude": 160.3332,
            "provenance": "explicit_source_text_gps", "coordinate_source": "source_text_gps"}},
        MustNotGeocode())
    assert (result["latitude"], result["longitude"]) == (52.3167, 160.3332)
    assert result["coordinate_source"] == "source_text_gps"


def test_usgs_adapter_exposes_structured_type_and_location_metadata():
    from monitoring.real_sources import usgs_feature_to_event

    event = usgs_feature_to_event({"type": "Feature", "id": "usgs-1",
        "properties": {"type": "earthquake", "title": "M 2.3 - 1 km WSW of Pāhala, Hawaii",
                       "place": "1 km WSW of Pāhala, Hawaii", "mag": 2.3},
        "geometry": {"type": "Point", "coordinates": [-155.494, 19.196, 4.0]}})
    resolved = resolve_disaster_type({"source": event["source"], "metadata": event["metadata"]})
    assert resolved["canonical_disaster_type"] == "earthquake"
    assert event["location_text"] == "1 km WSW of Pāhala, Hawaii"
    assert (event["latitude"], event["longitude"]) == (19.196, -155.494)


def test_live_pipeline_uses_source_type_location_and_coordinates_end_to_end():
    from monitoring.pipeline import process_raw_event

    result = process_raw_event({"source": "usgs", "source_event_id": "fixture-usgs-1",
        "text": "M 2.3 - 1 km WSW of Pāhala, Hawaii", "location_text": "1 km WSW of Pāhala, Hawaii",
        "latitude": 19.196, "longitude": -155.494,
        "metadata": {"feature_type": "earthquake"}})
    intelligence = result["intelligence"]
    assert intelligence["phase5"]["disaster_type"] == "earthquake"
    assert intelligence["phase5"]["location_text"] == "1 km WSW of Pāhala, Hawaii"
    assert result["source_location"]["provenance"] == "authoritative_structured_metadata"
    assert isinstance(intelligence["phase4"]["nlp_location_entities"], list)
    spatial = intelligence["phase6"]["incidents"][0]
    assert (spatial["latitude"], spatial["longitude"]) == (19.196, -155.494)
    assert spatial["coordinate_source"] == "source_metadata"


def test_pipeline_keeps_incorrect_phase3_prediction_visible(monkeypatch):
    import sys
    import types
    from monitoring.pipeline import process_raw_event

    predictor = types.ModuleType("models.predict")
    predictor.predict_all = lambda text: {"disaster_type": {"label": "hurricane",
        "confidence": 0.9987, "model": "distilbert_disaster_type"}}
    monkeypatch.setitem(sys.modules, "models.predict", predictor)
    result = process_raw_event({"source": "usgs", "source_event_id": "fixture-usgs-disagreement",
        "text": "M 4.9 - Drake Passage", "location_text": "Drake Passage",
        "metadata": {"feature_type": "earthquake"}})
    assert result["intelligence"]["phase3"]["disaster_type"]["label"] == "hurricane"
    assert result["type_resolution"]["canonical_disaster_type"] == "earthquake"
    assert result["type_resolution"]["ml_confidence"] == 0.9987
    assert result["type_resolution"]["model_disagreement"] is True
    assert result["intelligence"]["phase5"]["disaster_type"] == "earthquake"


def test_phase10_adapter_exposes_type_disagreement_fields():
    from dashboard.adapters import adapt_incident

    resolution = resolve_disaster_type({"source": "gdacs", "disaster_type": "FL"},
        {"disaster_type": {"label": "hurricane", "confidence": .9996}})
    view = adapt_incident({"source": "gdacs", "type_resolution": resolution,
                           "intelligence": {"phase5": {"incident_id": "incident-1",
                               "disaster_type": "flood", "canonical_disaster_type": "flood",
                               "type_resolution": resolution}}})
    assert view["canonical_disaster_type"] == "flood"
    assert view["source_authority"] == "GDACS"
    assert view["ml_disaster_type"]["value"] == "hurricane"
    assert view["ml_confidence"] == .9996
    assert view["type_agreement_status"] == "DISAGREEMENT"
    assert view["model_disagreement"] is True
    assert "GDACS" in view["resolution_rationale"]
