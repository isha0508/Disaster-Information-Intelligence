"""Configuration for the self-contained Phase 10 local dashboard."""

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    api_base_url: str = ""
    api_timeout_seconds: float = 3.0
    repository_root: Path = Path(__file__).resolve().parents[1]
    input_path: Path | None = None

    @classmethod
    def from_env(cls):
        api_port = _port(os.getenv("API_PORT", "8770"))
        base_url = os.getenv("DASHBOARD_API_BASE_URL", f"http://127.0.0.1:{api_port}")
        try:
            timeout = float(os.getenv("DASHBOARD_API_TIMEOUT", "3"))
        except ValueError:
            raise ValueError("DASHBOARD_API_TIMEOUT must be a number") from None
        return cls(host=os.getenv("DASHBOARD_HOST", "127.0.0.1"),
                   port=_port(os.getenv("DASHBOARD_PORT", "8765")),
                   api_base_url=base_url, api_timeout_seconds=timeout)

    def __post_init__(self):
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be within 0..65535")
        if not 0.1 <= self.api_timeout_seconds <= 30:
            raise ValueError("API timeout must be within 0.1..30 seconds")
        if not self.api_base_url:
            port = _port(os.getenv("API_PORT", "8770"))
            object.__setattr__(self, "api_base_url", f"http://127.0.0.1:{port}")


def _port(value):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError("dashboard port must be an integer") from None
    if not 0 <= result <= 65535:
        raise ValueError("dashboard port must be within 0..65535")
    return result
