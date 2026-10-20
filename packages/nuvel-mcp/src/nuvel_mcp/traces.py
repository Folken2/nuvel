"""Skill run traces (``nuvel.skill_run/1``) for the Nuvel Skills MCP server.

Stdlib-only. A trace is one closed, content-free JSON object per skill run; its
shape lives in the packaged JSON Schema ``schemas/skill_run.v1.schema.json``.

``validate_skill_run`` is a small recursive validator driven by that file. It
supports exactly the keywords the file uses and refuses (at load time) any
other, so the schema and the validator cannot silently drift apart.

Privacy rule: validation errors are JSON-pointer paths only (``/steps/2/id``),
never the offending value. Object keys that are not themselves identifier-shaped
are reported as ``*`` so a client cannot smuggle content back out through a key.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from importlib import resources

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
