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
import os
import re
import threading
from collections import OrderedDict
from datetime import date, datetime, timezone
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Protocol, runtime_checkable

SCHEMA_ID = "nuvel.skill_run/1"
SCHEMA_RESOURCE = "schemas/skill_run.v1.schema.json"

#: ``\A``/``\Z`` (not ``^``/``$``) so even ``.match`` rejects a trailing newline.
IDENTIFIER_RE = re.compile(r"\A[a-z0-9][a-z0-9_.:-]{0,79}\Z")

#: Keywords the validator implements. Anything else in the schema file raises.
VALIDATION_KEYWORDS = frozenset({
    "type", "enum", "const", "pattern", "minimum", "maximum", "maxItems",
    "maxProperties", "required", "properties", "additionalProperties", "items",
    "propertyNames", "format",
})
#: Keywords that carry no validation meaning and are ignored.
ANNOTATION_KEYWORDS = frozenset({"$schema", "title", "description"})
FORMATS = frozenset({"uuid", "date-time"})
TYPES = frozenset({"object", "array", "string", "boolean", "integer"})

_UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$", re.ASCII
)

ROOT_PATH = "/"
_UNSAFE_KEY = "*"


def check_schema_keywords(schema: dict) -> None:
    """Raise ``ValueError`` if *schema* uses a keyword, type or format we don't implement."""

    def walk(node: object, where: str) -> None:
        if not isinstance(node, dict):
            raise ValueError(f"schema node at {where or '/'} is not an object")
        for key, value in node.items():
            if key in ANNOTATION_KEYWORDS:
                continue
            if key not in VALIDATION_KEYWORDS:
                raise ValueError(f"unsupported schema keyword {key!r} at {where or '/'}")
            if key == "type" and not (isinstance(value, str) and value in TYPES):
                raise ValueError(f"unsupported type {value!r} at {where or '/'}")
            if key == "format" and not (isinstance(value, str) and value in FORMATS):
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


def _safe_key(key: object) -> str:
    # JSON keys are strings, but a caller may hand us any dict; never TypeError.
    return key if isinstance(key, str) and IDENTIFIER_RE.fullmatch(key) else _UNSAFE_KEY


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
            # Must also convert to UTC: normalize() does, and an offset can push
            # year 1 or year 9999 out of range (OverflowError).
            _parse_started_at(value)
        except (ValueError, OverflowError):
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
        """Store a normalised trace; ``False`` if it is a duplicate.

        A duplicate is the same ``(tenant, run_id)`` already stored; another
        tenant's identical ``run_id`` is not one.
        """
        ...

    def query(self, plugin: str, skill: str, since: datetime, tenant: str | None) -> list[dict]:
        """Traces for ``plugin.skill`` started at or after *since*.

        ``tenant=None`` returns every tenant's traces.
        """
        ...


class _DayIndex:
    """Dedupe keys already in one day file, and how far into it they were read.

    ``dev``/``inode``/``ctime_ns``/``size`` identify the file as last seen, and
    ``tail`` is the last complete line before ``offset`` (it carries a run id
    that is unique in the file). When the file was touched since (new ctime),
    the offset is trusted only if the file grew -- an append -- and that line
    still ends at the offset; otherwise the index is rebuilt.
    """

    __slots__ = ("dev", "inode", "ctime_ns", "size", "offset", "tail", "keys")

    TAIL_BYTES = 64 * 1024  # a longer last line is compared by its last 64 KiB

    def __init__(self, stat: os.stat_result | None = None):
        self.dev = stat.st_dev if stat else -1
        self.inode = stat.st_ino if stat else -1
        self.ctime_ns = stat.st_ctime_ns if stat else -1
        self.size = stat.st_size if stat else -1
        self.offset = 0
        self.tail = b""
        self.keys: set[tuple[object, object]] = set()

    def still_valid(self, path: Path, stat: os.stat_result) -> bool:
        """True when *path* is the file this index read, unchanged up to ``offset``."""
        if (self.dev, self.inode) != (stat.st_dev, stat.st_ino) or stat.st_size < self.offset:
            return False  # replaced (rename, other device) or truncated
        if stat.st_ctime_ns == self.ctime_ns:
            return True  # untouched since the last read
        # Touched: appended to (fine) or rewritten in place (not). An append
        # grows the file and leaves the line the offset follows in place.
        return (stat.st_size > self.size
                and _bytes_before(path, self.offset, len(self.tail)) == self.tail)


class LocalTraceStore:
    """JSONL files at ``{feedback_dir}/_runs/{plugin}.{skill}/{YYYY-MM-DD}.jsonl``.

    One line per run, dated by the normalised (UTC) ``started_at``. Each line is
    a single ``write()`` in append mode.

    Idempotency: a run is a duplicate when its ``(tenant, run_id)`` is already in
    the same UTC day file (``tenant`` absent counts as its own key). Appends to a
    file are serialised by one of ``LOCK_STRIPES`` process-wide locks (a fixed
    pool, so the registry never grows with the number of files), and the check
    runs against an in-memory index of that file's keys.

    The index stores the byte offset it has read up to and the file's identity
    (device, inode, ctime). On every append it reads only the complete lines
    past that offset, so lines written by another process (or another store
    instance) are picked up before the check. The index is rebuilt from the
    start if the file shrank, was replaced (different device or inode), or was
    touched (new ctime) and the bytes just before the offset changed -- a
    rewrite in place. At most ``MAX_INDEXED_FILES`` day files are indexed
    per store; the least recently used is dropped and rebuilt lazily from disk.
    """

    LOCK_STRIPES = 64
    MAX_INDEXED_FILES = 64

    _lock_pool: tuple[threading.Lock, ...] = tuple(threading.Lock() for _ in range(LOCK_STRIPES))

    def __init__(self, feedback_dir: Path):
        self.feedback_dir = Path(feedback_dir)
        self._index: OrderedDict[Path, _DayIndex] = OrderedDict()
        self._index_guard = threading.Lock()

    def _skill_dir(self, plugin: str, skill: str) -> Path:
        return self.feedback_dir / RUNS_DIRNAME / f"{plugin}.{skill}"

    @classmethod
    def _lock_for(cls, path: Path) -> threading.Lock:
        return cls._lock_pool[hash(str(path)) % len(cls._lock_pool)]

    def _keys_for(self, path: Path) -> set[tuple[object, object]]:
        """Bring *path*'s index up to date with the file. Caller holds its lock."""
        with self._index_guard:
            entry = self._index.pop(path, None)
        try:
            stat = path.stat()
        except FileNotFoundError:
            entry = _DayIndex()
        else:
            if entry is None or not entry.still_valid(path, stat):
                entry = _DayIndex(stat)
            # Taken before the read: a write racing it changes ctime again, so
            # the next call re-checks rather than trusting a stale snapshot.
            entry.ctime_ns, entry.size = stat.st_ctime_ns, stat.st_size
            if stat.st_size > entry.offset:
                records, consumed = _read_complete_lines(path, entry.offset)
                if consumed:
                    entry.offset += consumed
                    entry.tail = _last_line(_bytes_before(path, entry.offset,
                                                          _DayIndex.TAIL_BYTES))
                entry.keys.update(
                    _dedupe_key(r) for r in records
                    if isinstance(r.get("run_id"), str) and isinstance(r.get("tenant"), (str, type(None)))
                )
        with self._index_guard:
            self._index[path] = entry
            while len(self._index) > self.MAX_INDEXED_FILES:
                self._index.popitem(last=False)
        return entry.keys

    def append(self, trace: dict) -> bool:
        plugin, skill = trace["plugin"]["name"], trace["skill"]["name"]
        if not (IDENTIFIER_RE.fullmatch(plugin) and IDENTIFIER_RE.fullmatch(skill)):
            raise ValueError("plugin and skill names must be identifiers")
        day = _parse_started_at(trace["started_at"]).date().isoformat()
        path = (self._skill_dir(plugin, skill) / f"{day}.jsonl").resolve()
        line = json.dumps(trace, ensure_ascii=False, separators=(",", ":")) + "\n"
        key = _dedupe_key(trace)
        with self._lock_for(path):
            keys = self._keys_for(path)
            if key in keys:
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            # The offset is not advanced: the next append re-reads this line
            # along with anything another process wrote meanwhile.
            keys.add(key)
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
                # Dotted names can share a directory ('a.b' + 'c', 'a' + 'b.c').
                if _name(trace, "plugin") != plugin or _name(trace, "skill") != skill:
                    continue
                try:
                    started = _parse_started_at(trace["started_at"])
                except (KeyError, TypeError, ValueError):
                    continue
                if started >= since:
                    results.append(trace)
        return results


def _name(trace: dict, key: str) -> object:
    entry = trace.get(key)
    return entry.get("name") if isinstance(entry, dict) else None


def _dedupe_key(trace: dict) -> tuple[object, object]:
    return (trace.get("tenant"), trace.get("run_id"))


def _bytes_before(path: Path, offset: int, count: int) -> bytes:
    """Up to *count* bytes of *path* ending at byte *offset* (b"" if unreadable)."""
    start = max(0, offset - count)
    try:
        with path.open("rb") as fh:
            fh.seek(start)
            return fh.read(offset - start)
    except FileNotFoundError:
        return b""


def _last_line(data: bytes) -> bytes:
    """The last newline-terminated line of *data* (all of it if only one)."""
    return data[data.rfind(b"\n", 0, len(data) - 1) + 1:]


def _read_complete_lines(path: Path, offset: int) -> tuple[list[dict], int]:
    """JSON objects on the complete lines of *path* after byte *offset*.

    Returns the records and how many bytes were consumed; a trailing line with
    no newline yet (a write still in progress) is left for the next read.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read()
    except FileNotFoundError:
        return [], 0
    end = data.rfind(b"\n") + 1
    records = []
    for raw in data[:end].splitlines():
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return records, end


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
