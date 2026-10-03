import json
from datetime import datetime, timezone

import pytest

from monitoring.identity import event_id_for
from monitoring.live import LiveMonitoringSession
from monitoring.normalization import normalize_event
from monitoring.real_sources import (GDACSEventSource, MultiSource, NewsRSSSource,
                                     RealSourceConfig, SourceFetchError,
                                     USGSEarthquakeSource, gdacs_item_to_event,
                                     parse_feed, rss_entry_to_event, usgs_feature_to_event)
from monitoring.runner import MonitoringRunner


NOW = "2026-09-25T12:00:00Z"


def fake_http(payload, content_type="application/json", retrieved_at=NOW):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return payload, content_type, retrieved_at


def usgs_feature(event_id="usgs-1", *, mag=5.2, title="M 5.2 - Example region", updated=1780000000000,
                 coordinates=(-120.5, 35.2, 8.0)):
    return {"type": "Feature", "id": event_id,
            "properties": {"mag": mag, "place": "Example region", "title": title,
                           "time": 1779990000000, "updated": updated,
                           "url": f"https://earthquake.usgs.gov/earthquakes/eventpage/{event_id}",
                           "felt": 12, "alert": "green", "status": "reviewed",
                           "tsunami": 0, "sig": 410, "type": "earthquake"},
            "geometry": {"type": "Point", "coordinates": list(coordinates)}}


def usgs_payload(*features):
    return {"type": "FeatureCollection", "metadata": {"count": len(features)}, "features": list(features)}


def test_usgs_valid_feature_preserves_official_facts_and_provenance():
    raw = usgs_feature_to_event(usgs_feature(), NOW)
    event = normalize_event(raw, ingested_at=NOW)
    assert event["source"] == "usgs" and event["source_event_id"] == "usgs-1"
    assert event["title"] == "M 5.2 - Example region"
    assert event["metadata"]["magnitude"] == 5.2
    assert event["latitude"] == 35.2 and event["longitude"] == -120.5
    assert event["metadata"]["depth_km"] == 8
    assert event["event_timestamp"] == "2026-05-28T17:40:00Z"
    assert event["published_at"] is None
    assert event["metadata"]["alert"] == "green" and event["metadata"]["felt_reports"] == 12
    assert event["url"].endswith("usgs-1")
    assert event["provenance"]["retrieved_at"] == NOW
    assert event["provenance"]["adapter"] == "USGSGeoJSONSource"
    json.dumps(event, allow_nan=False)


def test_usgs_fetches_multiple_events_and_reports_counts(monkeypatch):
    monkeypatch.setattr("monitoring.real_sources._http_get",
                        lambda *args, **kwargs: fake_http(json.dumps(usgs_payload(usgs_feature(), usgs_feature("u2")))))
    source = USGSEarthquakeSource()
    events, report = source.fetch_with_report()
    assert [event["source_event_id"] for event in events] == ["usgs-1", "u2"]
    assert report["status"] == "success" and report["records_received"] == 2
    assert report["valid_records"] == 2 and report["invalid_records"] == 0


def test_usgs_invalid_feature_is_counted_without_losing_other_records(monkeypatch):
    payload = usgs_payload(usgs_feature(), {"type": "Feature", "properties": {}, "geometry": {}})
    monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, **k: fake_http(json.dumps(payload)))
    events, report = USGSEarthquakeSource().fetch_with_report()
    assert len(events) == 1 and report["records_received"] == 2
    assert report["valid_records"] == 1 and report["invalid_records"] == 1


def test_usgs_missing_optional_fields_and_bad_coordinates_are_not_invented():
    event = usgs_feature_to_event({"id": "u3", "properties": {"place": "Only place"},
                                   "geometry": {"type": "Point", "coordinates": [500, 95]}})
    assert event["text"] == "Only place"
    assert event["latitude"] is None and event["longitude"] is None
    assert event["metadata"]["magnitude"] is None and event["published_at"] is None


@pytest.mark.parametrize("feature", [None, {}, {"id": "x", "properties": {}}])
def test_usgs_malformed_or_unusable_features_rejected(feature):
    with pytest.raises(ValueError):
        usgs_feature_to_event(feature)


def test_usgs_malformed_geojson_response_is_reported(monkeypatch):
    monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, **k: fake_http("{bad"))
    events, report = USGSEarthquakeSource().fetch_with_report()
    assert events == [] and report["status"] == "error"


@pytest.mark.parametrize("error", [TimeoutError("timed out"), SourceFetchError("HTTP status 503")])
def test_usgs_timeout_and_http_errors_are_reported(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr("monitoring.real_sources._http_get", fail)
    events, report = USGSEarthquakeSource().fetch_with_report()
    assert events == [] and report["status"] == "error" and report["error"]


def test_usgs_source_id_duplicate_and_material_update_states(monkeypatch):
    payload = {"value": usgs_payload(usgs_feature())}
    monkeypatch.setattr("monitoring.real_sources._http_get",
                        lambda *a, **k: fake_http(json.dumps(payload["value"])))
    source = USGSEarthquakeSource()
    event1 = source.fetch()[0]
    event2 = source.fetch()[0]
    runner = MonitoringRunner(pipeline=_StubPipeline())
    first, duplicate = runner.process_event(event1), runner.process_event(event2)
    assert first["event_state"] == "NEW" and duplicate["event_state"] == "DUPLICATE"
    payload["value"] = usgs_payload(usgs_feature(mag=5.4, title="M 5.4 - Example region", updated=1780000100000))
    updated = runner.process_event(source.fetch()[0])
    assert updated["event_id"] == first["event_id"] and updated["event_state"] == "UPDATED"


def test_source_fact_metadata_change_is_an_update_even_when_text_is_unchanged():
    runner = MonitoringRunner(pipeline=_StubPipeline())
    base = {"source": "usgs", "source_event_id": "stable", "text": "same title",
            "metadata": {"magnitude": 4.1}}
    changed = {**base, "metadata": {"magnitude": 4.2}}
    first, update = runner.process_event(base), runner.process_event(changed)
    assert first["event_state"] == "NEW" and update["event_state"] == "UPDATED"
    assert first["event_id"] == update["event_id"]


def gdacs_feature(event_id="123", *, alert="Orange", description="Flood impact update", coordinates=(29.0, 12.0)):
    return {"type": "Feature", "id": f"FL-{event_id}",
            "properties": {"eventtype": "FL", "eventid": event_id, "episodeid": "2",
                           "name": "Flood in Exampleland", "description": description,
                           "alertlevel": alert, "country": "Exampleland", "fromdate": "2026-09-24T10:00:00Z",
                           "todate": "2026-09-25T10:00:00Z", "url": f"https://www.gdacs.org/report.aspx?eventid={event_id}"},
            "geometry": {"type": "Point", "coordinates": list(coordinates)}}


def test_gdacs_valid_records_preserve_ids_dates_location_alert_and_point():
    event = gdacs_item_to_event(gdacs_feature(), NOW)
    assert event["source"] == "gdacs" and event["source_event_id"] == "FL:123"
    assert event["event_timestamp"] == "2026-09-24T10:00:00Z"
    assert event["published_at"] is None and event["updated_at"] is None
    assert event["location_text"] == "Exampleland"
    assert (event["latitude"], event["longitude"]) == (12.0, 29.0)
    assert event["metadata"]["alert_level"] == "Orange"
    assert event["metadata"]["event_end_at"] == "2026-09-25T10:00:00Z"
    assert event["metadata"]["source_geometry"]["coordinates"] == [29.0, 12.0]
    assert "Flood impact update" in event["text"]


def test_gdacs_fetches_multiple_api_features(monkeypatch):
    payload = {"type": "FeatureCollection", "features": [gdacs_feature("1"), gdacs_feature("2")]}
    monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, **k: fake_http(json.dumps(payload)))
    source = GDACSEventSource()
    events, report = source.fetch_with_report()
    assert len(events) == 2 and report["valid_records"] == 2
    assert "eventlist=EQ%3BTC" in source.url


def test_gdacs_invalid_feature_is_counted_without_losing_other_records(monkeypatch):
    payload = {"features": [gdacs_feature(), None]}
    monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, **k: fake_http(json.dumps(payload)))
    events, report = GDACSEventSource().fetch_with_report()
    assert len(events) == 1 and report["invalid_records"] == 1


def test_gdacs_missing_fields_and_invalid_geometry_remain_null():
    event = gdacs_item_to_event({"eventtype": "EQ", "eventid": "7", "name": "Earthquake",
                                 "country": "Test", "alertlevel": "Green",
                                 "geometry": {"type": "Point", "coordinates": [181, 0]}})
    assert event["latitude"] is None and event["longitude"] is None
    assert event["source_url"] is None and event["metadata"]["severity"] is None


@pytest.mark.parametrize("payload", [b"{bad", b"{}"])
def test_gdacs_malformed_response_reported(monkeypatch, payload):
    monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, **k: fake_http(payload))
    events, report = GDACSEventSource().fetch_with_report()
    assert events == [] and report["status"] == "error"


def test_gdacs_empty_collection_is_valid_empty_response(monkeypatch):
    monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, **k: fake_http(b"[]"))
    events, report = GDACSEventSource().fetch_with_report()
    assert events == [] and report["status"] == "empty"


def test_gdacs_source_id_duplicate_and_material_update(monkeypatch):
    payload = {"value": {"features": [gdacs_feature()]}}
    monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, **k: fake_http(json.dumps(payload["value"])))
    source, runner = GDACSEventSource(), MonitoringRunner(pipeline=_StubPipeline())
    first = runner.process_event(source.fetch()[0])
    duplicate = runner.process_event(source.fetch()[0])
    assert first["event_state"] == "NEW" and duplicate["event_state"] == "DUPLICATE"
    payload["value"] = {"features": [gdacs_feature(alert="Red")]}
    updated = runner.process_event(source.fetch()[0])
    assert updated["event_id"] == first["event_id"] and updated["event_state"] == "UPDATED"


RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Disaster updates</title>
<item><guid>item-1</guid><title>Flood emergency declared</title><description>Residents are evacuated after severe river flooding.</description><pubDate>Fri, 25 Sep 2026 10:00:00 GMT</pubDate><link>https://news.example/a</link><category>Flood</category></item>
<item><title>Storm rescue operation</title><description>Teams continue rescue operations after storm damage.</description><pubDate>Fri, 25 Sep 2026 11:00:00 GMT</pubDate><link>https://news.example/b</link></item>
</channel></rss>'''
ATOM = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Field report</title>
<entry><id>tag:example,2026:1</id><title>Earthquake response</title><summary>Relief response after earthquake in the region.</summary><updated>2026-09-25T10:00:00Z</updated><link href="https://example.test/e1"/><category term="earthquake"/></entry></feed>'''


def test_rss_and_atom_parse_with_multiple_article_fields():
    publisher, items = parse_feed(RSS)
    assert publisher == "Disaster updates" and len(items) == 2
    event, include = rss_entry_to_event(items[0], feed_url="https://news.example/feed", feed_title=publisher,
                                        retrieved_at=NOW)
    assert include and event["source"] == "news_rss"
    assert event["source_event_id"].endswith(":item-1")
    assert event["published_at"] == "2026-09-25T10:00:00Z"
    assert event["metadata"]["categories"] == ["Flood"]
    atom_title, atom_items = parse_feed(ATOM)
    atom, include = rss_entry_to_event(atom_items[0], feed_url="https://example.test/feed", feed_title=atom_title)
    assert include and atom["source_event_id"].endswith(":tag:example,2026:1")
    assert atom["updated_at"] == "2026-09-25T10:00:00Z"


def test_rss_missing_guid_has_stable_url_title_timestamp_identity():
    _, items = parse_feed(RSS)
    a, _ = rss_entry_to_event(items[1], feed_url="https://news.example/feed")
    b, _ = rss_entry_to_event(items[1], feed_url="https://news.example/feed")
    c, _ = rss_entry_to_event(items[1], feed_url="https://other.example/feed")
    assert a["source_event_id"] == b["source_event_id"]
    assert a["source_event_id"] != c["source_event_id"]


def test_rss_relevance_filter_drops_clear_nonmatch_but_keeps_uncertain():
    unrelated, keep_unrelated = rss_entry_to_event(
        {"id": "x", "title": "A long sports result", "summary": "A lengthy unrelated sports article with enough text to classify."},
        feed_url="https://example.test/feed", relevance_filter_enabled=True)
    uncertain, keep_uncertain = rss_entry_to_event(
        {"title": "Update"}, feed_url="https://example.test/feed", relevance_filter_enabled=True)
    unfiltered, keep_when_disabled = rss_entry_to_event(
        {"id": "z", "title": "A long sports result", "summary": "A lengthy unrelated sports article with enough text to classify."},
        feed_url="https://example.test/feed", relevance_filter_enabled=False)
    assert not keep_unrelated and unrelated["metadata"]["relevance_status"] == "no_keyword_match"
    assert keep_uncertain and uncertain["metadata"]["relevance_status"] == "uncertain"
    assert keep_when_disabled and unfiltered["metadata"]["relevance_status"] == "no_keyword_match"


def test_rss_source_fetch_report_and_empty_feed(monkeypatch):
    monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, **k: fake_http(RSS, "application/rss+xml"))
    source = NewsRSSSource(["https://news.example/feed"])
    events, report = source.fetch_with_report()
    assert len(events) == 2 and report["valid_records"] == 2
    monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, **k: fake_http(b"<rss><channel><title>Empty</title></channel></rss>"))
    empty, empty_report = source.fetch_with_report()
    assert empty == [] and empty_report["status"] == "empty"


def test_live_config_defaults_to_public_gdacs_rss_but_allows_explicit_disable(monkeypatch):
    monkeypatch.delenv("DISASTER_RSS_FEEDS", raising=False)
    monkeypatch.setenv("NEWS_ENABLED", "true")
    config = RealSourceConfig.from_env()
    assert config.news_enabled and config.rss_feeds == ("https://www.gdacs.org/xml/rss.xml",)
    monkeypatch.setenv("DISASTER_RSS_FEEDS", "")
    assert RealSourceConfig.from_env().rss_feeds == ()


def test_rss_malformed_timeout_and_http_errors(monkeypatch):
    source = NewsRSSSource(["https://news.example/feed"])
    monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, **k: fake_http(b"<broken"))
    events, report = source.fetch_with_report()
    assert events == [] and report["feeds"][0]["status"] == "error"
    for error in (TimeoutError("timed out"), SourceFetchError("HTTP status 502")):
        monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, _error=error, **k: (_ for _ in ()).throw(_error))
        events, report = source.fetch_with_report()
        assert events == [] and report["status"] == "error"


def test_rss_feed_failures_are_isolated_from_other_configured_feeds(monkeypatch):
    def fetch(url, **kwargs):
        if "bad" in url:
            raise SourceFetchError("HTTP status 503")
        return fake_http(RSS, "application/rss+xml")
    monkeypatch.setattr("monitoring.real_sources._http_get", fetch)
    events, report = NewsRSSSource(["https://bad.example/feed", "https://good.example/feed"]).fetch_with_report()
    assert len(events) == 2 and report["status"] == "partial"
    assert report["feeds"][0]["status"] == "error" and report["feeds"][1]["status"] == "success"
    assert report["errors"]


def test_rss_guid_duplicate_and_article_update_states(monkeypatch):
    rss = {"body": RSS}
    monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, **k: fake_http(rss["body"], "application/rss+xml"))
    source = NewsRSSSource(["https://news.example/feed"])
    runner = MonitoringRunner(pipeline=_StubPipeline())
    first = runner.process_event(source.fetch()[0])
    duplicate = runner.process_event(source.fetch()[0])
    assert first["event_state"] == "NEW" and duplicate["event_state"] == "DUPLICATE"
    rss["body"] = RSS.replace(b"Residents are evacuated", b"Residents are rescued and evacuated")
    updated = runner.process_event(source.fetch()[0])
    assert updated["event_id"] == first["event_id"] and updated["event_state"] == "UPDATED"


def test_multisource_failure_isolation_and_operational_reports(monkeypatch):
    monkeypatch.setattr("monitoring.real_sources._http_get", lambda *a, **k: fake_http(json.dumps(usgs_payload(usgs_feature()))))
    class FailingSource:
        name = "fails"
        def fetch(self):
            raise TimeoutError("source unavailable")
    group = MultiSource([USGSEarthquakeSource(), FailingSource(), NewsRSSSource([])])
    records = group.fetch()
    assert len(records) == 1
    assert group.source_reports["usgs"]["status"] == "success"
    assert group.source_reports["fails"]["status"] == "error"
    assert group.source_reports["news_rss"]["status"] == "not_configured"


def test_live_session_routes_to_phase9_and_keeps_source_provenance():
    source_config = RealSourceConfig(usgs_enabled=False, gdacs_enabled=False, news_enabled=False,
                                     max_events_per_source=2)
    event = usgs_feature_to_event(usgs_feature())
    event["source"] = "future_public_api"
    group = MultiSource([_StaticSource([event])])
    session = LiveMonitoringSession(source_config, event_source=group, runner=MonitoringRunner(pipeline=_StubPipeline()))
    result = session.run_cycle()
    assert result["events"][0]["event_state"] == "NEW"
    assert result["events"][0]["provenance"]["source"] == "future_public_api"
    assert "alert_decision" in result["events"][0]


def test_feed_url_validation_and_minimum_poll_rate():
    _, report = NewsRSSSource(["file:///tmp/feed.xml"]).fetch_with_report()
    assert report["status"] == "error"
    with pytest.raises(ValueError):
        RealSourceConfig(polling_interval_seconds=5)


class _StubPipeline:
    def process(self, event):
        return {"event_id": event_id_for(event), "source": event["source"],
                "source_event_id": event.get("source_event_id"), "provenance": event["provenance"],
                "processing_status": "PROCESSED", "processing_errors": [], "intelligence": {},
                "phase_status": {}, "phases_completed": [], "phases_failed": []}


class _StaticSource:
    name = "future_public_api"
    def __init__(self, events):
        self.events = events
        self.source_reports = {self.name: {"source": self.name, "status": "success", "records_received": len(events),
                                           "valid_records": len(events), "retrieval_timestamp": NOW}}
    def fetch(self):
        return list(self.events)
