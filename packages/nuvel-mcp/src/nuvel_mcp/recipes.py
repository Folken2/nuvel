"""Recipe signals (``nuvel.recipe_signal/1``) and anonymised recipes (``nuvel.recipe/1``).

Stdlib-only. Shapes live in the packaged schemas ``recipe_signal.v1.schema.json``
and ``recipe.v1.schema.json``; validation reuses :mod:`nuvel_mcp.traces`.
Errors are JSON-pointer paths only, never values.

``screen_recipe`` is the server-side PII screen: it flags text fields that look
like they carry customer detail. False positives are acceptable.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Protocol, runtime_checkable

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


# --- Storage -----------------------------------------------------------------

RECIPES_DIRNAME = "_recipes"
_PATH_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


def tenant_key(tenant: str | None) -> str:
    """Opaque directory name for a tenant: ``sha256(tenant or "_local")[:16]``."""
    return hashlib.sha256((tenant or "_local").encode("utf-8")).hexdigest()[:16]


@runtime_checkable
class RecipeStore(Protocol):
    """Where recipe signals and recipes go. ``nuvel-cloud`` plugs in its own."""

    def append_signal(self, signal: dict) -> bool:
        """Store a signal; ``False`` if ``(tenant, signal_id)`` is already stored."""
        ...

    def put_recipe(self, recipe: dict) -> bool:
        """Store one recipe version; ``False`` if that version already exists."""
        ...


class LocalRecipeStore(traces.DayFileIndex):
    """Files under ``{feedback_dir}/_recipes/``.

    Signals: ``signals/{YYYY-MM-DD}.jsonl`` (UTC day of ``at``), one line each,
    deduped on ``(tenant, signal_id)``. Recipes:
    ``recipes/{tenant_key}/{recipe_id}/v{version}.json``, written atomically
    (temp file published with ``os.link``, which fails if the version exists). The store
    does not validate; callers validate and stamp ``tenant`` first.
    """

    def __init__(self, feedback_dir: Path):
        self.feedback_dir = Path(feedback_dir)
        self._init_day_index()

    @staticmethod
    def _key(record: dict) -> tuple[object, object]:
        return (record.get("tenant"), record.get("signal_id"))

    def _record_key(self, record: dict) -> tuple[object, object] | None:
        if isinstance(record.get("signal_id"), str) and isinstance(record.get("tenant"), (str, type(None))):
            return self._key(record)
        return None

    def append_signal(self, signal: dict) -> bool:
        day = traces._parse_started_at(signal["at"]).date().isoformat()
        path = (self.feedback_dir / RECIPES_DIRNAME / "signals" / f"{day}.jsonl").resolve()
        line = json.dumps(signal, ensure_ascii=False, separators=(",", ":")) + "\n"
        key = self._key(signal)
        with self._lock_for(path):
            keys = self._keys_for(path)
            if key in keys:
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            keys.add(key)
            # The offset is not advanced: the next append re-reads this line only.
        return True

    def put_recipe(self, recipe: dict) -> bool:
        recipe_id, version = recipe["recipe_id"], recipe["version"]
        if not (isinstance(recipe_id, str) and _PATH_ID_RE.fullmatch(recipe_id)
                and isinstance(version, int) and not isinstance(version, bool)):
            raise ValueError("recipe_id and version are not storable")
        directory = (self.feedback_dir / RECIPES_DIRNAME / "recipes"
                     / tenant_key(recipe.get("tenant")) / recipe_id).resolve()
        path = directory / f"v{version}.json"
        data = json.dumps(recipe, ensure_ascii=False, indent=2) + "\n"
        with self._lock_for(path):
            directory.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(data)
                # link() is atomic and fails if the version exists, so a
                # re-sent version is stored once even across processes.
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    return False
                return True
            finally:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass


def load_recipe_store(spec: str, feedback_dir: Path) -> RecipeStore:
    """Build the recipe store named by *spec*: ``"local"`` or ``"module:factory"``.

    The factory is called with no arguments. Raises ``ValueError`` otherwise.
    """
    if spec == "local":
        return LocalRecipeStore(feedback_dir)
    module_name, sep, attr = (spec or "").partition(":")
    if not sep or not module_name or not attr:
        raise ValueError("recipe store must be 'local' or 'module:factory'")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(f"cannot import recipe store module {module_name!r}: {exc}") from exc
    factory = getattr(module, attr, None)
    if not callable(factory):
        raise ValueError(f"recipe store factory {spec!r} not found or not callable")
    return factory()
