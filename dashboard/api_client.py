"""Small standard-library client for the Phase 11.2 REST API."""

from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_API_RESPONSE_BYTES = 12_000_000


class DashboardAPIError(RuntimeError):
    """A bounded, safe-to-display API transport or response error."""

    def __init__(self, message, *, status=None):
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class DashboardAPIClient:
    base_url: str = "http://127.0.0.1:8770"
    timeout_seconds: float = 3.0

    def __post_init__(self):
        if not 0.1 <= self.timeout_seconds <= 30:
            raise ValueError("API timeout must be within 0.1..30 seconds")
        parts = urlsplit(self.base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("API base URL must be an HTTP(S) origin/path without credentials or query parameters")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/") + "/")

    def get_json(self, path, params=None):
        relative = path.lstrip("/")
        url = urljoin(self.base_url, relative)
        if params:
            query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
            if query:
                url += "?" + query
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "DisasterIntelligenceDashboard/1.0"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_API_RESPONSE_BYTES + 1)
                if len(raw) > MAX_API_RESPONSE_BYTES:
                    raise DashboardAPIError(
                        f"API response exceeded the {MAX_API_RESPONSE_BYTES // 1_000_000} MB dashboard limit",
                        status=response.status)
        except HTTPError as exc:
            raise DashboardAPIError(f"API request returned HTTP {exc.code}", status=exc.code) from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None)
            label = "request timed out" if isinstance(exc, TimeoutError) or "timed out" in str(reason).casefold() else "could not be reached"
            raise DashboardAPIError(f"API {label}") from None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DashboardAPIError("API returned malformed JSON") from None
        if not isinstance(value, dict):
            raise DashboardAPIError("API response must be a JSON object")
        return value
