"""Mocked tests for public Mastodon hashtag ingestion."""

import json

import pytest

import monitoring.mastodon as mastodon
from monitoring.real_sources import RealSourceConfig, configured_sources, SourceFetchError
from dashboard.live_data import KNOWN_SOURCES


def status(id="123", content="<p>Update: <a href='https://example.org'>M1.4 #earthquake</a><br>near Malatya</p>"):
    return {"id": id, "uri": f"https://mastodon.social/@reporter/{id}",
        "url": f"https://mastodon.social/@reporter/{id}", "created_at": "2026-09-26T10:00:00Z",
        "content": content, "account": {"id": "acct-1", "username": "reporter", "acct": "reporter@example.net",
            "display_name": "Field Reporter", "url": "https://mastodon.social/@reporter"},
        "language": "en", "visibility": "public", "tags": [{"name": "earthquake"}, {"name": "malatya"}],
        "card": {"url": "https://example.org/report"}}


def fake_responses(monkeypatch, responses):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        return body, "application/json", "2026-09-26T10:00:00Z"
    monkeypatch.setattr(mastodon, "_http_get", get)
    return calls


def test_configuration_and_registry(monkeypatch):
    monkeypatch.setenv("DISASTER_MASTODON_ENABLED", "true")
    monkeypatch.setenv("DISASTER_MASTODON_BASE_URL", "https://mastodon.social")
    monkeypatch.setenv("DISASTER_MASTODON_HASHTAGS", "#earthquake, flood, wildfire,hurricane, #extra")
    config = RealSourceConfig.from_env()
    assert config.mastodon_hashtags == ("earthquake", "flood", "wildfire", "hurricane", "extra")
    assert [source.name for source in configured_sources(RealSourceConfig(
        usgs_enabled=False, gdacs_enabled=False, news_enabled=False, mastodon_enabled=True))] == ["mastodon"]
    assert not hasattr(config, "bluesky_enabled")
    assert KNOWN_SOURCES["mastodon"] == "Mastodon" and "bluesky" not in KNOWN_SOURCES


def test_normalization_html_metadata_and_identity():
    event = mastodon.mastodon_post_to_event(status())
    assert event["source"] == "mastodon" and event["source_type"] == "public_social"
    assert event["source_event_id"] == "mastodon.social:123"
    assert event["text"] == "Update: M1.4 #earthquake near Malatya"
    assert event["metadata"]["author"]["acct"] == "reporter@example.net"
    assert event["metadata"]["hashtags"] == ["earthquake", "malatya"]
    assert event["metadata"]["external_link"] == "https://example.org/report"
    assert "<a" in event["metadata"]["raw_content_html"]


def test_only_explicit_valid_gps_text_becomes_source_coordinate_evidence():
    event = mastodon.mastodon_post_to_event(status(content=
        "New earthquake 20 km NW of Kozan (Türkiye) GPS: 37.1200, 35.8000"))
    assert (event["latitude"], event["longitude"]) == (37.12, 35.8)
    assert event["coordinate_provenance"] == "explicit_source_text_gps"
    assert mastodon.mastodon_post_to_event(status(content=
        "Earthquake 109 km NE of Cruz Bay, U.S. Virgin Islands"))["latitude"] is None
    assert mastodon.mastodon_post_to_event(status(content=
        "Earthquake GPS: 91, 181"))["longitude"] is None


def test_multiple_hashtags_deduplicate_and_report(monkeypatch):
    calls = fake_responses(monkeypatch, [[status(), status("124")], [status(), status("125")], [], [status("126")]])
    source = mastodon.MastodonPublicSource(hashtags=("earthquake", "flood", "wildfire", "hurricane"))
    events, report = source.fetch_with_report()
    assert len(calls) == 4 and all("limit=10" in url for url in calls)
    assert len(events) == 4 and report["records_received"] == 5
    assert report["duplicates_removed"] == 1 and report["status"] == "success"


@pytest.mark.parametrize("error", [SourceFetchError("HTTP status 429"), SourceFetchError("HTTP status 403"),
    SourceFetchError("HTTP status 503"), SourceFetchError("request timed out"), OSError("connection failed")])
def test_partial_failures_are_per_hashtag(monkeypatch, error):
    fake_responses(monkeypatch, [[status()], error, []])
    events, report = mastodon.MastodonPublicSource(hashtags=("earthquake", "flood", "wildfire")).fetch_with_report()
    assert len(events) == 1 and report["status"] == "partial"
    assert "#flood" in report["errors"][0]


def test_all_failures_and_malformed_payload(monkeypatch):
    fake_responses(monkeypatch, [b"not json"])
    events, report = mastodon.MastodonPublicSource(hashtags=("earthquake",)).fetch_with_report()
    assert events == [] and report["status"] == "error"
    fake_responses(monkeypatch, [{"unexpected": []}])
    events, report = mastodon.MastodonPublicSource(hashtags=("earthquake",)).fetch_with_report()
    assert events == [] and report["status"] == "error"


def test_empty_timeline_and_hashtag_validation(monkeypatch):
    fake_responses(monkeypatch, [[]])
    assert mastodon.MastodonPublicSource(hashtags=("earthquake",)).fetch_with_report()[1]["status"] == "empty"
    with pytest.raises(ValueError):
        mastodon.MastodonPublicSource(hashtags=("bad/tag",))._validate()
