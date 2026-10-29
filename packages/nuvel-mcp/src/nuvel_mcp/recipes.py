"""Recipe signals (``nuvel.recipe_signal/1``) and shared recipes (``nuvel.recipe/2``).

Stdlib-only. Shapes live in the packaged schemas ``recipe_signal.v1.schema.json``
and ``recipe.v2.schema.json``; validation reuses :mod:`nuvel_mcp.traces`.
Errors are JSON-pointer paths only, never values.

A recipe's free text is not screened here: ``share_recipe`` masks it with
:mod:`nuvel_mcp.masking` (allowlist) and re-checks it with
``masking.invariant_hits`` before anything is stored. ``screen_signal`` keeps
the content-free signal's identifier fields to their closed lists.
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
RECIPE_SCHEMA_ID = "nuvel.recipe/2"
SIGNAL_RESOURCE = "schemas/recipe_signal.v1.schema.json"
RECIPE_RESOURCE = "schemas/recipe.v2.schema.json"


_UNKNOWN_SEGMENT = "*"
# Server-stamped top-level fields: fixed names, so naming them leaks nothing
# and tells the client which field it must not send.
_SERVER_FIELDS = frozenset({"tenant", "masking"})


def _schema_path(path: str, schema: dict) -> str:
    """*path* with every segment that is not a schema property name at its
    position, or an array index, replaced by ``*`` (and everything below it).

    ``traces.validate`` keeps identifier-shaped keys, so an extra key such as
    ``pedro`` or ``acme.sl`` would otherwise be echoed back to the client.
    """
    if not path or path == traces.ROOT_PATH:
        return path
    out: list[str] = []
    node: object = schema
    for segment in path[1:].split("/"):
        if isinstance(node, dict) and node.get("type") == "object" and segment in node.get("properties", {}):
            node = node["properties"][segment]
            out.append(segment)
        elif isinstance(node, dict) and node.get("type") == "array" and segment.isdigit() and segment.isascii():
            node = node.get("items")
            out.append(segment)
        elif node is schema and segment in _SERVER_FIELDS:
            out.append(segment)
            break
        else:
            out.append(_UNKNOWN_SEGMENT)
            break
    return "/" + "/".join(out)


def _validate_known_keys(doc: object, resource: str) -> list[str]:
    schema = traces.load_schema(resource)
    return list(dict.fromkeys(_schema_path(p, schema) for p in traces.validate(doc, resource)))


def validate_signal(doc: object) -> list[str]:
    """Paths of fields failing ``nuvel.recipe_signal/1`` (empty list = valid).
    Keys that are not in the schema are reported as ``*``."""
    return _validate_known_keys(doc, SIGNAL_RESOURCE)


def validate_recipe(doc: object) -> list[str]:
    """Paths of fields failing ``nuvel.recipe/2`` (empty list = valid).
    Keys that are not in the schema are reported as ``*``."""
    return _validate_known_keys(doc, RECIPE_RESOURCE)



# Closed lists. CATEGORIES mirrors plugins/chief-of-staff/references/recipe-categories.md
# (nuvel-plugins); PLUGIN_NAMES and ACTIONS grow with server releases. The
# recipe.v2 schema enums are a copy of these (a test checks they are equal).
CATEGORIES = (
    "client-proposal", "client-onboarding", "client-followup", "meeting-prep",
    "meeting-followup", "sales-pipeline", "partner-relations", "investor-update",
    "board-meeting", "monthly-close", "invoicing", "budget-planning", "hiring",
    "team-management", "reporting", "contract-review", "travel-planning",
    "event-planning", "inbox-triage", "document-drafting", "research",
    "weekly-planning", "other",
)
PLUGIN_NAMES = ("chief-of-staff",)
ACTIONS = (
    "search", "read", "summarise", "extract", "compare", "check", "calculate",
    "list", "draft", "edit", "translate", "create", "update", "file", "propose_slot",
    "schedule", "send", "reply", "forward", "share", "remind", "follow_up", "ask",
    "decide", "wait", "other",
)
TOOL_KINDS = ("email", "calendar", "chat", "docs", "storage", "tasks", "web", "none")

# Typed PII regexes, module-level so ``nuvel_mcp.masking`` reuses them.
EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
URL_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)                       # URL prefix
DOMAIN_RE = re.compile(r"(?<![\w.-])[a-z0-9][a-z0-9-]*\.[a-z]{2,24}\b", re.IGNORECASE)  # bare domain
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9 ]{10,}")                            # IBAN-like
DIGIT_RUN_RE = re.compile(r"\d(?:[\s.-]?\d){5,}")                                 # long digit run
CURRENCY_WORDS = r"(?:eur|euros?|usd|gbp|d[oó]lares?|libras?)\b"
CURRENCY_RE = re.compile(r"[€$£¥]\s?\d|\d[\d.,]*\s?(?:[€$£¥]|" + CURRENCY_WORDS + ")"
                         r"|\b(?:eur|usd|gbp)\s?\d", re.IGNORECASE)
PII_PATTERNS = (EMAIL_RE, URL_RE, DOMAIN_RE, PHONE_RE, IBAN_RE, DIGIT_RUN_RE, CURRENCY_RE)

# Private aliases kept for existing callers.
_EMAIL_RE = EMAIL_RE
_DIGIT_RUN_RE = DIGIT_RUN_RE
_CURRENCY_WORDS = CURRENCY_WORDS
_PII_PATTERNS = PII_PATTERNS


def _id_matches(text: str) -> bool:
    """Identifier-shaped fields (signal category): emails and digit runs only."""
    text = unicodedata.normalize("NFKC", text)
    return _EMAIL_RE.search(text) is not None or _DIGIT_RUN_RE.search(text) is not None


def screen_signal(signal: dict) -> list[str]:
    """JSON-pointer paths of signal fields that could carry client text:
    ``/plugin/name`` when not in ``PLUGIN_NAMES``; ``/category`` when not in
    ``CATEGORIES`` or when it looks like an email or a long digit run. Paths
    only; matched text is never returned."""
    if not isinstance(signal, dict):
        return []
    hits = []
    plugin = signal.get("plugin")
    if isinstance(plugin, dict) and "name" in plugin and plugin["name"] not in PLUGIN_NAMES:
        hits.append("/plugin/name")
    category = signal.get("category")
    if isinstance(category, str) and (category not in CATEGORIES or _id_matches(category)):
        hits.append("/category")
    return hits


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
