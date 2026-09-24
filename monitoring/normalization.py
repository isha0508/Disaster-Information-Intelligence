"""Input event validation, normalization, timestamps, and provenance."""

from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_PARTS = ("password", "secret", "token", "credential", "authorization", "api_key", "apikey")


def sanitize_source_value(value, stringify_unknown=True):
    """Copy JSON-compatible source metadata while dropping credential-like keys."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            name = str(key)
            if any(part in name.casefold() for part in _SENSITIVE_PARTS):
                continue
            if "url" in name.casefold() and isinstance(item, str):
                cleaned[name] = _sanitize_url(item)
            else:
                cleaned[name] = sanitize_source_value(item, stringify_unknown)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [sanitize_source_value(item, stringify_unknown) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return normalize_timestamp(value)
    return str(value) if stringify_unknown else value


def _sanitize_url(value):
    try:
        parts = urlsplit(value)
        # Rebuild netloc without user-info, which may contain embedded credentials.
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                 if not any(part in key.casefold() for part in _SENSITIVE_PARTS)]
        return urlunsplit((parts.scheme, host, parts.path, urlencode(query), parts.fragment))
    except ValueError:
        return value


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_timestamp(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_content(value):
    if not isinstance(value, str):
        return None
    # Whitespace is normalized without changing words or punctuation.
    return re.sub(r"\s+", " ", value).strip()


def normalize_event(raw, ingested_at=None):
    if not isinstance(raw, dict):
        raise ValueError("event must be a mapping")
    source_value = raw.get("source")
    if not isinstance(source_value, str) or not source_value.strip():
        raise ValueError("source is required")
    content = raw.get("text", raw.get("content"))
    text = normalize_content(content)
    if not text:
        raise ValueError("non-empty text or content is required")
    source = source_value.strip()
    source_event_id = raw.get("source_event_id")
    if source_event_id is not None:
        source_event_id = str(source_event_id).strip() or None
    metadata = raw.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a mapping")
    metadata = sanitize_source_value(metadata, stringify_unknown=False)
    try:
        json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must contain JSON-safe values") from exc
    original = sanitize_source_value(dict(raw))
    source_url = sanitize_source_value({"source_url": raw.get("source_url", raw.get("url"))}).get("source_url")
    normalized = {
        "source": source, "source_event_id": source_event_id,
        "text": text, "original_text": content,
        "observed_at": normalize_timestamp(raw.get("observed_at")),
        "published_at": normalize_timestamp(raw.get("published_at")),
        "raw_observed_at": raw.get("observed_at"),
        "raw_published_at": raw.get("published_at"),
        "ingested_at": normalize_timestamp(ingested_at) or utc_now(),
        "source_url": source_url,
        "metadata": metadata,
        "provenance": {"source": source, "source_type": raw.get("source_type"),
                       "source_event_id": source_event_id,
                       "source_url": source_url,
                       "observed_at": normalize_timestamp(raw.get("observed_at")),
                       "published_at": normalize_timestamp(raw.get("published_at")),
                       "ingested_at": normalize_timestamp(ingested_at) or utc_now(),
                       "adapter": raw.get("adapter")},
        "raw_event": original,
    }
    normalized["content_fingerprint"] = hashlib.sha256(text.casefold().encode("utf-8")).hexdigest()
    return normalized
