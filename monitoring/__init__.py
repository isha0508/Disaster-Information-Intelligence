"""Phase 8 operational monitoring public API."""

from monitoring.config import MonitoringConfig
from monitoring.runner import MonitoringRunner, run_monitoring_cycle
from monitoring.sources import EventSource, SyntheticEventSource
from monitoring.real_sources import (GDACSEventSource, MultiSource, NewsRSSSource,
                                    RealSourceConfig, USGSEarthquakeSource,
                                    configured_sources)
from monitoring.state import EventStateStore
from monitoring.mastodon import MastodonPublicSource

__all__ = ["MonitoringConfig", "MonitoringRunner", "EventSource",
           "SyntheticEventSource", "EventStateStore", "run_monitoring_cycle",
           "RealSourceConfig", "USGSEarthquakeSource", "GDACSEventSource",
           "NewsRSSSource", "MastodonPublicSource", "MultiSource", "configured_sources", "LiveMonitoringSession"]


def __getattr__(name):
    """Load the live session lazily so `python -m monitoring.live` is warning-free."""
    if name == "LiveMonitoringSession":
        from monitoring.live import LiveMonitoringSession
        return LiveMonitoringSession
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
