"""
preprocessing/text_cleaner.py
==============================
Reusable text preprocessing for disaster-related tweets (HumAID dataset).

Public API
----------
    clean_text(text: str | None, **kwargs) -> str

Design principles
-----------------
* Conservative: preserve information that may be meaningful for disaster NLP
  (numbers, hashtag words, punctuation-bounded clauses).
* No stemming / lemmatisation — these will be evaluated experimentally later.
* No stop-word removal — short disaster tweets are information-dense; removing
  words like "not", "no", "need" can flip the humanitarian signal.
* Hashtag words are kept as lowercased text (e.g. #FloodAlert → floodalert)
  because they often carry the core topic word.
"""

import re
import unicodedata
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Compiled regex patterns  (module-level → compiled once, reused on every call)
# ─────────────────────────────────────────────────────────────────────────────

# URLs: http/https/ftp and bare www. links
_RE_URL = re.compile(
    r"https?://\S+"           # http:// or https://
    r"|ftp://\S+"             # ftp://
    r"|www\.\S+",             # www. (no scheme)
    re.IGNORECASE,
)

# @mentions — also consume a trailing colon that Twitter appends in "RT @handle: …"
_RE_MENTION = re.compile(r"@\w+:?")

# Hashtags: capture the word part so we can keep it
_RE_HASHTAG = re.compile(r"#(\w+)")

# HTML entities: &amp; &lt; &gt; &quot; &apos; &#NNN; &#xHHH;
_RE_HTML_ENTITY = re.compile(r"&(?:[a-z]+|#\d+|#x[\da-f]+);", re.IGNORECASE)

# One or more whitespace characters (space, tab, newline, etc.)
_RE_WHITESPACE = re.compile(r"\s+")

# Repeated punctuation: 3+ of the same punctuation char → keep 2
# Keeps "!!" but collapses "!!!!!!" → "!!"
_RE_REPEATED_PUNCT = re.compile(r"([!?.,;:\-])\1{2,}")

# Emoji pattern — matches Unicode emoji and variation selectors
# Uses a broad range covering most emoji blocks
_RE_EMOJI = re.compile(
    "["
    "\U0001F600-\U0001F64F"   # emoticons
    "\U0001F300-\U0001F5FF"   # symbols & pictographs
    "\U0001F680-\U0001F6FF"   # transport & map
    "\U0001F1E0-\U0001F1FF"   # flags
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"   # supplemental symbols
    "\U00002500-\U00002BEF"
    "\U00010000-\U0010FFFF"
    "]+",
    flags=re.UNICODE,
)

# RT prefix (retweet marker common in HumAID tweets)
_RE_RT_PREFIX = re.compile(r"^RT\s+", re.IGNORECASE)

# ─────────────────────────────────────────────────────────────────────────────
# Main function
# ─────────────────────────────────────────────────────────────────────────────

def clean_text(
    text,
    *,
    remove_emojis: bool = True,
    remove_rt_prefix: bool = True,
    decode_html_entities: bool = True,
) -> str:
    """
    Clean a single tweet string for downstream NLP classification.

    Parameters
    ----------
    text : str | None
        Raw tweet text.  None, non-string, and whitespace-only inputs are
        handled gracefully and return an empty string "".
    remove_emojis : bool, default True
        Strip Unicode emoji characters.  Set False if you want to preserve
        emoji signal for multimodal/feature experiments.
    remove_rt_prefix : bool, default True
        Remove the leading "RT " retweet marker (adds no semantic content).
    decode_html_entities : bool, default True
        Replace HTML entities (&amp; → &, &lt; → <, etc.) before other steps.

    Returns
    -------
    str
        Cleaned tweet text.  Never raises; returns "" on bad input.
    """
    # ── 0. Null / non-string safety ──────────────────────────────────────────
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return ""
    text = text.strip()
    if not text:
        return ""

    # ── 1. HTML entity decoding ───────────────────────────────────────────────
    # Must happen before Unicode normalisation so that e.g. &amp; becomes &
    # before NFKC normalisation runs.
    if decode_html_entities:
        text = _RE_HTML_ENTITY.sub(_decode_html_entity, text)

    # ── 2. Unicode normalisation (NFKC) ──────────────────────────────────────
    # NFKC: compatibility decomposition then canonical composition.
    # Converts ligatures (ﬁ→fi), full-width chars (Ａ→A), etc.
    # Preserves accented Latin characters (é, ü, ñ …) which sometimes appear
    # in disaster location names.
    text = unicodedata.normalize("NFKC", text)

    # ── 3. RT prefix removal ─────────────────────────────────────────────────
    if remove_rt_prefix:
        text = _RE_RT_PREFIX.sub("", text)

    # ── 4. URL removal ───────────────────────────────────────────────────────
    # URLs carry no humanitarian content and introduce out-of-vocabulary tokens.
    text = _RE_URL.sub("", text)

    # ── 5. @mention removal ──────────────────────────────────────────────────
    # Twitter handles identify users, not content.  Removing them reduces noise
    # without losing topic signal.
    text = _RE_MENTION.sub("", text)

    # ── 6. Hashtag word preservation ─────────────────────────────────────────
    # Keep the word; drop only the '#' character.
    # "#FloodAlert in Karachi" → "floodalert in karachi"  (lowercased later)
    text = _RE_HASHTAG.sub(r"\1", text)

    # ── 7. Emoji removal ─────────────────────────────────────────────────────
    if remove_emojis:
        text = _RE_EMOJI.sub("", text)

    # ── 8. Lowercase ─────────────────────────────────────────────────────────
    text = text.lower()

    # ── 9. Repeated punctuation normalisation ────────────────────────────────
    # "!!!!!!" → "!!" — avoids artificially long token sequences while
    # preserving emphatic intent (one repetition is kept).
    text = _RE_REPEATED_PUNCT.sub(r"\1\1", text)

    # ── 10. Whitespace normalisation ─────────────────────────────────────────
    # Collapse runs of spaces / tabs / newlines to a single space.
    text = _RE_WHITESPACE.sub(" ", text).strip()

    return text


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

_HTML_ENTITY_MAP = {
    "amp":  "&",
    "lt":   "<",
    "gt":   ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
    "ndash": "–",
    "mdash": "—",
}

def _decode_html_entity(match: re.Match) -> str:
    """Replace a single HTML entity match with its decoded character."""
    raw = match.group(0)           # e.g. "&amp;"
    name = match.group(0)[1:-1]    # e.g. "amp"

    if name.startswith("#x") or name.startswith("#X"):
        # Hex numeric entity: &#x1F4A5;
        try:
            return chr(int(name[2:], 16))
        except ValueError:
            return raw
    elif name.startswith("#"):
        # Decimal numeric entity: &#128165;
        try:
            return chr(int(name[1:]))
        except ValueError:
            return raw
    else:
        return _HTML_ENTITY_MAP.get(name.lower(), raw)


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: apply to a pandas Series
# ─────────────────────────────────────────────────────────────────────────────

def clean_series(series, **kwargs):
    """
    Apply clean_text to every element of a pandas Series.

    Parameters
    ----------
    series : pd.Series
    **kwargs : passed to clean_text

    Returns
    -------
    pd.Series of cleaned strings
    """
    return series.apply(lambda t: clean_text(t, **kwargs))


# ─────────────────────────────────────────────────────────────────────────────
# Quick self-test when run directly
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _DEMO_CASES = [
        ("Normal tweet",          "Rescue teams arrived in Karachi after the flood."),
        ("URL",                   "More info at https://reliefweb.int/report/12345 stay safe"),
        ("@mention",              "@RedCross Please send aid to sector 4"),
        ("Hashtag word",          "#FloodAlert in Karachi: 50 families displaced"),
        ("Multiple spaces",       "5000   families   need   shelter"),
        ("Repeated punctuation",  "SOS!!!!! Need water NOW???"),
        ("Numbers",               "At least 235 dead, 800 injured after quake"),
        ("Empty string",          ""),
        ("None",                  None),
        ("Unicode / emoji",       "Prayers for Nepal 🙏🌊 #earthquake2025"),
        ("HTML entity",           "Aid workers &amp; volunteers needed &lt;immediately&gt;"),
        ("RT prefix",             "RT @WHO: Outbreak confirmed in 3 districts. Stay alert."),
        ("Mixed noise",           "RT @AidOrg: #Relief effort https://t.co/xyz @volunteer needed!!!!!!"),
    ]

    print(f"{'Input':<55}  {'Cleaned output'}")
    print("-" * 110)
    for label, raw in _DEMO_CASES:
        result = clean_text(raw)
        raw_display = repr(raw)[:52] if raw is not None else "None"
        print(f"  {label:<22}  {raw_display:<52}  →  {repr(result)}")
