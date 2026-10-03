# Phase 8 — Real Disaster Data Sources

## Architecture

The existing Phase 8 contracts remain in use: provider adapters implement `EventSource.fetch()` and return source-shaped mappings; `normalize_event` sanitizes and normalizes records; `event_id_for` creates stable source identities; `EventStateStore` tracks versions; and `MonitoringRunner` routes new and updated events through the existing Phase 3–7 orchestration. `MultiSource` runs each adapter independently and retains a report per provider, so one feed error does not stop other sources. `LiveMonitoringSession` connects Phase 8 results to the existing Phase 9 `AlertEvaluator`. Phase 10 can consume the resulting `events`/`alerts` JSON directly and labels a live artifact separately from its synthetic demo.

Each adapter uses the shared raw event contract (`source`, `source_event_id`, `title`, `text`, timestamps, `source_url`, optional location/coordinates, `metadata`, and adapter details). The normalized record retains `source`, source ID, title, text, URL, publication/update/retrieval times, metadata, original sanitized raw event, and provenance. Source-specific facts are stored as metadata and remain distinct from Phase 3–9 inferences. USGS magnitude and GDACS alert level never set Phase 5 severity or priority.

### Source-aware type and location evidence

The Phase 8 orchestration layer resolves disaster type without discarding evidence from the source, Phase 4, or Phase 3. Live structured sources can expose a high-confidence Phase 3 prediction that conflicts with their event classification; confidence alone does not establish correctness, and this pipeline does not claim to improve or validate model accuracy. The Phase 3 model is retained as a secondary prediction signal. It is not treated as authoritative when trusted structured source metadata is available.

For recognized structured feeds, USGS `properties.type` and GDACS `eventtype` are normalized with a small explicit alias map. A mapped structured source type is used as Phase 5's operational `disaster_type`. Phase 4's extracted type remains under `intelligence.phase4.disaster_type` and is recorded in `type_resolution.phase4_disaster_type`; it is used only as a fallback when no mappable authoritative type is available. A normalized Phase 3 prediction is the next fallback. An unknown authoritative value is preserved, produces a resolution warning, and is never mapped based on the ML prediction. If a separately attributed Phase 4 or Phase 3 fallback exists, it may be selected operationally while the source value remains unresolved.

`type_resolution` preserves source, Phase 4, and Phase 3 evidence, canonical operational type, resolution method/rationale, and a deterministic comparison status: `AGREEMENT`, `DISAGREEMENT`, `SOURCE_ONLY`, `ML_ONLY`, or `UNRESOLVED`. A disagreement includes `model_disagreement: true`, both normalized values, and the original ML confidence; the authoritative source continues to drive Phase 5. Confidence is retained as model output and is not interpreted as calibrated probability or proof of correctness. Malformed/unmappable source types are represented by `warning` without stopping downstream processing.

USGS/GDACS structured location and coordinates are kept in `source_location` with `authoritative_structured_metadata` provenance. Phase 4's extracted entities remain separately available in `nlp_location_entities`; source location is supplied to Phase 5 as an operational location without relabeling it as NER output. Phase 6 validates source coordinates and uses them directly with `coordinate_source: source_metadata`; if they are absent or invalid, its existing text geocoder path is used. Phase 10 displays operational type, source type, Phase 4 type, ML prediction/confidence, agreement status, resolution method/rationale, and coordinate provenance.

## Currently implemented sources

### USGS earthquake feed

`USGSEarthquakeSource` reads the official USGS past-day all-earthquakes GeoJSON summary feed by default. It expects a GeoJSON `FeatureCollection` and maps `Feature.id` (or official property code), title, place, magnitude, time, updated time, URL, felt count, alert/status, tsunami flag, significance, and point longitude/latitude/depth where valid. Invalid or missing coordinates remain null. Missing fields remain null. Raw properties are retained as sanitized metadata. The official USGS GeoJSON specification describes this FeatureCollection and its earthquake property/geometry fields: [USGS GeoJSON Summary Format](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php). Feeds follow the [USGS Earthquakes Feed Lifecycle Policy](https://earthquake.usgs.gov/earthquakes/feed/policy.php).

Default endpoint: `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson`. The feed's `time` is retained as `event_timestamp`, not mislabeled as article publication time; `updated` is retained as the source update time. It is a public feed and does not require an API key. Other official summary feed URLs may be configured using `USGS_FEED_URL`.

### GDACS event API

`GDACSEventSource` uses the official GDACS Events search API with an event-type and date-window query. It accepts GeoJSON FeatureCollections and supported JSON event-list wrappers, retaining the source event type/ID, episode ID, name, description, alert level, dates, country/region, source URL, point coordinates if supplied, and raw properties. Identity combines event type and event ID, so new episodes update the same disaster identity when the source reports them that way.

Default endpoint: `https://www.gdacs.org/gdacsapi/api/Events/geteventlist/SEARCH`. The event types and lookback window are configurable. GDACS `fromdate` is retained as the event timestamp and `todate` as the event-window end; neither is falsely labeled as publication/update time. A source `updated` field is mapped to `updated_at` only when present. GDACS documents its API as free public data, its GeoJSON search endpoint and event query parameters in the [GDACS API Quick Start](https://www.gdacs.org/Documents/2025/GDACS_API_quickstart_v2.pdf) and the [GDACS Swagger API](https://www.gdacs.org/gdacsapi/swagger/index.html). No API key is required for this public endpoint. The service currently limits collection responses and recommends selective, paged queries; this adapter intentionally makes one bounded query per cycle.

### News/RSS and Atom

`NewsRSSSource` accepts one or more operator-configured HTTP(S) RSS 2.0 or Atom feeds. It extracts publisher/feed title, article title, description/summary/content, publication/update time, URL, GUID/Atom ID, categories and sanitized raw item metadata. `source_event_id` uses a feed-scoped stable ID when a GUID exists; otherwise it hashes feed URL, article URL, normalized title and publication timestamp. It does not infer disaster type, casualty counts, requests or damage from text.

`DISASTER_RSS_FEEDS` defaults to the official GDACS RSS alert feed at `https://www.gdacs.org/xml/rss.xml`; set it to comma- or newline-separated feed URLs to add/replace feeds, or explicitly set it empty to disable RSS. The [GDACS Feed Reference](https://www.gdacs.org/feed_reference.aspx) describes its public machine-readable feeds and update cadence. This feed is an official disaster-alert stream rather than a general newsroom feed. Add only feeds whose publishers permit automated feed use. The source label remains `news_rss`; publisher and feed URL are preserved in metadata.

## Relevance filtering

Filtering is enabled by default and configurable with `DISASTER_RSS_FILTER_ENABLED`, `DISASTER_RSS_KEYWORDS`, and `DISASTER_RSS_KEEP_UNCERTAIN`. Matching is a case-insensitive substring search over title plus description/summary/content. A matching item is retained and tagged `relevant`. A sufficiently long item with no keyword match is filtered, but its count is included in the per-feed report. Very short items or an empty keyword list are marked `uncertain` and retained when `DISASTER_RSS_KEEP_UNCERTAIN=true`. Disable filtering to retain all valid articles. Filtering does not assign a disaster category.

## Configuration and environment variables

Copy `.env.example` as a local reference and export the desired values in the shell or process environment; the program does not automatically load `.env` files. There are no USGS/GDACS credentials. RSS URLs can be configured directly. Timeout, user-agent, source enables, event list, lookback, RSS terms, per-source processing cap, and polling interval are explicit settings in `RealSourceConfig`.

RSS uses a 20-second default HTTP timeout, an 8 MB response limit, and at most `DISASTER_MAX_EVENTS_PER_SOURCE` (10 by default) are selected for downstream processing per cycle. RSS is enabled by default when its feed list is non-empty. Mastodon is the public social source; `RealSourceConfig.from_env()` enables it by default, using the public hashtag timelines at `https://mastodon.social` without authentication. Configure it with `DISASTER_MASTODON_ENABLED`, `DISASTER_MASTODON_BASE_URL`, `DISASTER_MASTODON_HASHTAGS`, and `DISASTER_MASTODON_LIMIT`. Requests share the source timeout, response cap, and polling cadence. Do not put credentials in source files, feed URLs, logs, or committed configuration. Existing URL/key sanitization remains applied to output metadata.

## One-shot ingestion and polling

From the project root, one-shot live ingestion and a JSON result artifact:

```powershell
$env:DISASTER_RSS_FEEDS = "https://www.gdacs.org/xml/rss.xml"
python evaluation/run_phase8_real_sources_demonstration.py
```

The script makes actual HTTP requests. It writes `evaluation/phase8_real_sources_results.json`, labels the run `live`, reports each source/feed status, and does not substitute fixtures if requests fail. If no RSS URLs are configured, the RSS adapter reports `not_configured` and no RSS network request is made.

One cycle without writing the demonstration artifact:

```powershell
python -m monitoring.real_runner --once
```

Paced polling (default interval is 360 seconds; minimum accepted interval is 60 seconds):

```powershell
python -m monitoring.real_runner --poll
python -m monitoring.real_runner --poll --cycles 2
```

Stop continuous polling with Ctrl+C. The default 360-second cadence is conservative for public feeds and matches the GDACS feed reference cadence. Respect the current publisher's API/feed terms and any stricter rate limits. These commands use the existing in-memory state store: duplicate/update state does not persist between process restarts until Phase 11 storage exists.

## Provenance, identity, updates, and failures

Every normalized event retains source, stable source-event ID, source URL, publication/update/retrieval/ingestion timestamps, adapter name/version and sanitized raw source fields. USGS identity uses its feature ID; GDACS uses event type plus event ID; RSS uses the feed-scoped GUID or deterministic fallback. State comparison includes normalized text plus stable source metadata (not retrieval time), so source-fact-only revisions can be classified as `UPDATED` without changing the event identity. Identical revisions are `DUPLICATE`; normalization failures are `INVALID`.

Identity is source-scoped. The same disaster reported by USGS, GDACS, and an RSS publisher remains three attributable source events at this layer; cross-provider entity resolution is a separate future problem and is not guessed here.

The source report includes status, records received/valid/invalid/filtered, retrieval timestamp, HTTP status/content type, latency, and downstream NEW/DUPLICATE/UPDATED/INVALID counts. Errors are isolated per provider/feed and sanitized. Timeouts, HTTP errors, invalid URLs, oversized bodies, malformed JSON/XML and unsupported feed formats are reported without stopping other configured sources.

## Offline tests and live demonstration

All automated source tests mock HTTP responses and require no internet:

```powershell
pytest tests/test_phase8_real_sources.py -v
pytest tests/test_phase8_monitoring.py -v
```

The real-source demonstration is intentionally different: it makes network requests. A successful HTTP fetch means a feed was reachable and parseable, not that its contents are complete, accurate, or fit for emergency decisions. It records Phase 3–7 failures on each event; no missing downstream result is invented. Phase 9 uses its existing deterministic alert evaluator and mock notification behavior. Public-feed ingestion is not yet a production monitor or validated end-to-end operational service.

## Phase 9/10 integration

Each processed event passes through the current `IntelligencePipeline` for Phases 3–7. `LiveMonitoringSession` passes those Phase 8 records to the existing Phase 9 evaluator and retains the Phase 9 decision, alert record/status and provenance with the event. No alternate scoring, spatial or alert engine is introduced. Phase 10's data adapter accepts the live `events` and `alerts` artifact format; raw source coordinates are exposed as source facts and are not treated as Phase 6 geocoded/verified coordinates.

## Future extension: social/public information

Social/public information is not implemented in this phase. An authorized provider can be added as an `EventSource` adapter that fetches only through its legitimate public/authorized API, obtains any key from environment variables, preserves provider IDs/timestamps/URLs/provenance, and returns the same raw event contract. Add its constructor to `configured_sources`; `MultiSource`, normalization, deterministic identity, lifecycle state, Phase 3–9, and Phase 10 remain provider-agnostic. Do not scrape private content, bypass authentication/rate limits/robots policies, or create fake accounts/posts.

## Security and limitations

The HTTP client uses a configurable timeout, identifying user-agent, response-size cap and HTTP(S)-only feed URLs; URLs with embedded credentials are rejected. Feed-specific terms and copyright still apply. RSS keyword filtering can miss relevant wording; set appropriate terms or turn filtering off. Coordinates from USGS/GDACS are source facts; Phase 6's own geocoding/validation semantics remain authoritative for GIS. In-memory event and alert state is lost at exit. Public records can be revised, delayed, duplicated across providers, or lack fields. This work does not supply authenticated source access, durable storage, an SLA, real notification delivery, or validated disaster prediction.
