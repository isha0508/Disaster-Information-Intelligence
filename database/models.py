"""Small value objects returned by database repositories."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EventUpsertResult:
    event_id: str
    state: str
    version: int


@dataclass(frozen=True)
class MonitoringRunResult:
    run_id: str
