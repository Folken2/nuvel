"""Recipe signals (``nuvel.recipe_signal/1``) and anonymised recipes (``nuvel.recipe/1``).

Stdlib-only. Shapes live in the packaged schemas ``recipe_signal.v1.schema.json``
and ``recipe.v1.schema.json``; validation reuses :mod:`nuvel_mcp.traces`.
Errors are JSON-pointer paths only, never values.

``screen_recipe`` is the server-side PII screen: it flags text fields that look
like they carry customer detail. False positives are acceptable.
"""

from __future__ import annotations

import re
import unicodedata

from . import traces

SIGNAL_SCHEMA_ID = "nuvel.recipe_signal/1"
RECIPE_SCHEMA_ID = "nuvel.recipe/1"
SIGNAL_RESOURCE = "schemas/recipe_signal.v1.schema.json"
RECIPE_RESOURCE = "schemas/recipe.v1.schema.json"


def validate_signal(doc: object) -> list[str]:
    """Paths of fields failing ``nuvel.recipe_signal/1`` (empty list = valid)."""
    return traces.validate(doc, SIGNAL_RESOURCE)


def validate_recipe(doc: object) -> list[str]:
    """Paths of fields failing ``nuvel.recipe/1`` (empty list = valid)."""
    return traces.validate(doc, RECIPE_RESOURCE)



_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_DIGIT_RUN_RE = re.compile(r"\d(?:[\s.-]?\d){5,}")
_CURRENCY_WORDS = r"(?:eur|euros?|usd|gbp|d[oó]lares?|libras?)\b"
_PII_PATTERNS = (
    _EMAIL_RE,
    re.compile(r"(?:https?://|www\.)", re.IGNORECASE),             # URL
    re.compile(r"(?<![\w.-])[a-z0-9][a-z0-9-]*\.[a-z]{2,24}\b", re.IGNORECASE),  # bare domain
    re.compile(r"\+?\d[\d\s().-]{7,}\d"),                          # phone
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9 ]{10,}"),                  # IBAN-like
    _DIGIT_RUN_RE,                                                 # long digit run
    re.compile(r"[€$£¥]\s?\d|\d[\d.,]*\s?(?:[€$£¥]|" + _CURRENCY_WORDS + ")"
               r"|\b(?:eur|usd|gbp)\s?\d", re.IGNORECASE),      # currency
)
_BREAK_CHARS = ".,;:!?()[]{}\"'“”«»"


def _is_capitalised_word(word: str) -> bool:
    """Uppercase first letter; letters joined by ``-``, ``'`` or ``’`` (García-López, O'Brien)."""
    parts = re.split(r"[-'’]", word)
    return bool(word) and word[0].isupper() and all(p.isalpha() for p in parts)


def _capitalised_run(text: str) -> bool:
    """True for two or more consecutive capitalised words, not at the start of the text."""
    run = 0
    for index, token in enumerate(text.split()):
        word = token.strip(_BREAK_CHARS)
        if index > 0 and _is_capitalised_word(word):
            run += 1
            if run >= 2:
                return True
        else:
            run = 0
        if token[-1] in _BREAK_CHARS:
            run = 0  # punctuation ends a name-like sequence
    return False


def _text_matches(text: str) -> bool:
    text = unicodedata.normalize("NFKC", text)  # fullwidth '＠' etc. -> ASCII
    return any(p.search(text) for p in _PII_PATTERNS) or _capitalised_run(text)


def _id_matches(text: str) -> bool:
    """Identifier-shaped fields (step action, signal category): emails and digit runs only."""
    text = unicodedata.normalize("NFKC", text)
    return _EMAIL_RE.search(text) is not None or _DIGIT_RUN_RE.search(text) is not None


def _text_fields(recipe: dict):
    """Yield ``(pointer, text, matcher)`` for every screened field, in document order."""
    for key in ("title", "when_to_use"):
        if isinstance(recipe.get(key), str):
            yield f"/{key}", recipe[key], _text_matches
    steps = recipe.get("steps")
    if isinstance(steps, list):
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            if isinstance(step.get("action"), str):
                yield f"/steps/{i}/action", step["action"], _id_matches
            if isinstance(step.get("text"), str):
                yield f"/steps/{i}/text", step["text"], _text_matches
    pitfalls = recipe.get("pitfalls")
    if isinstance(pitfalls, list):
        for i, item in enumerate(pitfalls):
            if isinstance(item, str):
                yield f"/pitfalls/{i}", item, _text_matches


def screen_recipe(recipe: dict) -> list[str]:
    """JSON-pointer paths of text fields matching a PII pattern (deduplicated,
    document order). Matched text is never returned."""
    if not isinstance(recipe, dict):
        return []
    return list(dict.fromkeys(path for path, text, matches in _text_fields(recipe) if matches(text)))


def screen_signal(signal: dict) -> list[str]:
    """JSON-pointer paths of signal fields (``/category``) that look like an email
    or a long digit run. Paths only; matched text is never returned."""
    if not isinstance(signal, dict):
        return []
    category = signal.get("category")
    return ["/category"] if isinstance(category, str) and _id_matches(category) else []
