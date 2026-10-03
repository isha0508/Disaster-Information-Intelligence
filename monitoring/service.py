"""Continuous service wrapper for the existing Phase 8–9 live session."""

from dataclasses import dataclass
import logging
import os
import re
import time
from threading import Event, Lock

from monitoring.live import LiveMonitoringSession
from monitoring.real_sources import MultiSource, RealSourceConfig


@dataclass(frozen=True)
class MonitoringServiceConfig:
    """Bounded retry and delay-between-cycles settings for the local service."""

    interval_seconds: float = 360.0
    retry_count: int = 2
    retry_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0

    def __post_init__(self):
        if self.interval_seconds < 60:
            raise ValueError("monitoring interval must be at least 60 seconds")
        if not 0 <= self.retry_count <= 10:
            raise ValueError("retry_count must be within 0..10")
        if not 0 <= self.retry_backoff_seconds <= 60:
            raise ValueError("retry_backoff_seconds must be within 0..60")
        if not 0 <= self.max_backoff_seconds <= 300:
            raise ValueError("max_backoff_seconds must be within 0..300")

    @classmethod
    def from_env(cls, source_config=None):
        source_config = source_config or RealSourceConfig.from_env()
        return cls(interval_seconds=float(os.environ.get(
                    "DISASTER_POLL_INTERVAL", source_config.polling_interval_seconds)),
            retry_count=int(os.environ.get("DISASTER_SOURCE_RETRY_COUNT", "2")),
            retry_backoff_seconds=float(os.environ.get("DISASTER_SOURCE_RETRY_BACKOFF", "1")))


class RetryingEventSource:
    """Retry an existing source adapter on transient errors, preserving its schema."""

    def __init__(self, source, *, retry_count=2, backoff_seconds=1.0,
                 max_backoff_seconds=30.0, sleep=time.sleep, logger=None):
        self.source = source
        self.name = getattr(source, "name", source.__class__.__name__)
        self.retry_count = int(retry_count)
        self.backoff_seconds = float(backoff_seconds)
        self.max_backoff_seconds = float(max_backoff_seconds)
        self.sleep = sleep
        self.logger = logger or logging.getLogger("monitoring.source")

    def fetch_with_report(self):
        attempts, errors = 0, []
        records = []
        report = {"source": self.name, "status": "error", "records_received": 0,
                  "valid_records": 0, "errors": []}
        while True:
            attempts += 1
            try:
                if hasattr(self.source, "fetch_with_report"):
                    records, report = self.source.fetch_with_report()
                else:
                    records = list(self.source.fetch() or [])
                    report = {"source": self.name,
                        "status": "success" if records else "empty",
                        "records_received": len(records), "valid_records": len(records), "errors": []}
            except Exception as exc:
                records = []
                report = {"source": self.name, "status": "error",
                    "records_received": 0, "valid_records": 0,
                    "error": _safe_exception(exc), "errors": [_safe_exception(exc)]}
            if str(report.get("status", "")).casefold() not in {"error", "partial"} or not _is_transient(report):
                break
            if attempts > self.retry_count:
                break
            delay = min(self.backoff_seconds * (2 ** (attempts - 1)), self.max_backoff_seconds)
            message = str(report.get("error") or "; ".join(report.get("errors") or []))
            errors.append({"attempt": attempts, "error": message, "delay_seconds": delay})
            self.logger.warning("%s transient retrieval failure (attempt %s/%s); retrying in %.1fs: %s",
                self.name, attempts, self.retry_count + 1, delay, message)
            self.sleep(delay)
        result = dict(report or {})
        result["retry_attempts"] = errors
        result["attempt_count"] = attempts
        result.setdefault("errors", [])
        if errors and result.get("status") == "error":
            result["errors"] = list(result.get("errors") or [])
        return list(records or []), result

    def fetch(self):
        records, report = self.fetch_with_report()
        if report.get("status") == "error":
            raise RuntimeError(str(report.get("error") or "source retrieval failed"))
        return records


class ContinuousMonitoringService:
    """Serialize complete monitoring cycles and wait between their completion."""

    def __init__(self, session=None, *, source_config=None, service_config=None,
                 repository=None, run_phase3=True, sleep=time.sleep, logger=None):
        self.source_config = source_config or RealSourceConfig.from_env()
        self.config = service_config or MonitoringServiceConfig.from_env(self.source_config)
        self.logger = logger or logging.getLogger("monitoring.service")
        self.sleep = sleep
        self._stop = Event()
        self._cycle_lock = Lock()
        self.session = session or LiveMonitoringSession(
            self.source_config, repository=repository, run_phase3=run_phase3)
        if isinstance(self.session.source, MultiSource):
            self.session.source = MultiSource([
                RetryingEventSource(source, retry_count=self.config.retry_count,
                    backoff_seconds=self.config.retry_backoff_seconds,
                    max_backoff_seconds=self.config.max_backoff_seconds,
                    sleep=self.sleep, logger=self.logger)
                for source in self.session.source.sources])

    def request_stop(self):
        """Request shutdown; a synchronous in-progress cycle is allowed to finish."""
        self._stop.set()

    def run_cycle(self):
        with self._cycle_lock:
            self.logger.info("Starting monitoring cycle")
            repository = getattr(self.session, "repository", None)
            previous_run_ids = None
            if repository is not None:
                try:
                    self._recover_stale_runs(repository)
                    previous_run_ids = {item.get("run_id") for item in repository.list_monitoring_runs(limit=20)}
                except Exception:
                    self.logger.exception("Could not inspect prior monitoring runs before this cycle")
            try:
                result = self.session.run_cycle()
            except Exception as exc:
                self.logger.exception("Monitoring cycle failed outside an isolated source adapter")
                result = self._finalize_unexpected_failure(exc, previous_run_ids)
            run_id = result.get("monitoring_run_id")
            summary = result.get("summary", {})
            self.logger.info("Monitoring cycle finished run_id=%s status=%s received=%s processed=%s alerts=%s persistence=%s",
                run_id, result.get("status", "unknown"), result.get("records_received", 0),
                summary.get("events_processed", 0), summary.get("alerts_generated", 0),
                result.get("persistence_status"))
            for source, report in result.get("source_reports", {}).items():
                self.logger.info("source=%s status=%s received=%s processed=%s new=%s duplicate=%s updated=%s attempts=%s",
                    source, report.get("status"), report.get("records_received", 0),
                    report.get("selected_for_processing", 0), report.get("new_records", 0),
                    report.get("duplicate_records", 0), report.get("updated_records", 0),
                    report.get("attempt_count", 1))
            return result

    def _recover_stale_runs(self, repository):
        """Finalize abandoned runs on the next real service cycle, never in diagnostics."""
        stale_after = float(os.environ.get("DISASTER_STALE_RUN_SECONDS", "1800"))
        now = _utc_now()
        rows = repository.list_monitoring_runs(limit=1000)
        for run in rows:
            if run.get("source") != "multi_source" or run.get("status") != "running":
                continue
            if _age_seconds(run.get("started_at"), now) < stale_after:
                continue
            errors = list(run.get("errors") or [])
            errors.append({"phase": "monitoring_recovery",
                           "message": "Run exceeded the stale-run threshold and was finalized during service recovery"})
            repository.save_monitoring_run({**run, "ended_at": now, "status": "failed",
                "errors": errors, "processing_results": {**(run.get("processing_results") or {}),
                    "recovered_as_stale": True}})
            self.logger.warning("Finalized stale monitoring run run_id=%s", run.get("run_id"))

    def _finalize_unexpected_failure(self, exc, previous_run_ids):
        """Close a persisted running row if an unforeseen stage escapes the pipeline."""
        repository = getattr(self.session, "repository", None)
        message = _safe_exception(exc)
        run_id = None
        if repository is not None:
            try:
                running = next((item for item in repository.list_monitoring_runs(limit=20)
                                if item.get("source") == "multi_source" and
                                item.get("status") == "running" and
                                previous_run_ids is not None and
                                item.get("run_id") not in previous_run_ids), None)
                if running:
                    run_id = running.get("run_id")
                    errors = list(running.get("errors") or [])
                    errors.append({"phase": "monitoring_cycle", "message": message})
                    repository.save_monitoring_run({**running, "ended_at": _utc_now(),
                        "status": "failed", "errors": errors,
                        "processing_results": {**(running.get("processing_results") or {}),
                            "fatal_error": message}})
            except Exception:
                self.logger.exception("Could not finalize the failed monitoring-run row")
        return {"monitoring_run_id": run_id, "status": "failed", "records_received": 0,
                "valid_records": 0, "selected_records": 0, "source_failures": 0,
                "source_reports": {}, "errors": [{"phase": "monitoring_cycle", "message": message}],
                "events": [], "alerts": [], "persistence_status": "error" if repository else "not_configured",
                "summary": {"events_processed": 0, "alerts_evaluated": 0,
                            "processing_failures": 1, "alerts_generated": 0}}

    def run(self, *, once=False, max_cycles=None, wait=None):
        """Run one cycle or continue with a delay after each completed cycle."""
        if max_cycles is not None and max_cycles < 1:
            raise ValueError("max_cycles must be positive")
        self._stop.clear()
        self.logger.info("Monitoring service started mode=%s interval_seconds=%s retry_count=%s",
            "once" if once else "continuous", self.config.interval_seconds, self.config.retry_count)
        results, completed = [], 0
        wait_for_stop = wait or self._stop.wait
        while not self._stop.is_set():
            results.append(self.run_cycle())
            completed += 1
            if once or (max_cycles is not None and completed >= max_cycles) or self._stop.is_set():
                break
            self.logger.info("Sleeping %.1fs after completed monitoring cycle", self.config.interval_seconds)
            wait_for_stop(self.config.interval_seconds)
        self.logger.info("Monitoring service stopped cycles=%s", completed)
        return results


def _is_transient(report):
    text = " ".join([str(report.get("error") or ""),
        *(str(value) for value in (report.get("errors") or []))]).casefold()
    if any(token in text for token in ("access is denied", "permission denied",
            "forbidden by its access permissions", "invalid feed url", "must not embed credentials")):
        return False
    if any(token in text for token in ("timed out", "timeout", "network error", "connectionerror",
                                       "connection error", "connection reset", "connection refused",
                                       "connection aborted", "temporarily unavailable", "temporary failure")):
        return True
    match = re.search(r"http status (\d{3})", text)
    return bool(match and (int(match.group(1)) in {408, 425, 429} or int(match.group(1)) >= 500))


def _safe_exception(exc):
    text = str(exc)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(api[_-]?key|token|password|secret)(\s*[:=]\s*)[^\s,;]+",
                  r"\1\2[REDACTED]", text)
    return f"{type(exc).__name__}: {text[:400]}"


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _age_seconds(started_at, now):
    from datetime import datetime, timezone
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return max(0, (end - start).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return 0
