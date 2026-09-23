"""Conservative normalization of extracted location mentions."""

import re


def _location_values(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        text = value.get("text", value.get("location_text", ""))
        return [text] if isinstance(text, str) else []
    if isinstance(value, (list, tuple)):
        values = []
        for item in value:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, dict):
                text = item.get("text", item.get("location_text", ""))
                if isinstance(text, str):
                    values.append(text)
        return values
    return []


def normalize_location(value):
    """Return original and whitespace-normalized location mentions, deduplicated in order."""
    locations, seen = [], set()
    for original in _location_values(value):
        normalized = re.sub(r"\s+", " ", original).strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        locations.append({
            "original_text": original,
            "normalized_text": normalized,
            "status": "normalized" if original != normalized else "unchanged",
        })
    return locations


def normalize_locations(value):
    """Plural-named alias for callers normalizing a record's location field."""
    return normalize_location(value)
