"""Environment-backed configuration for the dependency-free local API."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class APIConfig:
    host: str = "127.0.0.1"
    port: int = 8770
    log_level: str = "INFO"

    @classmethod
    def from_env(cls):
        return cls(os.getenv("API_HOST", "127.0.0.1"),
                   _port(os.getenv("API_PORT", "8770")),
                   os.getenv("API_LOG_LEVEL", "INFO").upper())


def _port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError("API_PORT must be an integer") from None
    if not 0 <= port <= 65535:
        raise ValueError("API_PORT must be within 0..65535")
    return port
