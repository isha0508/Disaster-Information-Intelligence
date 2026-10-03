"""Deterministic, defensive JSON conversion for persisted evidence fields."""

from datetime import date, datetime
import json
import math
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SECRET_PARTS = ("password", "secret", "token", "credential", "authorization",
                 "api_key", "apikey", "cookie", "private_key")


def sanitize_json(value):
    """Return JSON-safe data while removing credential-like fields recursively."""
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            name = str(key)
            if any(part in name.casefold() for part in _SECRET_PARTS):
                continue
            if "url" in name.casefold() and isinstance(item, str):
                safe[name] = _sanitize_url(item)
            else:
                safe[name] = sanitize_json(item)
        return safe
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    if isinstance(value, set):
        return [sanitize_json(item) for item in sorted(value, key=str)]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return sanitize_json(value.to_dict())
    if hasattr(value, "item"):
        try:
            return sanitize_json(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def to_json_text(value):
    """Serialize stably and reject NaN/Infinity after sanitizing them to null."""
    return json.dumps(sanitize_json(value), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def from_json_text(value, default=None):
    """Decode optional database JSON safely, returning a fresh default on bad data."""
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _sanitize_url(value):
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                 if not any(part in key.casefold() for part in _SECRET_PARTS)]
        return urlunsplit((parts.scheme, host, parts.path, urlencode(query), parts.fragment))
    except ValueError:
        return value
