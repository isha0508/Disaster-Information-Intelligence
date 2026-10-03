"""API-backed dashboard view data; no database or demonstration-artifact access."""

from datetime import datetime, timedelta, timezone

from dashboard.adapters import adapt_incident
from dashboard.api_client import DashboardAPIError


PAGE_SIZE = 50
MAX_ALERTS = 100
MAX_RUNS = 20
KNOWN_SOURCES = {"usgs": "USGS", "gdacs": "GDACS", "rss": "NEWS / RSS",
                 "news": "NEWS / RSS", "news_rss": "NEWS / RSS",
                 "mastodon": "Mastodon"}


def load_live_snapshot(client, query=None, *, now=None):
    """Fetch bounded API resources and assemble operational display data."""
    query = query or {}
    components, errors = {}, {}
    try:
        health = client.get_json("/health")
        if health.get("status") != "ok" or health.get("database") != "ok":
            raise DashboardAPIError("API health check did not confirm database connectivity")
        components["health"] = health
    except Exception as exc:
        return {"connection_status": "offline", "data_status": "API OFFLINE",
                "error": _safe_error(exc), "last_updated": None,
                "components": {"health": "offline"}, "events": [], "alerts": [],
                "monitoring_runs": [], "source_status": [], "summary": {},
                "event_total": None, "event_page": 0, "page_size": PAGE_SIZE,
                "map_points": [], "unresolved_locations": [], "last_24h_events": None}

    # Event list rows are dashboard summaries; alert/revision history remains
    # available on event detail and the dedicated alert/history endpoints.
    params = {"limit": PAGE_SIZE, "offset": _int(query.get("offset"), 0),
              "include_history": "false"}
    for key in ("source", "disaster_type", "operational_type", "severity", "priority", "location", "since", "until"):
        if query.get(key):
            params[key] = query[key]
    endpoints = {
        "events": ("/api/events", params),
        "alerts": ("/api/alerts", {"active": "true", "limit": MAX_ALERTS, "offset": 0}),
        "critical_alerts": ("/api/alerts", {"active": "true", "alert_level": "CRITICAL", "limit": 1, "offset": 0}),
        "monitoring_runs": ("/api/monitoring/runs", {"limit": MAX_RUNS, "offset": 0}),
        "summary": ("/api/intelligence/summary", None),
    }
    payloads = {}
    for name, (path, arguments) in endpoints.items():
        try:
            payload = client.get_json(path, arguments)
            if name in {"events", "alerts", "monitoring_runs"}:
                if not isinstance(payload.get("items"), list) or not isinstance(payload.get("total"), int):
                    raise DashboardAPIError(f"API {name} response has an invalid collection shape")
            if name == "critical_alerts" and not isinstance(payload.get("total"), int):
                raise DashboardAPIError("API critical alert count has an invalid response shape")
            if name == "summary" and not isinstance(payload.get("total_events"), int):
                raise DashboardAPIError("API summary response has an invalid shape")
            payloads[name] = payload
            components[name] = "online"
        except Exception as exc:
            components[name] = "error"
            errors[name] = _safe_error(exc)

    event_payload = payloads.get("events", {})
    raw_events = event_payload.get("items", [])
    alerts = payloads.get("alerts", {}).get("items", [])
    runs = payloads.get("monitoring_runs", {}).get("items", [])
    summary = payloads.get("summary", {})
    event_by_id = {str(item.get("event_id")): item for item in raw_events if isinstance(item, dict) and item.get("event_id")}
    alert_views = [_adapt_persisted_alert(item, event_by_id.get(str(item.get("event_id"))))
                   for item in alerts if isinstance(item, dict) and item.get("alert_id")]
    alert_views.sort(key=lambda item: (_level_rank(item.get("alert_level")),
        _number(item.get("priority_score")), _timestamp_key(item.get("created_at"))), reverse=True)
    incidents, adapter_errors = [], []
    for record in raw_events:
        try:
            incidents.append(_adapt_persisted_event(record))
        except Exception as exc:
            adapter_errors.append({"event_id": record.get("event_id") if isinstance(record, dict) else None,
                                   "error": _safe_error(exc)})
    active_alerts_by_event = {str(alert.get("event_id")): alert for alert in alert_views
                              if alert.get("active") and alert.get("event_id")}
    for incident in incidents:
        alert = active_alerts_by_event.get(str(incident.get("event_id")))
        if alert:
            incident.update({"alert_id": alert.get("alert_id"),
                "alert_level": alert.get("alert_level"), "alert_status": alert.get("alert_status"),
                "alert_type": alert.get("alert_type"), "alert_reasons": alert.get("explanation"),
                "alert_escalation": alert.get("escalation"),
                "alert_suppression": {"suppressed": alert.get("suppressed"),
                                      "reason": alert.get("suppression_reason")},
                "active_alert": alert})
    # Alert level can only be filtered after joining the API's alert resource;
    # all other supported event filters and pagination are server-side.
    alert_filter = query.get("alert_level")
    if alert_filter:
        incidents = [item for item in incidents if str(item.get("alert_level") or "").upper() == alert_filter.upper()]
    map_points = [item for item in incidents if _valid_success_point(item)]
    unresolved = [item for item in incidents if item.get("location_text") and not _valid_success_point(item)]
    latest_run = runs[0] if runs else None
    source_status = _source_rows(latest_run)
    successful_run = next((run for run in runs if str(run.get("status", "")).casefold() in {"success", "partial_failure"}), None)
    current_time = now or datetime.now(timezone.utc)
    since = (current_time - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    try:
        recent = client.get_json("/api/events", {"since": since, "limit": 1, "offset": 0,
                                                   "include_history": "false"})
        if not isinstance(recent.get("total"), int):
            raise DashboardAPIError("API recent event count has an invalid response shape")
        last_24h = recent["total"]
        components["last_24h"] = "online"
    except Exception as exc:
        last_24h = None
        components["last_24h"] = "error"
        errors["last_24h"] = _safe_error(exc)
    last_updated = (latest_run or {}).get("ended_at") or next(
        (item.get("last_seen") for item in raw_events if item.get("last_seen")), None)
    synthetic_only = bool(raw_events) and all(_is_synthetic(item) for item in raw_events)
    live_current = _is_recent_live_run(latest_run, source_status, current_time) and not synthetic_only
    if summary.get("total_events") == 0:
        data_status = "NO OPERATIONAL DATA AVAILABLE"
    elif synthetic_only:
        data_status = "DEMO DATA"
    elif live_current:
        data_status = "LIVE DATA"
    else:
        data_status = "DATABASE DATA"
    required_error = components.get("events") == "error" or components.get("summary") == "error"
    if required_error:
        data_status = "API DEGRADED"
    return {"connection_status": "degraded" if required_error else "online", "data_status": data_status,
        "api_phase": (components.get("health") or {}).get("phase"),
        "database_status": (components.get("health") or {}).get("database", "unknown"),
        "monitoring_status": (latest_run or {}).get("status", "no_recent_run"),
        "last_updated": last_updated, "last_successful_run": successful_run,
        "last_error": next((error for run in runs for error in (run.get("errors") or [])), None),
        "components": components, "errors": errors,
        "events": incidents, "alerts": alert_views, "monitoring_runs": runs,
        "latest_run": latest_run, "source_status": source_status,
        "summary": summary, "event_total": event_payload.get("total"),
        "active_critical_alerts": payloads.get("critical_alerts", {}).get("total"),
        "event_page": params["offset"] // PAGE_SIZE, "page_size": PAGE_SIZE,
        "map_points": map_points, "unresolved_locations": unresolved,
        "last_24h_events": last_24h, "adapter_errors": adapter_errors}


def _adapt_persisted_event(record):
    if not isinstance(record, dict):
        raise ValueError("event record must be an object")
    intelligence = record.get("intelligence") if isinstance(record.get("intelligence"), dict) else {}
    phase5 = intelligence.get("phase5") if isinstance(intelligence.get("phase5"), dict) else {}
    incidents = record.get("incident_intelligence") if isinstance(record.get("incident_intelligence"), list) else []
    stored_phase5 = next((row.get("intelligence") for row in reversed(incidents)
                          if isinstance(row, dict) and isinstance(row.get("intelligence"), dict)), {})
    phase5 = phase5 or stored_phase5 or (incidents[-1] if incidents and isinstance(incidents[-1], dict) else {})
    phase6 = intelligence.get("phase6") if isinstance(intelligence.get("phase6"), dict) else {}
    phase6_rows = phase6.get("incidents") if isinstance(phase6.get("incidents"), list) else []
    incident_id = phase5.get("incident_id")
    spatial = next((row for row in phase6_rows if isinstance(row, dict) and row.get("incident_id") == incident_id), {})
    if not spatial and len(phase6_rows) == 1 and isinstance(phase6_rows[0], dict):
        spatial = phase6_rows[0]
    location_evidence = record.get("location_evidence") if isinstance(record.get("location_evidence"), dict) else {}
    intelligence_phase4 = intelligence.get("phase4") if isinstance(intelligence.get("phase4"), dict) else {}
    extracted_locations = (location_evidence.get("phase4_location_entities") or
                           intelligence_phase4.get("nlp_location_entities") or
                           intelligence_phase4.get("location") or [])
    if isinstance(extracted_locations, str):
        extracted_locations = [extracted_locations]
    extracted_locations = [item.get("text") if isinstance(item, dict) else item
                           for item in extracted_locations if isinstance(item, (str, dict))]
    extracted_locations = [item for item in extracted_locations if isinstance(item, str) and item.strip()]
    spatial = {**spatial}
    for key, source_key in (("location_text", "geocoded_location_text"), ("latitude", "geocoded_latitude"),
                            ("longitude", "geocoded_longitude"), ("geocoding_status", "geocoding_status"),
                            ("coordinate_source", "coordinate_source")):
        if spatial.get(key) is None:
            spatial[key] = location_evidence.get(source_key)
    source_location = {"text": location_evidence.get("source_location_text"),
        "latitude": location_evidence.get("source_latitude"), "longitude": location_evidence.get("source_longitude"),
        "provenance": location_evidence.get("source_coordinate_provenance")}
    type_evidence = record.get("type_evidence") if isinstance(record.get("type_evidence"), dict) else {}
    type_resolution = phase5.get("type_resolution") if isinstance(phase5.get("type_resolution"), dict) else {}
    alerts = record.get("alerts") if isinstance(record.get("alerts"), list) else []
    alert_record = next((item for item in reversed(alerts) if item.get("alert_id")), {})
    raw_alert = alert_record.get("raw_alert") if isinstance(alert_record.get("raw_alert"), dict) else {}
    alert_decision = raw_alert.get("alert_decision") if isinstance(raw_alert.get("alert_decision"), dict) else {}
    operational_alert = alert_record if str(alert_record.get("alert_status") or "").upper() in {"ACTIVE", "CREATED", "ESCALATED"} else {}
    adapter_input = {**record, "event": {"title": record.get("title"), "text": record.get("text"),
        "original_text": record.get("original_text"), "event_timestamp": record.get("event_timestamp"),
        "observed_at": record.get("observed_at"), "published_at": record.get("published_at"),
        "updated_at": record.get("source_updated_at"), "retrieved_at": record.get("retrieved_at"),
        "metadata": record.get("metadata"), "source_url": record.get("source_url")},
        "incident_intelligence": phase5, "intelligence": intelligence,
        "type_resolution": type_resolution,
        "source_location": source_location, "spatial_context": spatial,
        "alert_decision": alert_decision,
        "alert_record": {"alert_id": operational_alert.get("alert_id"),
            "incident_id": operational_alert.get("incident_id"), "event_id": operational_alert.get("event_id"),
            "alert_level": operational_alert.get("alert_level"), "status": operational_alert.get("alert_status"),
            "created_at": operational_alert.get("created_at"),
            "trigger_reasons": operational_alert.get("triggering_rules"),
            "escalation": operational_alert.get("escalation_state"),
            "notification": {"status": operational_alert.get("notification_status")}},
        "alert_status": operational_alert.get("alert_status"),
        "phase_status": (record.get("processing_results") or {}).get("phase_status", {})}
    view = adapt_incident(adapter_input, dataset_mode="database")
    view.update({"event_id": record.get("event_id"), "incident_id": incident_id,
        "source": record.get("source"), "source_event_id": record.get("source_event_id"),
        "source_title": record.get("title"), "source_text": record.get("original_text") or record.get("text"),
        "event_timestamp": record.get("event_timestamp"), "observed_at": record.get("observed_at"),
        "updated_at": record.get("source_updated_at"), "retrieved_at": record.get("retrieved_at"),
        "last_seen": record.get("last_seen"), "event_state": record.get("event_state"),
        "processing_status": record.get("processing_status"),
        "source_type_evidence": type_evidence,
        "location_evidence": location_evidence,
        "decision_flags": phase5.get("decision_flags", []),
        "incident_status": record.get("incident_status"),
        "alert_state": {"status": alert_record.get("alert_status"),
                         "suppressed": alert_record.get("suppressed"),
                         "suppression_reason": alert_record.get("suppression_reason"),
                         "escalation_state": alert_record.get("escalation_state"),
                         "triggering_rules": alert_record.get("triggering_rules")},
        "source_provenance": record.get("provenance"),
        "spatial_record": spatial, "persisted_record": record})
    # The Phase 10 adapter emits coordinates only for successful Phase 6 status.
    coordinates_valid = _valid_success_point({"geocoding_status": spatial.get("geocoding_status"),
        "latitude": spatial.get("latitude"), "longitude": spatial.get("longitude")})
    view["latitude"] = spatial.get("latitude") if coordinates_valid else None
    view["longitude"] = spatial.get("longitude") if coordinates_valid else None
    view["location_text"] = ((spatial.get("location_text") or location_evidence.get("source_location_text") or
                               record.get("location_text")) if coordinates_valid else None)
    view["location_evidence_text"] = "; ".join(dict.fromkeys(extracted_locations)) or None
    view["normalized_location"] = spatial.get("normalized_location") or spatial.get("location_text")
    view["geocoding_status"] = ("invalid_coordinates" if spatial.get("geocoding_status") == "success" and not coordinates_valid
                                else spatial.get("geocoding_status") or "unknown")
    view["coordinate_source"] = spatial.get("coordinate_source") or location_evidence.get("coordinate_source")
    view["source_disaster_type"] = type_evidence.get("source_type") or type_evidence.get("source_canonical_type")
    view["phase4_disaster_type"] = type_evidence.get("phase4_type") or type_evidence.get("phase4_canonical_type")
    view["ml_disaster_type"] = type_evidence.get("ml_type") or type_evidence.get("ml_canonical_type")
    view["ml_confidence"] = type_evidence.get("ml_confidence")
    view["type_agreement_status"] = type_evidence.get("agreement_status")
    view["resolution_rationale"] = type_evidence.get("resolution_rationale")
    view["type_resolution"] = type_resolution
    view["alert_level"] = operational_alert.get("alert_level")
    view["alert_status"] = operational_alert.get("alert_status")
    view["alert_id"] = operational_alert.get("alert_id")
    view["active_alert"] = operational_alert or None
    view["processing_results"] = record.get("intelligence", {})
    view["raw_event"] = record.get("raw_event", {})
    return view


def _adapt_persisted_alert(item, event):
    raw = item.get("raw_alert") if isinstance(item.get("raw_alert"), dict) else {}
    decision = raw.get("alert_decision") if isinstance(raw.get("alert_decision"), dict) else item.get("alert_decision", {})
    record = raw.get("alert_record") if isinstance(raw.get("alert_record"), dict) else {}
    escalation = item.get("escalation_state") or record.get("escalation") or {}
    suppressed = item.get("suppressed")
    return {"alert_id": item.get("alert_id"), "event_id": item.get("event_id"),
        "incident_id": item.get("incident_id"), "alert_level": item.get("alert_level"),
        "alert_status": item.get("alert_status"), "alert_type": item.get("alert_type"),
        "created_at": item.get("created_at"), "updated_at": item.get("updated_at"),
        "triggered_rules": item.get("triggering_rules") or record.get("trigger_reasons") or [],
        "explanation": decision.get("trigger_reasons") or record.get("trigger_reasons") or [],
        "escalation": escalation, "suppressed": bool(suppressed),
        "suppression_reason": item.get("suppression_reason"),
        "notification_status": item.get("notification_status") or
            ((record.get("notification") or {}).get("status") if isinstance(record.get("notification"), dict) else None),
        "source": event.get("source") if event else None,
        "location_text": (event.get("location_evidence") or {}).get("geocoded_location_text") or
                         (event.get("location_evidence") or {}).get("source_location_text") if event else None,
        "disaster_type": event.get("canonical_disaster_type") if event else None,
        "priority_score": (item.get("score_evidence") or {}).get("priority_score") or decision.get("priority_score"),
        "severity_score": (item.get("score_evidence") or {}).get("severity_score") or decision.get("severity_score"),
        "event_record": event, "raw_alert": raw, "alert_decision": decision,
        "active": bool(item.get("alert_id") and item.get("alert_status", "").upper() in {"ACTIVE", "CREATED", "ESCALATED"})}


def _source_rows(latest_run):
    report_map = ((latest_run or {}).get("processing_results") or {}).get("source_reports") or {}
    normalized = {}
    for name, report in report_map.items():
        row = dict(report) if isinstance(report, dict) else {}
        row["name"] = name
        normalized[str(name).casefold()] = row
    rows = []
    for key, label in KNOWN_SOURCES.items():
        row = normalized.get(key)
        if row is not None:
            # Avoid duplicate RSS/news aliases if both appear in a report.
            rows.append({"name": label, "status": row.get("status", "unknown"),
                "received": row.get("records_received", 0), "processed": row.get("selected_for_processing", 0),
                "retained": row.get("valid_records", 0),
                "new": row.get("new_records", 0), "duplicate": row.get("duplicate_records", 0),
                "updated": row.get("updated_records", 0), "errors": row.get("errors") or ([row.get("error")] if row.get("error") else []),
                "retrieved_at": row.get("retrieval_timestamp"), "configured": True})
    rows = list({row["name"]: row for row in rows}.values())
    known_labels = {row["name"] for row in rows}
    for name, row in normalized.items():
        label = KNOWN_SOURCES.get(name, str(row.get("name") or name))
        if label in known_labels:
            continue
        rows.append({"name": label, "status": row.get("status", "unknown"),
            "received": row.get("records_received", 0), "processed": row.get("selected_for_processing", 0),
            "retained": row.get("valid_records", 0),
            "new": row.get("new_records", 0), "duplicate": row.get("duplicate_records", 0),
            "updated": row.get("updated_records", 0),
            "errors": row.get("errors") or ([row.get("error")] if row.get("error") else []),
            "retrieved_at": row.get("retrieval_timestamp"), "configured": True})
    missing_status = "not_configured" if latest_run and report_map is not None else "no_recent_run"
    for label in ("USGS", "GDACS", "NEWS / RSS", "Mastodon"):
        if label not in known_labels:
            rows.append({"name": label, "status": missing_status, "received": 0,
                "retained": 0, "processed": 0, "new": 0, "duplicate": 0, "updated": 0,
                "errors": [], "retrieved_at": None, "configured": False})
    return rows


def _valid_success_point(item):
    if item.get("geocoding_status") != "success":
        return False
    try:
        lat, lon = item.get("latitude"), item.get("longitude")
        return (not isinstance(lat, bool) and not isinstance(lon, bool) and
                -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180)
    except (TypeError, ValueError, OverflowError):
        return False


def _is_synthetic(record):
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    raw = record.get("raw_event") if isinstance(record.get("raw_event"), dict) else {}
    raw_metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    return bool(metadata.get("demonstration") or metadata.get("synthetic_data") or
                raw_metadata.get("demonstration") or raw_metadata.get("synthetic_data") or
                str(record.get("source", "")).casefold().startswith(("synthetic", "demo")))


def _is_recent_live_run(run, sources, now):
    if not run or str(run.get("status", "")).casefold() not in {"success", "partial_failure"}:
        return False
    actual_source = any(item.get("configured") and item.get("received", 0) for item in sources)
    if not actual_source:
        return False
    value = run.get("ended_at") or run.get("started_at")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = (now - parsed.astimezone(timezone.utc)).total_seconds()
        return -60 <= age <= 15 * 60
    except (TypeError, ValueError):
        return False


def _timestamp_key(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0


def _level_rank(value):
    return {"CRITICAL": 5, "HIGH": 4, "MEDIUM": 3, "MODERATE": 2, "LOW": 1}.get(str(value or "").upper(), 0)


def _number(value):
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else -1
    except (TypeError, ValueError, OverflowError):
        return -1


def _int(value, default):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _safe_error(exc):
    if isinstance(exc, DashboardAPIError):
        return str(exc)
    return "Dashboard data could not be loaded from the operational API"
