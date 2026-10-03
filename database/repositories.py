"""Transactional SQLite repositories for Phase 11.1 persistence."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import uuid

from database.config import get_database_path
from database.connection import connect, transaction
from database.models import EventUpsertResult, MonitoringRunResult
from database.schema import initialize_database
from database.serialization import from_json_text, to_json_text
from monitoring.identity import event_id_for
from monitoring.normalization import normalize_event, utc_now


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _tri_bool(value):
    if value is None:
        return None
    return int(bool(value))


def _decode_fields(item, fields):
    if item is None:
        return None
    result = dict(item)
    for column, target, default in fields:
        result[target] = from_json_text(result.pop(column, None), default)
    return result


def _event_bundle(record):
    """Accept normalized source events and Phase 8 processed-event wrappers."""
    outer = _mapping(record)
    raw_event = outer.get("event") if isinstance(outer.get("event"), dict) else outer
    if "content_fingerprint" in raw_event and "revision_fingerprint" in raw_event:
        normalized = dict(raw_event)
    else:
        normalized = normalize_event(raw_event)
    event_id = outer.get("event_id") or normalized.get("event_id") or event_id_for(normalized)
    intelligence = _mapping(outer.get("intelligence"))
    phase5 = (_mapping(outer.get("incident_intelligence")) or
              _mapping(outer.get("incident_record")) or _mapping(intelligence.get("phase5")))
    phase6 = _mapping(intelligence.get("phase6"))
    spatial_rows = phase6.get("incidents") or []
    spatial = next((row for row in spatial_rows if isinstance(row, dict) and
                    row.get("incident_id") == phase5.get("incident_id")), None)
    if spatial is None and len(spatial_rows) == 1:
        spatial = _mapping(spatial_rows[0])
    phase4 = _mapping(intelligence.get("phase4"))
    phase9 = outer if ("alert_decision" in outer or "alert_record" in outer) else _mapping(outer.get("phase9"))
    return outer, normalized, str(event_id), phase4, phase5, _mapping(spatial), phase9


class DatabaseRepository:
    """High-level repositories using short-lived connections and atomic writes."""

    def __init__(self, db_path=None, *, initialize=True):
        self.db_path = get_database_path(db_path)
        if initialize:
            initialize_database(self.db_path)

    @contextmanager
    def transaction(self):
        """Expose a bounded transaction for composition and rollback-safe callers."""
        with transaction(self.db_path) as connection:
            yield connection

    def upsert_event(self, record, *, type_evidence=None, source_location=None,
                     phase4_record=None, phase5_record=None, phase6_record=None,
                     alert_record=None):
        """Insert, detect duplicate, or revise an event using Phase 8 fingerprints.

        Duplicate observations update last-seen/retrieval metadata without adding
        a row or a revision. Material revisions update the same event and append
        one event_revisions snapshot.
        """
        outer, event, event_id, phase4, phase5, spatial, phase9 = _event_bundle(record)
        phase4 = _mapping(phase4_record) or phase4
        phase5 = _mapping(phase5_record) or phase5
        spatial = _mapping(phase6_record) or spatial
        type_evidence = _mapping(type_evidence) or _mapping(outer.get("type_resolution")) or \
            _mapping(phase5.get("type_resolution"))
        source_location = _mapping(source_location) or _mapping(outer.get("source_location")) or \
            _mapping(phase5.get("source_location"))
        alert_record = _mapping(alert_record) or phase9
        event = dict(event)
        event["canonical_disaster_type"] = (type_evidence.get("canonical_disaster_type") or
                                            phase5.get("canonical_disaster_type"))
        event["processing_status"] = outer.get("processing_status", event.get("processing_status"))
        event["processing_results"] = _mapping(outer.get("intelligence"))
        now = utc_now()
        source, source_event_id = event.get("source"), event.get("source_event_id")
        revision = event.get("revision_fingerprint") or _fallback_fingerprint(event)
        payload = event.get("raw_event") or event
        provenance = _mapping(event.get("provenance")) or _mapping(outer.get("provenance"))

        with transaction(self.db_path) as connection:
            existing = connection.execute(
                "SELECT * FROM events WHERE event_id = ? OR (source = ? AND source_event_id = ?) "
                "ORDER BY CASE WHEN event_id = ? THEN 0 ELSE 1 END LIMIT 1",
                (event_id, source, source_event_id, event_id)).fetchone()
            if existing is None:
                state, version = "NEW", 1
                first_seen = event.get("ingested_at") or now
                effective_id = event_id
                self._insert_event(connection, effective_id, event, revision, state, version,
                                   first_seen, now, payload, now)
                self._insert_revision(connection, effective_id, version, revision, event, payload, now)
            else:
                effective_id = existing["event_id"]
                first_seen = existing["first_seen"] or now
                if existing["revision_fingerprint"] == revision:
                    state, version = "DUPLICATE", existing["version"]
                else:
                    state, version = "UPDATED", existing["version"] + 1
                self._update_event(connection, effective_id, event, revision, state, version,
                                   first_seen, now, payload)
                if state == "UPDATED":
                    self._insert_revision(connection, effective_id, version, revision, event, payload, now)

            self._save_provenance(connection, effective_id, event, provenance, now)
            if type_evidence:
                self._save_type_evidence(connection, effective_id, type_evidence, now)
            location_evidence = source_location or _source_location_from_event(event)
            if location_evidence or phase4 or spatial:
                self._save_location_evidence(connection, effective_id, location_evidence,
                                             phase4.get("nlp_location_entities", phase4.get("location")),
                                             spatial, now)
            if phase5.get("incident_id"):
                self._save_incident_intelligence(connection, phase5, effective_id, now)
            if alert_record and (alert_record.get("alert_decision") or alert_record.get("alert_record")):
                self._save_alert(connection, alert_record, effective_id, phase5.get("incident_id"), now)
        return EventUpsertResult(effective_id, state, version)

    @staticmethod
    def _insert_event(connection, event_id, event, revision, state, version, first_seen, now, payload, db_now):
        connection.execute("""INSERT INTO events (
            event_id, source, source_event_id, title, text, original_text, source_url,
            location_text, latitude, longitude, event_timestamp, published_at, observed_at,
            source_updated_at, retrieved_at, ingested_at, disaster_type, canonical_disaster_type,
            content_fingerprint, revision_fingerprint, event_state, version, first_seen, last_seen,
            processing_status, metadata_json, processing_results_json, raw_event_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            _event_values(event_id, event, revision, state, version, first_seen, now, payload, db_now))

    @staticmethod
    def _update_event(connection, event_id, event, revision, state, version, first_seen, now, payload):
        values = _event_values(event_id, event, revision, state, version, first_seen, now, payload, now)
        connection.execute("""UPDATE events SET
            source=?, source_event_id=?, title=?, text=?, original_text=?, source_url=?,
            location_text=?, latitude=?, longitude=?, event_timestamp=?, published_at=?,
            observed_at=?, source_updated_at=?, retrieved_at=?, ingested_at=?, disaster_type=?,
            canonical_disaster_type=?, content_fingerprint=?, revision_fingerprint=?, event_state=?, version=?, first_seen=?,
            last_seen=?, processing_status=?, metadata_json=?, processing_results_json=?, raw_event_json=?, updated_at=?
            WHERE event_id=?""",
            (event.get("source"), event.get("source_event_id"), event.get("title"), event.get("text") or "",
             event.get("original_text"), event.get("source_url") or event.get("url"), event.get("location_text"),
             _number(event.get("latitude")), _number(event.get("longitude")), event.get("event_timestamp"),
             event.get("published_at"), event.get("observed_at"), event.get("updated_at"),
             event.get("retrieved_at"), event.get("ingested_at"), event.get("disaster_type"),
             _column_text(event.get("canonical_disaster_type")), event.get("content_fingerprint"), revision, state, version,
             first_seen, now, event.get("processing_status", state), to_json_text(_mapping(event.get("metadata"))),
             to_json_text(_mapping(event.get("processing_results"))), to_json_text(payload), now, event_id))

    @staticmethod
    def _insert_revision(connection, event_id, version, revision, event, payload, now):
        connection.execute("""INSERT INTO event_revisions
            (event_id, version, revision_fingerprint, observed_at, recorded_at, payload_json)
            VALUES(?,?,?,?,?,?)""",
            (event_id, version, revision, event.get("observed_at"), now,
             to_json_text({"event": payload, "metadata": event.get("metadata", {})})))

    def get_event(self, event_id):
        connection = connect(self.db_path)
        try:
            row = connection.execute("SELECT * FROM events WHERE event_id = ?", (str(event_id),)).fetchone()
            return self._hydrate(connection, row) if row else None
        finally:
            connection.close()

    def get_event_by_source_id(self, source, source_event_id):
        connection = connect(self.db_path)
        try:
            row = connection.execute("SELECT * FROM events WHERE source = ? AND source_event_id = ?",
                                     (source, str(source_event_id))).fetchone()
            return self._hydrate(connection, row) if row else None
        finally:
            connection.close()

    def list_events(self, *, source=None, limit=100, offset=0):
        limit, offset = max(0, min(int(limit), 1000)), max(0, int(offset))
        connection = connect(self.db_path)
        try:
            if source is None:
                rows = connection.execute("SELECT * FROM events ORDER BY ingested_at DESC, event_id LIMIT ? OFFSET ?",
                                          (limit, offset)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM events WHERE source = ? ORDER BY ingested_at DESC, event_id LIMIT ? OFFSET ?",
                                          (source, limit, offset)).fetchall()
            return [self._hydrate(connection, row) for row in rows]
        finally:
            connection.close()

    def query_events(self, *, source=None, disaster_type=None, operational_type=None,
                     severity=None, priority=None, location=None, since=None, until=None,
                     limit=50, offset=0, include_history=True):
        """Filter/page events with parameterized SQL; returns (items, total)."""
        clauses, params = [], []
        if source is not None:
            clauses.append("e.source=?"); params.append(source)
        if disaster_type is not None:
            clauses.append("(e.disaster_type=? OR e.canonical_disaster_type=?)")
            params.extend((disaster_type, disaster_type))
        if operational_type is not None:
            clauses.append("e.canonical_disaster_type=?"); params.append(operational_type)
        if severity is not None:
            clauses.append("EXISTS(SELECT 1 FROM incident_intelligence i WHERE i.event_id=e.event_id AND upper(i.severity_level)=upper(?))")
            params.append(severity)
        if priority is not None:
            clauses.append("EXISTS(SELECT 1 FROM incident_intelligence i WHERE i.event_id=e.event_id AND upper(i.priority_level)=upper(?))")
            params.append(priority)
        if location is not None:
            clauses.append("(e.location_text LIKE ? OR EXISTS(SELECT 1 FROM location_evidence l WHERE l.event_id=e.event_id AND l.geocoded_location_text LIKE ?))")
            params.extend((f"%{location}%", f"%{location}%"))
        if since is not None:
            clauses.append("COALESCE(e.event_timestamp,e.published_at,e.ingested_at)>=?"); params.append(since)
        if until is not None:
            clauses.append("COALESCE(e.event_timestamp,e.published_at,e.ingested_at)<=?"); params.append(until)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        limit, offset = max(1, min(int(limit), 1000)), max(0, int(offset))
        connection = connect(self.db_path)
        try:
            total = connection.execute("SELECT COUNT(*) FROM events e" + where, params).fetchone()[0]
            rows = connection.execute("SELECT e.* FROM events e" + where +
                " ORDER BY COALESCE(e.event_timestamp,e.published_at,e.ingested_at,e.created_at) DESC,e.event_id LIMIT ? OFFSET ?",
                params + [limit, offset]).fetchall()
            return [self._hydrate(connection, row, include_history=include_history) for row in rows], total
        finally:
            connection.close()

    @staticmethod
    def _hydrate(connection, row, *, include_history=True):
        event = dict(row)
        event["metadata"] = from_json_text(event.pop("metadata_json", None), {})
        event["intelligence"] = from_json_text(event.pop("processing_results_json", None), {})
        event["raw_event"] = from_json_text(event.pop("raw_event_json", None), {})
        event["provenance"] = _decode_fields(connection.execute(
            "SELECT * FROM event_provenance WHERE event_id = ?", (event["event_id"],)).fetchone(),
            [("provenance_json", "details", {})])
        event["type_evidence"] = _decode_fields(connection.execute(
            "SELECT * FROM disaster_type_evidence WHERE event_id = ?", (event["event_id"],)).fetchone(),
            [("source_type_json", "source_type", None), ("phase4_type_json", "phase4_type", None),
             ("warnings_json", "warnings", []), ("evidence_json", "evidence", {})])
        event["location_evidence"] = _decode_fields(connection.execute(
            "SELECT * FROM location_evidence WHERE event_id = ?", (event["event_id"],)).fetchone(),
            [("phase4_location_entities_json", "phase4_location_entities", []),
             ("evidence_json", "evidence", {})])
        event["revisions"] = ([_decode_fields(item, [("payload_json", "payload", {})]) for item in
            connection.execute("SELECT * FROM event_revisions WHERE event_id = ? ORDER BY version", (event["event_id"],)).fetchall()]
            if include_history else [])
        event["incident_intelligence"] = [_decode_fields(item, [
            ("decision_flags_json", "decision_flags", []), ("resource_priorities_json", "resource_priorities", {}),
            ("resource_estimates_json", "resource_estimates", {}), ("duplicate_info_json", "duplicate_info", {}),
            ("processing_metadata_json", "processing_metadata", {}), ("intelligence_json", "intelligence", {})])
            for item in connection.execute("SELECT * FROM incident_intelligence WHERE event_id = ? ORDER BY created_at",
                                           (event["event_id"],)).fetchall()]
        event["alerts"] = [_decode_fields(item, [
            ("alert_decision_json", "alert_decision", {}), ("triggering_rules_json", "triggering_rules", []),
            ("score_evidence_json", "score_evidence", {}), ("escalation_state_json", "escalation_state", {}),
            ("raw_alert_json", "raw_alert", {})])
            for item in connection.execute("SELECT * FROM alerts WHERE event_id = ? ORDER BY created_at, id",
                                           (event["event_id"],)).fetchall()] if include_history else []
        event["alert_history"] = ([_decode_fields(item, [("details_json", "details", {})]) for item in
            connection.execute("SELECT * FROM alert_history WHERE event_id = ? ORDER BY occurred_at, history_id",
                               (event["event_id"],)).fetchall()] if include_history else [])
        return event

    def save_provenance(self, event_id, provenance):
        with transaction(self.db_path) as connection:
            row = connection.execute("SELECT * FROM events WHERE event_id = ?", (str(event_id),)).fetchone()
            if row is None:
                raise ValueError(f"event not found: {event_id}")
            data = _mapping(provenance)
            now = utc_now()
            self._save_provenance(connection, str(event_id), dict(row), data, now)

    @staticmethod
    def _save_provenance(connection, event_id, event, provenance, now):
        provenance = _mapping(provenance)
        source = event["source"]
        data = (event_id, source, provenance.get("source_type"), event.get("source_event_id"),
                provenance.get("source_url") or event.get("source_url"), provenance.get("adapter"),
                provenance.get("adapter_version"), provenance.get("observed_at") or event.get("observed_at"),
                provenance.get("event_timestamp") or event.get("event_timestamp"),
                provenance.get("published_at") or event.get("published_at"),
                provenance.get("updated_at") or event.get("source_updated_at") or event.get("updated_at"),
                provenance.get("retrieved_at") or event.get("retrieved_at"),
                provenance.get("ingested_at") or event.get("ingested_at"),
                provenance.get("processing_status") or event.get("processing_status"),
                to_json_text(provenance), now)
        connection.execute("""INSERT INTO event_provenance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_id) DO UPDATE SET source=excluded.source, source_type=excluded.source_type,
            source_event_id=excluded.source_event_id, source_url=excluded.source_url, adapter=excluded.adapter,
            adapter_version=excluded.adapter_version, observed_at=excluded.observed_at,
            event_timestamp=excluded.event_timestamp, published_at=excluded.published_at,
            source_updated_at=excluded.source_updated_at, retrieved_at=excluded.retrieved_at,
            ingested_at=excluded.ingested_at, processing_status=excluded.processing_status,
            provenance_json=excluded.provenance_json, updated_at=excluded.updated_at""", data)

    def save_type_evidence(self, event_id, evidence):
        with transaction(self.db_path) as connection:
            _require_event(connection, event_id)
            self._save_type_evidence(connection, str(event_id), _mapping(evidence), utc_now())

    @staticmethod
    def _save_type_evidence(connection, event_id, evidence, now):
        source = _mapping(evidence.get("source_disaster_type"))
        phase4 = _mapping(evidence.get("phase4_disaster_type"))
        ml = _mapping(evidence.get("ml_disaster_type"))
        warnings = evidence.get("warnings")
        if warnings is None:
            warnings = [evidence["warning"]] if evidence.get("warning") else []
        values = (event_id, _column_text(evidence.get("canonical_disaster_type")),
                  to_json_text(source.get("value") if source else evidence.get("source_type")),
                  _column_text(source.get("canonical_value")),
                  to_json_text(phase4.get("value") if phase4 else evidence.get("phase4_type")),
                  _column_text(phase4.get("canonical_value")),
                  _column_text(ml.get("value") if ml else evidence.get("ml_type")),
                  _column_text(ml.get("canonical_value")), _number(ml.get("confidence", evidence.get("ml_confidence"))),
                  _column_text(evidence.get("type_agreement_status", evidence.get("agreement_status"))),
                  _tri_bool(evidence.get("type_agreement")), int(bool(evidence.get("model_disagreement"))),
                  _column_text(evidence.get("resolution_method", evidence.get("selected_from"))),
                  _column_text(evidence.get("resolution_rationale") or evidence.get("resolution_reason")),
                  to_json_text(warnings), to_json_text(evidence), now)
        connection.execute("""INSERT INTO disaster_type_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_id) DO UPDATE SET operational_type=excluded.operational_type,
            source_type_json=excluded.source_type_json, source_canonical_type=excluded.source_canonical_type,
            phase4_type_json=excluded.phase4_type_json, phase4_canonical_type=excluded.phase4_canonical_type,
            ml_type=excluded.ml_type, ml_canonical_type=excluded.ml_canonical_type,
            ml_confidence=excluded.ml_confidence, agreement_status=excluded.agreement_status,
            type_agreement=excluded.type_agreement, model_disagreement=excluded.model_disagreement,
            resolution_method=excluded.resolution_method, resolution_rationale=excluded.resolution_rationale,
            warnings_json=excluded.warnings_json, evidence_json=excluded.evidence_json, updated_at=excluded.updated_at""", values)

    def save_location_evidence(self, event_id, source_location=None, phase4_entities=None, spatial=None):
        with transaction(self.db_path) as connection:
            _require_event(connection, event_id)
            self._save_location_evidence(connection, str(event_id), _mapping(source_location),
                                          phase4_entities, _mapping(spatial), utc_now())

    @staticmethod
    def _save_location_evidence(connection, event_id, source_location, phase4_entities, spatial, now):
        source_location = _mapping(source_location)
        spatial = _mapping(spatial)
        coordinate_source = spatial.get("coordinate_source")
        source_lat = _number(source_location.get("latitude"))
        source_lon = _number(source_location.get("longitude"))
        if source_lat is None and coordinate_source == "source_metadata":
            source_lat = _number(spatial.get("latitude"))
        if source_lon is None and coordinate_source == "source_metadata":
            source_lon = _number(spatial.get("longitude"))
        geocoded = coordinate_source == "geocoder" and spatial.get("geocoding_status") == "success"
        values = (event_id, _column_text(source_location.get("text")), source_lat, source_lon,
                  _column_text(source_location.get("coordinate_provenance") or source_location.get("provenance")),
                  to_json_text(phase4_entities if phase4_entities is not None else []),
                  _column_text(spatial.get("normalized_location")) if geocoded else None,
                  _number(spatial.get("latitude")) if geocoded else None,
                  _number(spatial.get("longitude")) if geocoded else None,
                  _column_text(coordinate_source), _column_text(spatial.get("geocoding_status")),
                  to_json_text({"source_location": source_location, "spatial": spatial}), now)
        connection.execute("""INSERT INTO location_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_id) DO UPDATE SET source_location_text=excluded.source_location_text,
            source_latitude=excluded.source_latitude, source_longitude=excluded.source_longitude,
            source_coordinate_provenance=excluded.source_coordinate_provenance,
            phase4_location_entities_json=excluded.phase4_location_entities_json,
            geocoded_location_text=excluded.geocoded_location_text,
            geocoded_latitude=excluded.geocoded_latitude, geocoded_longitude=excluded.geocoded_longitude,
            coordinate_source=excluded.coordinate_source, geocoding_status=excluded.geocoding_status,
            evidence_json=excluded.evidence_json, updated_at=excluded.updated_at""", values)

    def save_incident_intelligence(self, incident, event_id=None):
        data = _mapping(incident)
        event_id = event_id or data.get("event_id")
        if not data.get("incident_id") or not event_id:
            raise ValueError("incident_id and event_id are required")
        with transaction(self.db_path) as connection:
            _require_event(connection, event_id)
            self._save_incident_intelligence(connection, data, str(event_id), utc_now())

    @staticmethod
    def _save_incident_intelligence(connection, incident, event_id, now):
        processing = _mapping(incident.get("processing_metadata"))
        duplicate = {key: incident.get(key) for key in
                     ("is_duplicate", "duplicate_of", "similarity_scores") if key in incident}
        values = (str(incident["incident_id"]), event_id,
                  _number(incident.get("severity_score")), incident.get("severity_level"),
                  _number(incident.get("urgency_score")), incident.get("urgency_level"),
                  _number(incident.get("confidence_score")), incident.get("confidence_level"),
                  _number(incident.get("priority_score")), incident.get("priority_level"),
                  to_json_text(incident.get("decision_flags", [])),
                  to_json_text(incident.get("resource_priorities", {})),
                  to_json_text(incident.get("resource_estimates", {})), to_json_text(duplicate),
                  processing.get("phase5_version"), to_json_text(processing), to_json_text(incident), now, now)
        connection.execute("""INSERT INTO incident_intelligence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(incident_id) DO UPDATE SET event_id=excluded.event_id,
            severity_score=excluded.severity_score,severity_level=excluded.severity_level,
            urgency_score=excluded.urgency_score,urgency_level=excluded.urgency_level,
            confidence_score=excluded.confidence_score,confidence_level=excluded.confidence_level,
            priority_score=excluded.priority_score,priority_level=excluded.priority_level,
            decision_flags_json=excluded.decision_flags_json,
            resource_priorities_json=excluded.resource_priorities_json,
            resource_estimates_json=excluded.resource_estimates_json,duplicate_info_json=excluded.duplicate_info_json,
            phase_version=excluded.phase_version,processing_metadata_json=excluded.processing_metadata_json,
            intelligence_json=excluded.intelligence_json,updated_at=excluded.updated_at""", values)

    def save_alert(self, record, *, event_id=None, incident_id=None):
        with transaction(self.db_path) as connection:
            return self._save_alert(connection, _mapping(record), event_id, incident_id, utc_now())

    @staticmethod
    def _save_alert(connection, record, event_id, incident_id, now):
        alert_record = _mapping(record.get("alert_record"))
        decision = _mapping(record.get("alert_decision"))
        alert_id = alert_record.get("alert_id") or decision.get("alert_id") or record.get("alert_id")
        event_id = event_id or record.get("event_id") or alert_record.get("event_id") or decision.get("event_id")
        incident_id = incident_id or alert_record.get("incident_id") or decision.get("incident_id")
        if event_id is not None:
            _require_event(connection, event_id)
        evaluated_at = record.get("alert_evaluated_at") or alert_record.get("created_at") or now
        evaluation_key = _digest({"event_id": event_id, "incident_id": incident_id,
                                  "alert_id": alert_id, "evaluated_at": evaluated_at,
                                  "status": record.get("alert_status")})
        trigger_reasons = (alert_record.get("trigger_reasons") or decision.get("trigger_reasons") or
                           decision.get("triggered_rules") or [])
        suppression = _mapping(record.get("alert_suppression")) or _mapping(alert_record.get("suppression"))
        notification = _mapping(alert_record.get("notification"))
        escalation = _mapping(alert_record.get("escalation")) or {
            "is_escalation": decision.get("is_escalation"),
            "reasons": decision.get("escalation_reasons"), "deescalated": decision.get("deescalated")}
        scores = {key: decision.get(key, alert_record.get(key)) for key in
                  ("priority_score", "severity_score", "urgency_score", "confidence_score")}
        created_at = alert_record.get("created_at") or evaluated_at
        values = (alert_id, evaluation_key, str(event_id) if event_id is not None else None,
                  str(incident_id) if incident_id is not None else None,
                  alert_record.get("alert_level") or decision.get("alert_level"),
                  record.get("alert_status") or alert_record.get("status"),
                  alert_record.get("alert_type") or decision.get("alert_type"), to_json_text(decision),
                  to_json_text(trigger_reasons), to_json_text(scores), to_json_text(escalation),
                  _tri_bool(suppression.get("suppressed", decision.get("suppressed"))),
                  suppression.get("reason") or decision.get("suppression_reason"),
                  notification.get("status"), created_at, now, to_json_text(record))
        existing = None
        if alert_id:
            existing = connection.execute("SELECT id FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
        if existing:
            connection.execute("""UPDATE alerts SET evaluation_key=?,event_id=?,incident_id=?,alert_level=?,
                alert_status=?,alert_type=?,alert_decision_json=?,triggering_rules_json=?,score_evidence_json=?,
                escalation_state_json=?,suppressed=?,suppression_reason=?,notification_status=?,updated_at=?,raw_alert_json=?
                WHERE id=?""", values[1:14] + (now, values[16], existing["id"]))
            return alert_id
        connection.execute("""INSERT INTO alerts (alert_id,evaluation_key,event_id,incident_id,alert_level,
            alert_status,alert_type,alert_decision_json,triggering_rules_json,score_evidence_json,
            escalation_state_json,suppressed,suppression_reason,notification_status,created_at,updated_at,raw_alert_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(evaluation_key) DO UPDATE SET
            alert_level=excluded.alert_level,alert_status=excluded.alert_status,alert_type=excluded.alert_type,
            alert_decision_json=excluded.alert_decision_json,triggering_rules_json=excluded.triggering_rules_json,
            score_evidence_json=excluded.score_evidence_json,escalation_state_json=excluded.escalation_state_json,
            suppressed=excluded.suppressed,suppression_reason=excluded.suppression_reason,
            notification_status=excluded.notification_status,updated_at=excluded.updated_at,raw_alert_json=excluded.raw_alert_json""", values)
        return alert_id

    def save_alert_history(self, history, *, event_id=None, incident_id=None, alert_id=None):
        data = _mapping(history)
        occurred_at = data.get("occurred_at") or data.get("changed_at") or utc_now()
        details = data.get("details", data)
        history_key = data.get("history_key") or _digest({"alert_id": alert_id or data.get("alert_id"),
            "event_id": event_id or data.get("event_id"), "incident_id": incident_id or data.get("incident_id"),
            "from_state": data.get("from_state"), "to_state": data.get("to_state"),
            "change_type": data.get("change_type"), "occurred_at": occurred_at, "details": details})
        with transaction(self.db_path) as connection:
            effective_event_id = event_id or data.get("event_id")
            if effective_event_id is not None:
                _require_event(connection, effective_event_id)
            cursor = connection.execute("""INSERT INTO alert_history
                (history_key,alert_id,event_id,incident_id,from_state,to_state,change_type,reason,occurred_at,details_json)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(history_key) DO NOTHING""",
                (history_key, alert_id or data.get("alert_id"), effective_event_id,
                 incident_id or data.get("incident_id"), data.get("from_state"), data.get("to_state"),
                 data.get("change_type"), data.get("reason"), occurred_at, to_json_text(details)))
            return cursor.rowcount == 1

    def save_monitoring_run(self, run):
        data = _mapping(run)
        now = utc_now()
        run_id = str(data.get("run_id") or uuid.uuid4())
        configuration = data.get("configuration_summary", data.get("configuration", {}))
        values = (run_id, data.get("source"), data.get("run_mode", data.get("mode")),
                  data.get("started_at", data.get("start_time")), data.get("ended_at", data.get("end_time")),
                  data.get("status"), _integer(data.get("records_received")),
                  _integer(data.get("valid_records")), _integer(data.get("invalid_records")),
                  _integer(data.get("new_records")), _integer(data.get("duplicate_records")),
                  _integer(data.get("updated_records")), to_json_text(data.get("errors", [])),
                  to_json_text(configuration), to_json_text(data.get("processing_results", {})),
                  _number(data.get("latency_seconds")), now)
        with transaction(self.db_path) as connection:
            connection.execute("""INSERT INTO monitoring_runs
                (run_id,source,run_mode,started_at,ended_at,status,records_received,valid_records,
                 invalid_records,new_records,duplicate_records,updated_records,errors_json,configuration_json,
                 processing_results_json,latency_seconds,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET source=excluded.source,run_mode=excluded.run_mode,
                started_at=excluded.started_at,ended_at=excluded.ended_at,status=excluded.status,
                records_received=excluded.records_received,valid_records=excluded.valid_records,
                invalid_records=excluded.invalid_records,new_records=excluded.new_records,
                duplicate_records=excluded.duplicate_records,updated_records=excluded.updated_records,
                errors_json=excluded.errors_json,configuration_json=excluded.configuration_json,
                processing_results_json=excluded.processing_results_json,latency_seconds=excluded.latency_seconds""", values)
        return MonitoringRunResult(run_id)

    def list_monitoring_runs(self, *, source=None, limit=100, offset=0):
        limit, offset = max(1, min(int(limit), 1000)), max(0, int(offset))
        connection = connect(self.db_path)
        try:
            if source is None:
                rows = connection.execute("SELECT * FROM monitoring_runs ORDER BY started_at DESC, run_id LIMIT ? OFFSET ?",
                                          (limit, offset)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM monitoring_runs WHERE source = ? ORDER BY started_at DESC, run_id LIMIT ? OFFSET ?",
                                          (source, limit, offset)).fetchall()
            return [_decode_fields(row, [("errors_json", "errors", []),
                                         ("configuration_json", "configuration_summary", {}),
                                         ("processing_results_json", "processing_results", {})]) for row in rows]
        finally:
            connection.close()

    def get_monitoring_run(self, run_id):
        connection = connect(self.db_path)
        try:
            row = connection.execute("SELECT * FROM monitoring_runs WHERE run_id=?", (str(run_id),)).fetchone()
            return _decode_fields(row, [("errors_json", "errors", []),
                ("configuration_json", "configuration_summary", {}),
                ("processing_results_json", "processing_results", {})]) if row else None
        finally:
            connection.close()

    def count_monitoring_runs(self, *, source=None):
        connection = connect(self.db_path)
        try:
            if source is None:
                return connection.execute("SELECT COUNT(*) FROM monitoring_runs").fetchone()[0]
            return connection.execute("SELECT COUNT(*) FROM monitoring_runs WHERE source=?", (source,)).fetchone()[0]
        finally:
            connection.close()

    def health_check(self):
        connection = connect(self.db_path)
        try:
            return connection.execute("SELECT 1").fetchone()[0] == 1
        finally:
            connection.close()

    def query_alerts(self, *, alert_level=None, active=None, source=None, event_id=None,
                     since=None, until=None, limit=50, offset=0):
        clauses, params = [], []
        if alert_level is not None:
            clauses.append("upper(a.alert_level)=upper(?)"); params.append(alert_level)
        if active is not None:
            clauses.append("upper(a.alert_status) IN (" + ("'ACTIVE','ESCALATED','CREATED'" if active else "'RESOLVED','CLOSED','INACTIVE','SUPPRESSED','NO_ALERT'") + ")")
        if source is not None:
            clauses.append("e.source=?"); params.append(source)
        if event_id is not None:
            clauses.append("a.event_id=?"); params.append(event_id)
        if since is not None:
            clauses.append("COALESCE(a.created_at,a.updated_at)>=?"); params.append(since)
        if until is not None:
            clauses.append("COALESCE(a.created_at,a.updated_at)<=?"); params.append(until)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        limit, offset = max(1, min(int(limit), 1000)), max(0, int(offset))
        connection = connect(self.db_path)
        try:
            total = connection.execute("SELECT COUNT(*) FROM alerts a LEFT JOIN events e ON e.event_id=a.event_id" + where, params).fetchone()[0]
            rows = connection.execute("SELECT a.* FROM alerts a LEFT JOIN events e ON e.event_id=a.event_id" + where +
                " ORDER BY COALESCE(a.created_at,a.updated_at) DESC,a.id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
            fields = [("alert_decision_json", "alert_decision", {}), ("triggering_rules_json", "triggering_rules", []),
                ("score_evidence_json", "score_evidence", {}), ("escalation_state_json", "escalation_state", {}),
                ("raw_alert_json", "raw_alert", {})]
            return [_decode_fields(row, fields) for row in rows], total
        finally:
            connection.close()

    def get_alert(self, alert_id):
        connection = connect(self.db_path)
        try:
            row = connection.execute("SELECT * FROM alerts WHERE alert_id=?", (str(alert_id),)).fetchone()
            if row is None:
                return None
            return _decode_fields(row, [("alert_decision_json", "alert_decision", {}),
                ("triggering_rules_json", "triggering_rules", []), ("score_evidence_json", "score_evidence", {}),
                ("escalation_state_json", "escalation_state", {}), ("raw_alert_json", "raw_alert", {})])
        finally:
            connection.close()

    def get_alert_history(self, alert_id):
        connection = connect(self.db_path)
        try:
            rows = connection.execute("SELECT * FROM alert_history WHERE alert_id=? ORDER BY occurred_at,history_id", (str(alert_id),)).fetchall()
            return [_decode_fields(row, [("details_json", "details", {})]) for row in rows]
        finally:
            connection.close()

    def intelligence_summary(self):
        connection = connect(self.db_path)
        try:
            def groups(sql):
                return {row[0] or "UNKNOWN": row[1] for row in connection.execute(sql)}
            recent = connection.execute("SELECT run_id,source,status,started_at,ended_at FROM monitoring_runs ORDER BY started_at DESC,run_id LIMIT 1").fetchone()
            return {"total_events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                "events_by_source": groups("SELECT source,COUNT(*) FROM events GROUP BY source"),
                "events_by_operational_type": groups("SELECT canonical_disaster_type,COUNT(*) FROM events GROUP BY canonical_disaster_type"),
                "severity_distribution": groups("SELECT severity_level,COUNT(*) FROM incident_intelligence GROUP BY severity_level"),
                "priority_distribution": groups("SELECT priority_level,COUNT(*) FROM incident_intelligence GROUP BY priority_level"),
                "active_alerts": connection.execute("SELECT COUNT(*) FROM alerts WHERE upper(alert_status) IN ('ACTIVE','ESCALATED','CREATED')").fetchone()[0],
                "alert_levels": groups("SELECT alert_level,COUNT(*) FROM alerts GROUP BY alert_level"),
                "unresolved_locations": connection.execute("""SELECT COUNT(*) FROM events e
                    LEFT JOIN location_evidence l ON l.event_id=e.event_id
                    WHERE NOT (l.geocoding_status='success' AND (
                        (l.coordinate_source IN ('source_metadata','source_text_gps') AND
                         l.source_latitude BETWEEN -90 AND 90 AND l.source_longitude BETWEEN -180 AND 180)
                        OR (l.coordinate_source='geocoder' AND
                            l.geocoded_latitude BETWEEN -90 AND 90 AND l.geocoded_longitude BETWEEN -180 AND 180)))""").fetchone()[0],
                "recent_monitoring_status": dict(recent) if recent else None}
        finally:
            connection.close()


def _event_values(event_id, event, revision, state, version, first_seen, now, payload, db_now):
    metadata = _mapping(event.get("metadata"))
    return (event_id, event.get("source"), event.get("source_event_id"), event.get("title"),
            event.get("text") or "", event.get("original_text"), event.get("source_url") or event.get("url"),
            event.get("location_text"), _number(event.get("latitude")), _number(event.get("longitude")),
            event.get("event_timestamp"), event.get("published_at"), event.get("observed_at"),
            event.get("updated_at"), event.get("retrieved_at"), event.get("ingested_at"),
            _column_text(event.get("disaster_type")), _column_text(event.get("canonical_disaster_type")), event.get("content_fingerprint"), revision,
            state, version, first_seen, now, event.get("processing_status", state),
            to_json_text(metadata), to_json_text(_mapping(event.get("processing_results"))),
            to_json_text(payload), db_now, db_now)


def _fallback_fingerprint(event):
    excluded = {"retrieved_at", "ingested_at", "raw_event", "provenance", "content_fingerprint",
                "revision_fingerprint", "event_state", "processing_status"}
    stable = {key: value for key, value in event.items() if key not in excluded}
    return hashlib.sha256(to_json_text(stable).encode("utf-8")).hexdigest()


def _source_location_from_event(event):
    if not any(event.get(key) is not None for key in ("location_text", "latitude", "longitude")):
        return {}
    return {"text": event.get("location_text"), "latitude": event.get("latitude"),
            "longitude": event.get("longitude"), "source": event.get("source"),
            "provenance": event.get("coordinate_provenance") or "authoritative_structured_metadata",
            "coordinate_source": "source_text_gps" if
                event.get("coordinate_provenance") == "explicit_source_text_gps" else "source_metadata"}


def _require_event(connection, event_id):
    if connection.execute("SELECT 1 FROM events WHERE event_id = ?", (str(event_id),)).fetchone() is None:
        raise ValueError(f"event not found: {event_id}")


def _integer(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _column_text(value):
    """Coerce optional nested/untrusted values to a SQLite TEXT-compatible form."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return to_json_text(value)


def _digest(value):
    return hashlib.sha256(to_json_text(value).encode("utf-8")).hexdigest()
