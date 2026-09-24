"""Environment-backed Phase 7 configuration; mock generation is the default."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "mock"
    model: str = "mock-phase7-v1"
    max_context_chars: int = 12000
    temperature: float = 0.0
    max_output_tokens: int = 700
    grounding_enabled: bool = True
    recommendations_enabled: bool = True
    spatial_context_enabled: bool = True
    api_base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    timeout_seconds: float = 20.0

    def __post_init__(self):
        if self.max_context_chars < 2048:
            raise ValueError("max_context_chars must be at least 2048")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")

    @classmethod
    def from_env(cls):
        """Load optional provider settings without requiring credentials."""
        return cls(
            provider=os.getenv("LLM_PROVIDER", "mock").strip().lower(),
            model=os.getenv("LLM_MODEL", "mock-phase7-v1").strip(),
            max_context_chars=_env_int("LLM_MAX_CONTEXT_CHARS", 12000, minimum=2048),
            temperature=_env_float("LLM_TEMPERATURE", 0.0, minimum=0.0, maximum=2.0),
            max_output_tokens=_env_int("LLM_MAX_OUTPUT_TOKENS", 700, minimum=1),
            grounding_enabled=_env_bool("LLM_GROUNDING_ENABLED", True),
            recommendations_enabled=_env_bool("LLM_RECOMMENDATIONS_ENABLED", True),
            spatial_context_enabled=_env_bool("LLM_SPATIAL_CONTEXT_ENABLED", True),
            api_base_url=os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            api_key=os.getenv("LLM_API_KEY", ""),
            timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 20.0, minimum=0.1),
        )


def _env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name, default, minimum):
    try:
        return max(minimum, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


def _env_float(name, default, minimum, maximum=None):
    try:
        value = max(minimum, float(os.getenv(name, default)))
        return min(value, maximum) if maximum is not None else value
    except (TypeError, ValueError):
        return default
