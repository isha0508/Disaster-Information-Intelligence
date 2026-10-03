"""One-cycle live feed session joining source adapters to existing Phase 8/9."""

from collections import Counter
import logging
import os
import re
import signal
from time import perf_counter
from threading import Lock
from uuid import uuid4

from alerting import AlertEvaluator
from monitoring.config import MonitoringConfig
from monitoring.pipeline import IntelligencePipeline
from monitoring.real_sources import MultiSource, RealSourceConfig, configured_sources
from gis.geocoding import configured_geocoder
from monitoring.runner import MonitoringRunner
from monitoring.normalization import utc_now


class LiveMonitoringSession:
    """Reusable polling session with shared event and alert state between cycles."""

    def __init__(self, source_config=None, *, event_source=None, runner=None, alert_evaluator=None,
                 run_phase3=True, repository=None):
        self.source_config = source_config or RealSourceConfig.from_env()
        self.source = event_source or MultiSource(configured_sources(self.source_config))
        self.runner = runner or MonitoringRunner(
            config=MonitoringConfig(polling_interval_seconds=self.source_config.polling_interval_seconds,
                                    max_events_per_cycle=max(1, self.source_config.max_events_per_source * 10),
                                    run_phase3=run_phase3),
            pipeline=IntelligencePipeline(geocoder=configured_geocoder(), run_phase3=run_phase3))
        self.alert_evaluator = alert_evaluator or AlertEvaluator()
        self.repository = repository
        self._cycle_lock = Lock()

    def run_cycle(self):
        # Also protect direct session callers; the service loop itself is serial.
        with self._cycle_lock:
            return self._run_cycle()

    def _run_cycle(self):
        started_clock = perf_counter()
        run_id = str(uuid4())
        started_at = utc_now()
        run_started_persisted = False
        if self.repository is not None:
            try:
                self.repository.save_monitoring_run({
                    "run_id": run_id, "source": "multi_source", "run_mode": "poll",
                    "started_at": started_at, "status": "running", "records_received": 0,
                    "valid_records": 0, "invalid_records": 0, "errors": [],
                    "configuration_summary": {"phase3_enabled": self.runner.config.run_phase3,
                        "max_events_per_source": self.source_config.max_events_per_source},
                    "processing_results": {"source_reports": {}}})
                run_started_persisted = True
            except Exception as exc:
                logging.getLogger("monitoring.session").error(
                    "could not persist monitoring-run start: %s", _safe_error_text(exc))
        fetch_error = None
        try:
            raw = list(self.source.fetch() or [])
        except Exception as exc:
            raw = []
            fetch_error = {"source": getattr(self.source, "name", "source"),
                           "error_type": type(exc).__name__, "message": _safe_error_text(exc)}
        reports = getattr(self.source, "source_reports", {})
        reports = {name: dict(report) for name, report in reports.items()}
        if fetch_error:
            reports[fetch_error["source"]] = {"status": "error", "records_received": 0,
                                                "errors": [fetch_error["message"]], **fetch_error}
        selected, selected_counts = [], Counter()
        for item in raw:
            name = item.get("source") if isinstance(item, dict) else None
            if name is None:
                selected.append(item)
            elif selected_counts[name] < self.source_config.max_events_per_source:
                selected.append(item)
                selected_counts[name] += 1
        records = self.runner.process_events(selected)
        state_counts = Counter(str(item.get("event_state", "UNKNOWN")).upper() for item in records)
        alerts = []
        for record in records:
            if record.get("event_state") in {"INVALID", "FAILED"} or not record.get("event_id"):
                continue
            alert = self.alert_evaluator.evaluate(record)
            record["phase9"] = alert
            # Phase 10 already accepts these direct Phase 9 fields from a Phase 8 record.
            for key in ("alert_decision", "alert_record", "alert_status", "alert_suppression"):
                if key in alert:
                    record[key] = alert[key]
            alerts.append(alert)
        persistence_errors = []
        persistence_counts = Counter()
        if self.repository is not None:
            for record in records:
                if not record.get("event_id") or record.get("event_state") in {"INVALID", "FAILED"}:
                    continue
                try:
                    saved = self.repository.upsert_event(record)
                    record["database_event_state"] = saved.state
                    record["event_state"] = saved.state
                    record["version"] = saved.version
                    persistence_counts[saved.state] += 1
                    if record.get("alert_record") and record["alert_record"].get("alert_id"):
                        alert = record["alert_record"]
                        self.repository.save_alert_history({
                            "alert_id": alert["alert_id"], "event_id": saved.event_id,
                            "incident_id": alert.get("incident_id"), "from_state": None,
                            "to_state": alert.get("alert_level"), "change_type": "CREATED",
                            "occurred_at": alert.get("created_at") or record.get("alert_evaluated_at"),
                            "reason": "Phase 9 created an operational alert", "details": alert})
                except Exception as exc:
                    message = {"event_id": record.get("event_id"), "error_type": type(exc).__name__,
                               "message": str(exc)[:500]}
                    persistence_errors.append(message)
                    record.setdefault("processing_errors", []).append({"phase": "database", **message})
        finalized_reports = {}
        for name, report in reports.items():
            copy = dict(report)
            subset = [item for item in records if item.get("source") == name]
            counts = Counter(str(item.get("event_state", "UNKNOWN")).upper() for item in subset)
            copy.update({"selected_for_processing": len(subset),
                         "not_processed_due_to_limit": max(0, report.get("valid_records", 0) - len(subset)),
                         "new_records": counts.get("NEW", 0),
                         "duplicate_records": counts.get("DUPLICATE", 0),
                         "updated_records": counts.get("UPDATED", 0),
                         "invalid_processing_records": counts.get("INVALID", 0),
                         "failed_processing_records": counts.get("FAILED", 0)})
            finalized_reports[name] = copy
        run_errors = []
        for name, report in finalized_reports.items():
            if report.get("status") in {"error", "partial_failure", "partial"} or report.get("errors"):
                run_errors.extend({"source": name, "message": str(error)[:500]}
                                  for error in (report.get("errors") or [report.get("error")]) if error)
        run_errors.extend(persistence_errors)
        event_errors = []
        for record in records:
            for error in record.get("processing_errors", []) if isinstance(record.get("processing_errors"), list) else []:
                details = dict(error) if isinstance(error, dict) else {"message": str(error)}
                event_errors.append({"event_id": record.get("event_id"), **details})
        run_errors.extend(event_errors)
        valid_count = sum(report.get("valid_records", 0) for report in finalized_reports.values())
        invalid_count = sum(report.get("invalid_records", 0) for report in finalized_reports.values())
        received_count = sum(report.get("records_received", 0) for report in finalized_reports.values())
        if not finalized_reports or all(report.get("status") == "not_configured"
                                        for report in finalized_reports.values()):
            run_errors.append({"phase": "source", "message": "no configured source returned operational data"})
        if self.repository is not None and not run_started_persisted:
            run_errors.append({"phase": "database", "message": "monitoring-run start record could not be persisted"})
        source_failed = any(report.get("status") == "error" for report in finalized_reports.values())
        has_failure = bool(run_errors or any(item.get("processing_status") in {"FAILED", "INVALID"}
                                             for item in records))
        status = "failed" if (not records and (source_failed or has_failure or not finalized_reports)) else (
            "partial_failure" if has_failure else "success")
        persistence_status = "not_configured"
        if self.repository is not None:
            try:
                self.repository.save_monitoring_run({
                    "run_id": run_id, "source": "multi_source", "run_mode": "poll",
                    "started_at": started_at, "ended_at": utc_now(), "status": status,
                    "records_received": max(len(raw), received_count), "valid_records": valid_count,
                    "invalid_records": invalid_count,
                    "new_records": persistence_counts.get("NEW", 0),
                    "duplicate_records": persistence_counts.get("DUPLICATE", 0),
                    "updated_records": persistence_counts.get("UPDATED", 0),
                    "errors": run_errors,
                    "latency_seconds": round(perf_counter() - started_clock, 6),
                    "processing_results": {"events_processed": len(records),
                        "alerts_evaluated": len(alerts),
                        "alerts_generated": sum(bool(item.get("alert_record")) for item in alerts),
                        "event_states": dict(state_counts),
                        "persistence_states": dict(persistence_counts),
                        "source_reports": finalized_reports},
                    "configuration_summary": {"phase3_enabled": self.runner.config.run_phase3,
                        "max_events_per_source": self.source_config.max_events_per_source,
                        "phase11_6_continuous_service": True}})
                persistence_status = "ok"
            except Exception as exc:
                persistence_status = "error"
                persistence_errors.append({"error_type": type(exc).__name__, "message": _safe_error_text(exc)})
        return {"monitoring_run_id": run_id, "started_at": started_at, "completed_at": utc_now(),
                "duration_seconds": round(perf_counter() - started_clock, 6), "status": status,
                "records_received": max(len(raw), received_count), "valid_records": valid_count,
                "selected_records": len(records), "source_failures": sum(
                    report.get("status") == "error" for report in finalized_reports.values()),
                "run_start_persisted": run_started_persisted,
                "retrieval_timestamp": next(iter(finalized_reports.values()), {}).get("retrieval_timestamp"),
                "source_reports": finalized_reports,
                "errors": run_errors,
                "events": records, "alerts": alerts, "persistence_status": persistence_status,
                "persistence_errors": persistence_errors,
                "summary": {"events_processed": len(records), "alerts_evaluated": len(alerts),
                            "event_states": dict(state_counts),
                            "database_persistence_states": dict(persistence_counts),
                            "processing_failures": len(event_errors) + len(persistence_errors),
                            "alerts_generated": sum(bool(item.get("alert_record")) for item in alerts)}}


def make_live_monitoring_session(source_config=None, **kwargs):
    return LiveMonitoringSession(source_config, **kwargs)


def main(argv=None):
    """Start one live cycle or the configurable continuous monitoring loop."""
    import argparse
    from dataclasses import replace
    from database import DatabaseRepository
    from monitoring.real_sources import RealSourceConfig
    from monitoring.service import ContinuousMonitoringService, MonitoringServiceConfig

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="run exactly one ingestion/processing/persistence cycle")
    parser.add_argument("--interval", type=float, default=None,
                        help="seconds to wait after each completed cycle (minimum 60; default from DISASTER_POLL_INTERVAL)")
    parser.add_argument("--max-events-per-source", type=int, default=None,
                        help="maximum events selected from each source (default from DISASTER_MAX_EVENTS_PER_SOURCE)")
    parser.add_argument("--retry-count", type=int, default=None,
                        help="retries after the initial request (default from DISASTER_SOURCE_RETRY_COUNT)")
    parser.add_argument("--retry-backoff", type=float, default=None,
                        help="base transient retry delay in seconds (default from DISASTER_SOURCE_RETRY_BACKOFF)")
    parser.add_argument("--database", "--db-path", dest="database", default=None,
                        help="SQLite database path (default DISASTER_DB_PATH)")
    parser.add_argument("--log-level", default=os.environ.get("DISASTER_MONITORING_LOG_LEVEL", "INFO"),
                        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"))
    args = parser.parse_args(argv)
    try:
        defaults = RealSourceConfig.from_env()
        service_defaults = MonitoringServiceConfig.from_env(defaults)
        source_config = replace(defaults,
            polling_interval_seconds=args.interval if args.interval is not None else defaults.polling_interval_seconds,
            max_events_per_source=args.max_events_per_source if args.max_events_per_source is not None else defaults.max_events_per_source)
        service_config = MonitoringServiceConfig(
            interval_seconds=source_config.polling_interval_seconds,
            retry_count=args.retry_count if args.retry_count is not None else service_defaults.retry_count,
            retry_backoff_seconds=args.retry_backoff if args.retry_backoff is not None else service_defaults.retry_backoff_seconds)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    logging.basicConfig(level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    repository = DatabaseRepository(args.database)
    service = ContinuousMonitoringService(source_config=source_config,
        service_config=service_config, repository=repository, run_phase3=True)
    stopping = False
    def request_shutdown(signum, _frame):
        nonlocal stopping
        if not stopping:
            stopping = True
            logging.getLogger("monitoring.service").warning(
                "Shutdown requested by signal %s; finishing the current cycle before exit", signum)
        service.request_stop()
    previous = {}
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is not None:
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, request_shutdown)
    try:
        results = service.run(once=args.once)
        return 1 if results and results[-1].get("status") == "failed" else 0
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _safe_error_text(exc):
    text = str(exc)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(api[_-]?key|token|password|secret)(\s*[:=]\s*)[^\s,;]+",
                  r"\1\2[REDACTED]", text)
    return f"{type(exc).__name__}: {text[:400]}"


if __name__ == "__main__":
    raise SystemExit(main())
