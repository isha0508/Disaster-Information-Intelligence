"""Small, explicit settings for local Phase 8 polling."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MonitoringConfig:
    polling_interval_seconds: float = 5.0
    max_events_per_cycle: int = 500
    run_phase3: bool = True

    def __post_init__(self):
        if self.polling_interval_seconds < 0:
            raise ValueError("polling interval cannot be negative")
        if self.max_events_per_cycle < 1:
            raise ValueError("max_events_per_cycle must be positive")
