"""Source adapter contract and deterministic synthetic fixture."""

from abc import ABC, abstractmethod


class EventSource(ABC):
    name = "event_source"
    source_type = "external"

    @abstractmethod
    def fetch(self):
        """Return an iterable of raw event mappings."""


class SyntheticEventSource(EventSource):
    """Finite in-memory demo fixture; all content is synthetic."""
    name = "synthetic_demo"
    source_type = "synthetic"

    def __init__(self, cycles=None):
        self.cycles = list(cycles or [
            [{"source": self.name, "source_type": "synthetic", "source_event_id": "demo-1",
              "text": "Synthetic flood report: residents trapped; rescue teams requested.",
              "observed_at": "2026-01-01T10:00:00Z", "metadata": {"demo": True}},
             {"source": self.name, "source_type": "synthetic",
              "text": "Synthetic storm damage reported near demo sector.", "metadata": {"demo": True}}],
            [{"source": self.name, "source_type": "synthetic", "source_event_id": "demo-1",
              "text": "Synthetic flood report: residents trapped; rescue teams requested.",
              "observed_at": "2026-01-01T10:00:00Z", "metadata": {"demo": True}},
             {"source": self.name, "source_type": "synthetic", "source_event_id": "demo-1",
              "text": "UPDATE: Synthetic flood report confirms additional shelter request.",
              "observed_at": "2026-01-01T10:00:00Z", "metadata": {"demo": True}},
             {"source": self.name, "source_type": "synthetic", "text": "Synthetic separate incident; optional metadata omitted."},
             None],
        ])
        self._index = 0

    def fetch(self):
        if self._index >= len(self.cycles):
            return []
        result = self.cycles[self._index]
        self._index += 1
        return list(result)
