"""Recipe signals (``nuvel.recipe_signal/1``) and anonymised recipes (``nuvel.recipe/1``).

Stdlib-only. Shapes live in the packaged schemas ``recipe_signal.v1.schema.json``
and ``recipe.v1.schema.json``; validation reuses :mod:`nuvel_mcp.traces`.
Errors are JSON-pointer paths only, never values.

``screen_recipe`` is the server-side PII screen: it flags text fields that look
like they carry customer detail. False positives are acceptable.
"""

from __future__ import annotations

import re

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



_PII_PATTERNS = (
    re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+"),                       # email
    re.compile(r"(?:https?://|www\.)", re.IGNORECASE),             # URL
    re.compile(r"\+?\d[\d\s().-]{7,}\d"),                          # phone
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9 ]{10,}"),                  # IBAN-like
    re.compile(r"\d(?:[\s.-]?\d){5,}"),                            # long digit run
    re.compile(r"[€$£]\s?\d|\d[\d.,]*\s?(?:€|EUR\b|USD\b|\$|£)"),  # currency
)
_BREAK_CHARS = ".,;:!?()[]{}\"'“”«»"


def _capitalised_run(text: str) -> bool:
    """True for two or more consecutive capitalised words, not at the start of the text."""
    run = 0
    for index, token in enumerate(text.split()):
        word = token.strip(_BREAK_CHARS)
        if index > 0 and word.isalpha() and word[0].isupper():
            run += 1
            if run >= 2:
                return True
        else:
            run = 0
        if token[-1] in _BREAK_CHARS:
            run = 0  # punctuation ends a name-like sequence
    return False


def _text_matches(text: str) -> bool:
    return any(p.search(text) for p in _PII_PATTERNS) or _capitalised_run(text)


def _text_fields(recipe: dict):
    """Yield ``(pointer, text)`` for every screened text field, in document order."""
    for key in ("title", "when_to_use"):
        if isinstance(recipe.get(key), str):
            yield f"/{key}", recipe[key]
    steps = recipe.get("steps")
    if isinstance(steps, list):
        for i, step in enumerate(steps):
            if isinstance(step, dict) and isinstance(step.get("text"), str):
                yield f"/steps/{i}/text", step["text"]
    pitfalls = recipe.get("pitfalls")
    if isinstance(pitfalls, list):
        for i, item in enumerate(pitfalls):
            if isinstance(item, str):
                yield f"/pitfalls/{i}", item


def screen_recipe(recipe: dict) -> list[str]:
    """JSON-pointer paths of text fields matching a PII pattern (deduplicated,
    document order). Matched text is never returned."""
    if not isinstance(recipe, dict):
        return []
    return list(dict.fromkeys(path for path, text in _text_fields(recipe) if _text_matches(text)))
