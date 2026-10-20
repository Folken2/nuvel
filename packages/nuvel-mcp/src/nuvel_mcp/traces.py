"""Skill run traces (``nuvel.skill_run/1``) for the Nuvel Skills MCP server.

Stdlib-only. A trace is one closed, content-free JSON object per skill run; its
shape lives in the packaged JSON Schema ``schemas/skill_run.v1.schema.json``.

``validate_skill_run`` is a small recursive validator driven by that file. It
supports exactly the keywords the file uses and refuses (at load time) any
other, so the schema and the validator cannot silently drift apart.

Privacy rule: validation errors are JSON-pointer paths only (``/steps/2/id``),
never the offending value. Object keys that are not themselves identifier-shaped
are reported as ``*`` so a client cannot smuggle content back out through a key.

Storage: ``normalize`` produces the stored form (``started_at`` truncated to
the minute in UTC, ``tenant`` stamped by the server), and a ``TraceStore``
keeps it. ``LocalTraceStore`` is the default; ``load_trace_store`` resolves a
``--trace-store`` spec (``local`` or ``module:factory``).
"""

from __future__ import annotations

import copy
import importlib
import json
import re
import threading
from datetime import date, datetime, timezone
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Protocol, runtime_checkable

SCHEMA_ID = "nuvel.skill_run/1"
SCHEMA_RESOURCE = "schemas/skill_run.v1.schema.json"

IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,79}$")

#: Keywords the validator implements. Anything else in the schema file raises.
VALIDATION_KEYWORDS = frozenset({
    "type", "enum", "const", "pattern", "minimum", "maximum", "maxItems",
    "maxProperties", "required", "properties", "additionalProperties", "items",
    "propertyNames", "format",
})
#: Keywords that carry no validation meaning and are ignored.
ANNOTATION_KEYWORDS = frozenset({"$schema", "title", "description"})
FORMATS = frozenset({"uuid", "date-time"})

_UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$", re.ASCII
)

ROOT_PATH = "/"
_UNSAFE_KEY = "*"


def check_schema_keywords(schema: dict) -> None:
    """Raise ``ValueError`` if *schema* uses a keyword or format we don't implement."""

    def walk(node: object, where: str) -> None:
        if not isinstance(node, dict):
            raise ValueError(f"schema node at {where or '/'} is not an object")
        for key, value in node.items():
            if key in ANNOTATION_KEYWORDS:
                continue
            if key not in VALIDATION_KEYWORDS:
                raise ValueError(f"unsupported schema keyword {key!r} at {where or '/'}")
            if key == "format" and value not in FORMATS:
                raise ValueError(f"unsupported format {value!r} at {where or '/'}")
            if key == "properties":
                for name, sub in value.items():
                    walk(sub, f"{where}/properties/{name}")
            elif key in ("items", "propertyNames") or (key == "additionalProperties" and value is not False):
                walk(value, f"{where}/{key}")

    walk(schema, "")


@lru_cache(maxsize=1)
def _packaged_schema() -> str:
    return resources.files("nuvel_mcp").joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")


def load_schema() -> dict:
    """Return the packaged ``nuvel.skill_run/1`` JSON Schema (keyword-checked)."""
    schema = json.loads(_packaged_schema())
    check_schema_keywords(schema)
    return schema


@lru_cache(maxsize=1)
def _schema() -> dict:
    return load_schema()


def validate_skill_run(trace: object) -> list[str]:
    """Validate *trace* against ``nuvel.skill_run/1``.

    Returns the JSON-pointer paths of failing fields (``"/"`` for the root);
    an empty list means valid. Values are never included.
    """
    errors: list[str] = []
    _validate(trace, _schema(), "", errors)
    return list(dict.fromkeys(p or ROOT_PATH for p in errors))


def _safe_key(key: str) -> str:
    return key if IDENTIFIER_RE.fullmatch(key) else _UNSAFE_KEY


def _type_ok(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        # bool is an int subclass in Python; JSON true/false are not integers.
        return isinstance(value, int) and not isinstance(value, bool)
    raise ValueError(f"unsupported type {expected!r}")


def _format_ok(value: str, fmt: str) -> bool:
    if fmt == "uuid":
        return _UUID4_RE.fullmatch(value) is not None
    if fmt == "date-time":
        if _DATETIME_RE.fullmatch(value) is None:
            return False
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
        return True
    raise ValueError(f"unsupported format {fmt!r}")


def _validate(value: object, schema: dict, path: str, errors: list[str]) -> None:
    expected = schema.get("type")
    if expected is not None and not _type_ok(value, expected):
        errors.append(path)
        return

    if "const" in schema and value != schema["const"]:
        errors.append(path)
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(path)
        return

    if isinstance(value, str):
        if "pattern" in schema and re.fullmatch(schema["pattern"], value, re.ASCII) is None:
            errors.append(path)
            return
        if "format" in schema and not _format_ok(value, schema["format"]):
            errors.append(path)
            return

    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(path)
            return
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(path)
            return

    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(path)
            return
        if "items" in schema:
            for i, item in enumerate(value):
                _validate(item, schema["items"], f"{path}/{i}", errors)

    if isinstance(value, dict):
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(path)
            return
        for name in schema.get("required", ()):
            if name not in value:
                errors.append(f"{path}/{name}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        names = schema.get("propertyNames")
        for key, sub in value.items():
            child = f"{path}/{_safe_key(key)}"
            if names is not None:
                key_errors: list[str] = []
                _validate(key, names, child, key_errors)
                if key_errors:
                    errors.append(child)
                    continue
            if key in properties:
                _validate(sub, properties[key], child, errors)
            elif additional is False:
                errors.append(child)
            elif isinstance(additional, dict):
                _validate(sub, additional, child, errors)


# --- Normalisation -----------------------------------------------------------


def normalize(trace: dict, tenant: str | None) -> dict:
    """Return a stored-form copy of a *valid* trace.

    ``started_at`` is converted to UTC and truncated to the minute
    (``YYYY-MM-DDTHH:MM:00Z``); ``tenant`` is added only when not ``None``.
    The input is not modified.
    """
    out = copy.deepcopy(trace)
    started = _parse_started_at(out["started_at"])
    out["started_at"] = started.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    if tenant is not None:
        out["tenant"] = tenant
    return out


def _parse_started_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# --- Storage -----------------------------------------------------------------


RUNS_DIRNAME = "_runs"


@runtime_checkable
class TraceStore(Protocol):
    """Where normalised traces go. ``nuvel-cloud`` plugs in its own."""

    def append(self, trace: dict) -> bool:
        """Store a normalised trace; ``False`` if its ``run_id`` is a duplicate."""
        ...

    def query(self, plugin: str, skill: str, since: datetime, tenant: str | None) -> list[dict]:
        """Traces for ``plugin.skill`` started at or after *since*.

        ``tenant=None`` returns every tenant's traces.
        """
        ...


class LocalTraceStore:
    """JSONL files at ``{feedback_dir}/_runs/{plugin}.{skill}/{YYYY-MM-DD}.jsonl``.

    One line per run, dated by the normalised (UTC) ``started_at``. Appends are
    serialised per file by a lock (check-then-write for ``run_id`` idempotency)
    and each line is a single ``write()`` in append mode.
    """

    _locks: dict[Path, threading.Lock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, feedback_dir: Path):
        self.feedback_dir = Path(feedback_dir)

    def _skill_dir(self, plugin: str, skill: str) -> Path:
        return self.feedback_dir / RUNS_DIRNAME / f"{plugin}.{skill}"

    @classmethod
    def _lock_for(cls, path: Path) -> threading.Lock:
        with cls._locks_guard:
            return cls._locks.setdefault(path, threading.Lock())

    def append(self, trace: dict) -> bool:
        plugin, skill = trace["plugin"]["name"], trace["skill"]["name"]
        if not (IDENTIFIER_RE.fullmatch(plugin) and IDENTIFIER_RE.fullmatch(skill)):
            raise ValueError("plugin and skill names must be identifiers")
        day = _parse_started_at(trace["started_at"]).date().isoformat()
        path = (self._skill_dir(plugin, skill) / f"{day}.jsonl").resolve()
        line = json.dumps(trace, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock_for(path):
            if any(t.get("run_id") == trace["run_id"] for t in _read_jsonl(path)):
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        return True

    def query(self, plugin: str, skill: str, since: datetime, tenant: str | None) -> list[dict]:
        if not (
            isinstance(plugin, str) and isinstance(skill, str)
            and IDENTIFIER_RE.fullmatch(plugin) and IDENTIFIER_RE.fullmatch(skill)
        ):
            return []
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        since = since.astimezone(timezone.utc)
        directory = self._skill_dir(plugin, skill)
        if not directory.is_dir():
            return []
        results: list[dict] = []
        for path in sorted(directory.glob("*.jsonl")):
            try:
                day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if day < since.date():
                continue
            for trace in _read_jsonl(path):
                if tenant is not None and trace.get("tenant") != tenant:
                    continue
                try:
                    started = _parse_started_at(trace["started_at"])
                except (KeyError, TypeError, ValueError):
                    continue
                if started >= since:
                    results.append(trace)
        return results


def _read_jsonl(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    records = []
    for raw in text.splitlines():
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def load_trace_store(spec: str, feedback_dir: Path) -> TraceStore:
    """Build the trace store named by *spec*.

    ``"local"`` is :class:`LocalTraceStore` under *feedback_dir*;
    ``"module:factory"`` imports ``module`` and calls ``factory()`` with no
    arguments. Raises ``ValueError`` for anything else.
    """
    if spec == "local":
        return LocalTraceStore(feedback_dir)
    module_name, sep, attr = (spec or "").partition(":")
    if not sep or not module_name or not attr:
        raise ValueError("trace store must be 'local' or 'module:factory'")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(f"cannot import trace store module {module_name!r}: {exc}") from exc
    factory = getattr(module, attr, None)
    if not callable(factory):
        raise ValueError(f"trace store factory {spec!r} not found or not callable")
    return factory()
