"""Small API boundary helpers; repository records remain the source of truth."""

from datetime import datetime, timezone
from database.serialization import sanitize_json


class APIError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def response(items=None, **metadata):
    value = {**metadata}
    if items is not None:
        value["items"] = sanitize_json(items)
    return sanitize_json(value)


def parse_page(params):
    limit = _integer(params, "limit", 50, 1, 1000)
    offset = _integer(params, "offset", 0, 0, 2**31 - 1)
    return limit, offset


def validate_time(params, name):
    value = _single(params, name)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise APIError(400, "invalid_parameter", f"{name} must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _integer(params, name, default, minimum, maximum):
    value = _single(params, name)
    if value is None:
        return default
    try:
        result = int(value)
    except ValueError:
        raise APIError(400, "invalid_parameter", f"{name} must be an integer") from None
    if not minimum <= result <= maximum:
        raise APIError(400, "invalid_parameter", f"{name} must be between {minimum} and {maximum}")
    return result


def _single(params, name):
    values = params.get(name)
    if not values:
        return None
    if len(values) != 1:
        raise APIError(400, "invalid_parameter", f"{name} may be specified once")
    return values[0]


def parse_bool(params, name):
    value = _single(params, name)
    if value is None:
        return None
    lowered = value.casefold()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    raise APIError(400, "invalid_parameter", f"{name} must be true or false")
