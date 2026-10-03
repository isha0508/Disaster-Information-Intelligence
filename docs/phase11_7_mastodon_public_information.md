# Phase 11.7 — Mastodon Public Information Source

The monitoring service can read public Mastodon hashtag timelines without authentication. The default instance is `https://mastodon.social`; the default hashtags are `earthquake`, `flood`, `wildfire`, and `hurricane`. Each hashtag uses `GET /api/v1/timelines/tag/{hashtag}?limit=N`, with a bounded limit and the shared timeout and retry policy.

The adapter implements the existing `EventSource` contract and sends normalized source events through the same event identity/state store, Phase 3–9 pipeline, SQLite repositories, REST API, and dashboard. Statuses from multiple timelines are deduplicated by instance and Mastodon status ID before processing. HTTP, network, timeout, and malformed-response failures are reported per hashtag; successful hashtags remain available when another hashtag fails.

Mastodon returns status content as HTML. The adapter converts it to plain text for NLP while retaining original HTML in source metadata. Events preserve the Mastodon ID, URI and URL, created time, author identifiers, language, visibility, hashtag names, source instance, retrieval time, and an external card URL when present. `source=mastodon` and `source_type=public_social` keep the origin explicit. No account credentials are required or used.

Configure the source through process environment variables; `.env` is not loaded automatically:

```text
DISASTER_MASTODON_ENABLED=true
DISASTER_MASTODON_BASE_URL=https://mastodon.social
DISASTER_MASTODON_HASHTAGS=earthquake,flood,wildfire,hurricane
DISASTER_MASTODON_LIMIT=10
DISASTER_SOURCE_TIMEOUT=20
DISASTER_SOURCE_RETRY_COUNT=2
DISASTER_SOURCE_RETRY_BACKOFF=1
```

Add hashtags as comma-separated names, without needing `#`. The per-tag limit is 1–40. The source does not apply a topicality filter: hashtag timelines can contain incident reports, historical news, commentary, or unrelated posts. Existing Phase 3 classification, Phase 4 extraction, and Phase 5 priority logic remain responsible for downstream interpretation. Coordinates are not inferred from author profiles or hashtag terms; existing Phase 6 behavior is preserved.

Run a one-cycle live monitor with `python -m monitoring.live --once`. The events are available from the existing API at `/api/events?source=mastodon`. Focused mocked tests are `pytest tests/test_phase11_7_mastodon.py -v`; they do not require network access. Live verification must use the configured public instance and report source failures as observed. Mastodon hashtag timelines are instance-scoped and do not provide complete social-media coverage.
