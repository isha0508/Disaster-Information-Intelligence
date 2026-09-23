"""
nlp/extractor.py
=================
Phase 4 Hybrid NER / Information Extraction — Reusable Production Module.

Faithfully preserves the logic finalised in:
  notebooks/phase_4/final_evaluation/4-4-and-4-5.ipynb  (Phase 4.4 / 4.5)

Public API
----------
    from nlp.extractor import extract_hybrid, build_structured_record

    result = extract_hybrid("50 people were killed in the flood.")
    # {"text": ..., "entities": [...], "request_resource_links": [...]}

    record = build_structured_record(result)
    # {"text": ..., "location": [], "casualties": [...], "disaster_type": "flood", ...}

Design notes
------------
* The general NER layer requires `transformers` and `dslim/bert-base-NER`.
  If the library or model is unavailable the pipeline is set to None and
  extract_hybrid() falls back to rule/gazetteer extraction only — it does
  NOT crash.  All unit tests that do not need the model are unaffected.
* Behavior is deterministic for the same input (no random sampling).
* MISC is retained internally (from dslim/bert-base-NER) but is not
  promoted into the 11 official project entity types.
"""

from __future__ import annotations

import re
import warnings
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# 1. Schema
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ENTITY_TYPES: list[str] = [
    "LOCATION",
    "CASUALTY",
    "DISPLACED",
    "REQUEST",
    "RESOURCE",
    "RESCUE",
    "DISASTER_TYPE",
    "ORGANIZATION",
    "PERSON",
    "NUMBER",
    "INFRASTRUCTURE",
]

# ─────────────────────────────────────────────────────────────────────────────
# 2. Gazetteers
# ─────────────────────────────────────────────────────────────────────────────

DISASTER_TYPES: dict[str, list[str]] = {
    "flood": [
        "flood", "flooding", "flash flood", "flash flooding",
        "floodwaters", "flood water", "inundation",
    ],
    "earthquake": [
        "earthquake", "quake", "aftershock", "tremor",
    ],
    "fire": [
        "fire", "wildfire", "forest fire", "bushfire", "blaze",
    ],
    "hurricane": [
        "hurricane", "cyclone", "typhoon", "tropical storm",
    ],
    "tornado": [
        "tornado", "twister",
    ],
    "landslide": [
        "landslide", "mudslide", "rockslide",
    ],
    "tsunami": [
        "tsunami",
    ],
    "volcano": [
        "volcanic eruption", "volcano", "eruption",
    ],
    "storm": [
        "storm", "thunderstorm", "windstorm",
    ],
}

# Inverted lookup: surface term (lower) → canonical category
DISASTER_TYPE_CANONICAL: dict[str, str] = {
    term.lower(): canonical
    for canonical, terms in DISASTER_TYPES.items()
    for term in terms
}

RESOURCE_TERMS: list[str] = [
    "water", "drinking water", "food", "meals", "blankets",
    "medicine", "medicines", "medical supplies", "supplies",
    "clothes", "shelter", "tents", "fuel", "baby food",
    "bottled water", "first aid", "first aid kits",
    "blood", "oxygen", "generators",
]

INFRASTRUCTURE_TERMS: list[str] = [
    "bridge", "road", "highway", "street", "building",
    "house", "homes", "hospital", "school", "airport",
    "railway", "railroad", "station", "dam", "power line",
    "power lines", "electricity grid", "pipeline", "port",
    "roadway", "overpass", "underpass",
]

RESCUE_TERMS: list[str] = [
    "rescue", "rescued", "rescuing", "trapped", "stranded",
    "stuck", "evacuate", "evacuated", "evacuation",
    "missing", "search and rescue", "sos", "save us",
]

REQUEST_TERMS: list[str] = [
    "need", "needs", "needed", "require", "requires",
    "required", "request", "requesting", "please send",
    "please provide", "looking for", "asking for",
    "urgently need", "urgent need", "help needed",
    "help us",
]

DISPLACEMENT_TERMS: list[str] = [
    "displaced", "homeless", "evacuated", "evacuation",
    "forced to leave", "left their homes", "lost their homes",
    "without shelter", "shelter needed",
]

# ─────────────────────────────────────────────────────────────────────────────
# 3. Regex patterns  (compiled once at import time)
# ─────────────────────────────────────────────────────────────────────────────

_NUMBER_RE = re.compile(
    r"(?<!\w)(\d{1,6}(?:[,.]\d{3})*(?:\.\d+)?)(?!\w)",
    re.IGNORECASE,
)

_CASUALTY_RE = re.compile(
    r"(?P<number>\d{1,6})\s*"
    r"(?P<descriptor>people|persons|person|residents|victims|"
    r"children|men|women|families|workers)?\s*"
    r"(?:are|were|have\s+been|had\s+been)?\s*"
    r"(?P<status>killed|dead|died|deadly|injured|hurt|missing|"
    r"trapped|rescued|fatalities|casualties|victims)",
    re.IGNORECASE,
)

_CASUALTY_COUNT_RE = re.compile(
    r"(?<!\w)(\d{1,6})\s+"
    r"(fatalities|casualties|deaths|injuries|victims)(?!\w)",
    re.IGNORECASE,
)

_REQUEST_RE = re.compile(
    r"(?P<context>"
    r"(?:urgently\s+)?(?:need|needs|needed|require|requires|required|"
    r"request|requesting|please\s+(?:send|provide)|help\s+(?:needed|us)|"
    r"looking\s+for|asking\s+for)"
    r")\s+"
    r"(?P<item>[^.!?;,\n]{2,80})",
    re.IGNORECASE,
)

_DISPLACED_RE = re.compile(
    r"(?P<text>"
    r"\d{0,6}\s*(?:people|families|residents)?\s*"
    r"(?:are\s+)?(?:displaced|homeless|evacuated)|"
    r"(?:people|families|residents)\s+(?:were|are)\s+"
    r"(?:forced\s+to\s+leave|displaced)|"
    r"(?:lost|have\s+lost)\s+(?:their\s+)?homes"
    r")",
    re.IGNORECASE,
)

# ─────────────────────────────────────────────────────────────────────────────
# 4. Rule confidence constants  (from final Phase 4 implementation)
# ─────────────────────────────────────────────────────────────────────────────

_RULE_HIGH   = 0.92
_RULE_MEDIUM = 0.85
_RULE_LOW    = 0.75

# ─────────────────────────────────────────────────────────────────────────────
# 5. Conflict resolution priority  (from Phase 4.4 / 4.5)
# ─────────────────────────────────────────────────────────────────────────────

_PARENT_PRIORITY: dict[str, int] = {
    "CASUALTY":       100,
    "DISPLACED":       90,
    "REQUEST":         80,
    "RESCUE":          80,
    "DISASTER_TYPE":   70,
    "INFRASTRUCTURE":  60,
    "RESOURCE":        50,
    "LOCATION":        40,
    "ORGANIZATION":    40,
    "PERSON":          40,
    "NUMBER":          10,
    "MISC":             1,
}

# ─────────────────────────────────────────────────────────────────────────────
# 6. Structured incident field map
# ─────────────────────────────────────────────────────────────────────────────

_FIELD_MAP: dict[str, str] = {
    "LOCATION":      "location",
    "CASUALTY":      "casualties",
    "DISPLACED":     "displaced",
    "REQUEST":       "requests",
    "RESOURCE":      "resources",
    "RESCUE":        "rescue",
    "ORGANIZATION":  "organizations",
    "PERSON":        "persons",
    "NUMBER":        "numbers",
    "INFRASTRUCTURE": "infrastructure",
}

# ─────────────────────────────────────────────────────────────────────────────
# 7. General NER pipeline  (lazy-loaded, None when unavailable)
# ─────────────────────────────────────────────────────────────────────────────

_NER_MODEL_NAME = "dslim/bert-base-NER"
_GENERAL_NER_THRESHOLD = 0.55

# Module-level singleton — initialised on first call to _get_ner_pipeline()
_ner_pipeline = None
_ner_load_attempted = False


def _get_ner_pipeline():
    """
    Return the dslim/bert-base-NER pipeline, or None if unavailable.
    The model is loaded once and cached.  Errors are silenced so that
    rule-only extraction still works in environments without transformers.
    """
    global _ner_pipeline, _ner_load_attempted
    if _ner_load_attempted:
        return _ner_pipeline
    _ner_load_attempted = True
    try:
        import torch
        from transformers import pipeline as hf_pipeline
        device = 0 if torch.cuda.is_available() else -1
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _ner_pipeline = hf_pipeline(
                "token-classification",
                model=_NER_MODEL_NAME,
                tokenizer=_NER_MODEL_NAME,
                aggregation_strategy="simple",
                device=device,
            )
    except Exception:  # ImportError, OSError, HTTPError …
        _ner_pipeline = None
    return _ner_pipeline


def reset_ner_pipeline() -> None:
    """Force re-initialisation of the NER pipeline (useful in tests)."""
    global _ner_pipeline, _ner_load_attempted
    _ner_pipeline = None
    _ner_load_attempted = False


# ─────────────────────────────────────────────────────────────────────────────
# 8. Low-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clean_span(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip(" ,.;:!?")


def _add_entity(
    entities: list,
    text: str,
    start: int | None,
    end: int | None,
    label: str,
    score: float = 1.0,
    source: str = "rule",
) -> None:
    text = _clean_span(text)
    if not text or start is None or end is None or end <= start:
        return
    entities.append({
        "text":   text,
        "start":  int(start),
        "end":    int(end),
        "label":  label,
        "score":  float(score),
        "source": source,
    })


def _overlaps(a: dict, b: dict) -> bool:
    return max(a["start"], b["start"]) < min(a["end"], b["end"])


def _normalize_label(label: str) -> str:
    """Map CoNLL-2003 labels to project schema."""
    return {
        "PER":  "PERSON",
        "ORG":  "ORGANIZATION",
        "LOC":  "LOCATION",
        "MISC": "MISC",
    }.get(label, label)


def _normalize_entity_text(text: str, label: str) -> str:
    text = _clean_span(text)
    if label in {"LOCATION", "ORGANIZATION", "PERSON", "INFRASTRUCTURE"}:
        return text.strip()
    return text.lower()


def _is_probable_ner_fragment(span_text: str) -> bool:
    """
    Noise filter added in Phase 4.4 / 4.5 audit.
    Returns True (= should be dropped) for:
      - empty / 1-char spans
      - 2-char spans that are not uppercase alphabetic state/country codes
    """
    s = span_text.strip()
    if len(s) <= 1:
        return True
    if len(s) == 2:
        return not (s.isalpha() and s.isupper())
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 9. Entity-level post-processing
# ─────────────────────────────────────────────────────────────────────────────

def _deduplicate_entities(entities: list[dict]) -> list[dict]:
    """Keep the highest-confidence entity for each (start, end, label, text) key."""
    best: dict = {}
    for e in entities:
        key = (
            e["start"],
            e["end"],
            e["label"],
            _normalize_entity_text(e["text"], e["label"]),
        )
        if key not in best or e["score"] > best[key]["score"]:
            best[key] = e
    return list(best.values())


def _resolve_overlaps(entities: list[dict]) -> list[dict]:
    """
    Keep highest-priority entity when spans overlap.
    NUMBER is allowed to coexist alongside non-NUMBER entities
    (reproduces Phase 4.4 / 4.5 behavior).
    """
    entities = _deduplicate_entities(entities)
    ordered = sorted(
        entities,
        key=lambda e: (
            _PARENT_PRIORITY.get(e["label"], 0),
            e["score"],
            e["end"] - e["start"],
        ),
        reverse=True,
    )
    kept: list[dict] = []
    for candidate in ordered:
        conflicting = False
        for existing in kept:
            if _overlaps(candidate, existing):
                # NUMBER coexists with non-NUMBER (preserves Phase 4.4 behavior)
                if (
                    candidate["label"] == "NUMBER" and existing["label"] != "NUMBER"
                ) or (
                    existing["label"] == "NUMBER" and candidate["label"] != "NUMBER"
                ):
                    continue
                conflicting = True
                break
        if not conflicting:
            kept.append(candidate)
    return sorted(kept, key=lambda e: (e["start"], e["end"]))


# ─────────────────────────────────────────────────────────────────────────────
# 10. Individual extraction functions (public — importable directly)
# ─────────────────────────────────────────────────────────────────────────────

def extract_general_ner(text: str) -> list[dict]:
    """
    Run dslim/bert-base-NER and map CoNLL labels → project schema.
    Returns [] silently if the model is unavailable.
    """
    entities: list[dict] = []
    ner = _get_ner_pipeline()
    if ner is None or not text.strip():
        return entities
    try:
        predictions = ner(text)
    except Exception:
        return entities
    for p in predictions:
        score = float(p.get("score", 0.0))
        raw_label = p.get("entity_group", p.get("entity", ""))
        if score < _GENERAL_NER_THRESHOLD:
            continue
        label = _normalize_label(raw_label)
        if label not in {"LOCATION", "ORGANIZATION", "PERSON", "MISC"}:
            continue
        start = p.get("start")
        end   = p.get("end")
        if start is None or end is None:
            word  = _clean_span(p.get("word", ""))
            start = text.find(word)
            end   = start + len(word) if start >= 0 else None
        if start is not None and start >= 0 and end is not None:
            span_text = text[start:end]
            if _is_probable_ner_fragment(span_text):
                continue
            _add_entity(entities, span_text, start, end, label, score, "general_ner")
    return entities


def extract_disaster_type(text: str) -> list[dict]:
    entities: list[dict] = []
    lower = text.lower()
    for canonical, terms in DISASTER_TYPES.items():
        for term in terms:
            for match in re.finditer(
                rf"(?<!\w){re.escape(term)}(?!\w)", lower, flags=re.IGNORECASE
            ):
                _add_entity(
                    entities, text[match.start():match.end()],
                    match.start(), match.end(),
                    "DISASTER_TYPE", _RULE_HIGH, "disaster_gazetteer",
                )
    return entities


def extract_numbers(text: str) -> list[dict]:
    entities: list[dict] = []
    for match in _NUMBER_RE.finditer(text):
        _add_entity(
            entities, match.group(1),
            match.start(1), match.end(1),
            "NUMBER", _RULE_HIGH, "number_regex",
        )
    return entities


def extract_casualties(text: str) -> list[dict]:
    entities: list[dict] = []
    for match in _CASUALTY_RE.finditer(text):
        _add_entity(
            entities, text[match.start():match.end()],
            match.start(), match.end(),
            "CASUALTY", _RULE_HIGH, "casualty_regex",
        )
    for match in _CASUALTY_COUNT_RE.finditer(text):
        _add_entity(
            entities, match.group(0),
            match.start(), match.end(),
            "CASUALTY", _RULE_HIGH, "casualty_regex",
        )
    return entities


def extract_requests(text: str) -> list[dict]:
    entities: list[dict] = []
    for match in _REQUEST_RE.finditer(text):
        item       = _clean_span(match.group("item"))
        item_start = match.start("item")
        item_end   = match.end("item")
        if len(item) > 80:
            item = item[:80].rstrip()
        _add_entity(
            entities, item,
            item_start, min(item_end, item_start + len(item)),
            "REQUEST", _RULE_MEDIUM, "request_regex",
        )
    return entities


def extract_resources(text: str) -> list[dict]:
    entities: list[dict] = []
    lower = text.lower()
    for term in sorted(RESOURCE_TERMS, key=len, reverse=True):
        for match in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", lower):
            _add_entity(
                entities, text[match.start():match.end()],
                match.start(), match.end(),
                "RESOURCE", _RULE_MEDIUM, "resource_gazetteer",
            )
    return entities


def extract_rescue(text: str) -> list[dict]:
    entities: list[dict] = []
    lower = text.lower()
    for term in sorted(RESCUE_TERMS, key=len, reverse=True):
        for match in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", lower):
            _add_entity(
                entities, text[match.start():match.end()],
                match.start(), match.end(),
                "RESCUE", _RULE_MEDIUM, "rescue_gazetteer",
            )
    return entities


def extract_displaced(text: str) -> list[dict]:
    entities: list[dict] = []
    for match in _DISPLACED_RE.finditer(text):
        _add_entity(
            entities, match.group(0),
            match.start(), match.end(),
            "DISPLACED", _RULE_MEDIUM, "displacement_regex",
        )
    lower = text.lower()
    for term in DISPLACEMENT_TERMS:
        for match in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", lower):
            _add_entity(
                entities, text[match.start():match.end()],
                match.start(), match.end(),
                "DISPLACED", _RULE_LOW, "displacement_gazetteer",
            )
    return entities


def extract_infrastructure(text: str) -> list[dict]:
    entities: list[dict] = []
    lower = text.lower()
    for term in sorted(INFRASTRUCTURE_TERMS, key=len, reverse=True):
        for match in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", lower):
            _add_entity(
                entities, text[match.start():match.end()],
                match.start(), match.end(),
                "INFRASTRUCTURE", _RULE_MEDIUM, "infrastructure_gazetteer",
            )
    return entities


# ─────────────────────────────────────────────────────────────────────────────
# 11. Request ↔ Resource linking  (Phase 4.4 / 4.5 proximity heuristic)
# ─────────────────────────────────────────────────────────────────────────────

def _link_request_to_resources(
    request_entities: list[dict],
    resource_entities: list[dict],
) -> list[dict]:
    links = []
    for req in request_entities:
        nearby = [
            r for r in resource_entities
            if abs(r["start"] - req["start"]) <= 120
        ]
        for r in nearby:
            links.append({
                "request_text":  req["text"],
                "resource_text": r["text"],
                "request_span":  [req["start"], req["end"]],
                "resource_span": [r["start"],   r["end"]],
            })
    return links


# ─────────────────────────────────────────────────────────────────────────────
# 12. Main public API
# ─────────────────────────────────────────────────────────────────────────────

def extract_hybrid(text: Any) -> dict:
    """
    Run the full Phase 4 hybrid extraction pipeline on a single text.

    Parameters
    ----------
    text : str (or coercible to str)
        Raw tweet / disaster text.

    Returns
    -------
    dict with keys:
        text                  : str
        entities              : list of entity dicts
            {text, start, end, label, score, source, normalized_text}
        request_resource_links: list of link dicts
    """
    text = str(text) if text is not None else ""

    all_entities: list[dict] = []
    all_entities.extend(extract_general_ner(text))
    all_entities.extend(extract_disaster_type(text))
    all_entities.extend(extract_numbers(text))
    all_entities.extend(extract_casualties(text))
    all_entities.extend(extract_requests(text))
    all_entities.extend(extract_resources(text))
    all_entities.extend(extract_rescue(text))
    all_entities.extend(extract_displaced(text))
    all_entities.extend(extract_infrastructure(text))

    all_entities = _deduplicate_entities(all_entities)
    all_entities = _resolve_overlaps(all_entities)

    # Refresh text slice + add normalised text after resolution
    for e in all_entities:
        e["text"]            = text[e["start"]:e["end"]]
        e["normalized_text"] = _normalize_entity_text(e["text"], e["label"])

    req_ents = [e for e in all_entities if e["label"] == "REQUEST"]
    res_ents = [e for e in all_entities if e["label"] == "RESOURCE"]
    links    = _link_request_to_resources(req_ents, res_ents)

    return {
        "text":                   text,
        "entities":               all_entities,
        "request_resource_links": links,
    }


def build_structured_record(result: dict) -> dict:
    """
    Convert an extract_hybrid() result into a structured incident record.

    Preserves the exact field schema from the final Phase 4 implementation:
        text, location, casualties, displaced, requests, resources, rescue,
        disaster_type, organizations, persons, numbers, infrastructure,
        entities, request_resource_links

    Parameters
    ----------
    result : dict returned by extract_hybrid()

    Returns
    -------
    dict — structured incident record
    """
    text     = result["text"]
    entities = result["entities"]

    record: dict = {
        "text":                   text,
        "location":               [],
        "casualties":             [],
        "displaced":              [],
        "requests":               [],
        "resources":              [],
        "rescue":                 [],
        "disaster_type":          [],
        "organizations":          [],
        "persons":                [],
        "numbers":                [],
        "infrastructure":         [],
        "entities":               entities,
        "request_resource_links": result["request_resource_links"],
    }

    for entity in entities:
        label = entity["label"]

        if label == "DISASTER_TYPE":
            canonical = DISASTER_TYPE_CANONICAL.get(
                entity["text"].lower(), entity["text"].lower()
            )
            if canonical not in record["disaster_type"]:
                record["disaster_type"].append(canonical)
            continue

        if label not in _FIELD_MAP:
            # MISC and any unexpected labels are silently skipped
            continue

        field = _FIELD_MAP[label]
        if entity["text"] not in record[field]:
            record[field].append(entity["text"])

    # Unwrap single-element disaster_type list to a plain string
    # (replicates Phase 4.4 / 4.5 behavior)
    if len(record["disaster_type"]) == 1:
        record["disaster_type"] = record["disaster_type"][0]

    return record


# ─────────────────────────────────────────────────────────────────────────────
# 13. Convenience: batch processing
# ─────────────────────────────────────────────────────────────────────────────

def extract_batch(texts: list[str]) -> list[dict]:
    """
    Run extract_hybrid on a list of texts.
    Returns a list of extract_hybrid() results in the same order.
    """
    return [extract_hybrid(t) for t in texts]


def build_structured_batch(texts: list[str]) -> list[dict]:
    """
    Full pipeline: extract_hybrid + build_structured_record for each text.
    """
    return [build_structured_record(extract_hybrid(t)) for t in texts]
