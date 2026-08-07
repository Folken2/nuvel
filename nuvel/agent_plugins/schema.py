"""Embedded JSON schema handling for ``plugin.json``.

The canonical Agent Plugins v1.0.0 manifest schema is embedded here as a Python
dict so validation is entirely offline (no network fetch, per the spec).
"""

from __future__ import annotations

import re

from .exceptions import ManifestError, SchemaVersionError

#: The only ``$schema`` value accepted by this implementation.
SUPPORTED_SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"

#: The canonical (closed) plugin.json schema, embedded for offline validation.
PLUGIN_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": SUPPORTED_SCHEMA_ID,
    "title": "Agent Plugin Manifest",
    "type": "object",
    "additionalProperties": False,
    "required": ["$schema", "name"],
    "properties": {
        "$schema": {"type": "string", "const": SUPPORTED_SCHEMA_ID},
        "name": {"type": "string", "minLength": 1, "maxLength": 64},
        "version": {"type": "string"},
        "description": {"type": "string"},
        "author": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "url": {"type": "string"},
            },
        },
        "homepage": {"type": "string"},
        "repository": {"type": "string"},
        "license": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "extensions": {"type": "object"},
    },
}

#: Fields the manifest schema explicitly allows.
ALLOWED_FIELDS = frozenset(PLUGIN_SCHEMA["properties"].keys())

#: name: 1-64 chars, lowercase alphanumeric plus hyphens/dots, start/end
#: alphanumeric. ``--`` and ``..`` are rejected separately.
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.\-]*[a-z0-9])?$")


def validate_name(name: object) -> str:
    """Validate a plugin ``name`` and return it, or raise ``ManifestError``."""
    if not isinstance(name, str):
        raise ManifestError("'name' must be a string")
    if not (1 <= len(name) <= 64):
        raise ManifestError("'name' must be between 1 and 64 characters")
    if "--" in name or ".." in name:
        raise ManifestError("'name' must not contain '--' or '..'")
    if not _NAME_RE.match(name):
        raise ManifestError(
            "'name' must be lowercase alphanumeric with hyphens/dots and "
            "start/end with an alphanumeric character"
        )
    return name


def validate_manifest(data: object) -> list[str]:
    """Validate a parsed ``plugin.json`` mapping against the closed schema.

    Returns the list of unknown (non-fatal, reported+ignored) field names.
    Raises :class:`ManifestError` / :class:`SchemaVersionError` on fatal issues.
    """
    if not isinstance(data, dict):
        raise ManifestError("plugin.json must contain a JSON object")

    # --- $schema (required, must match) ---
    schema_id = data.get("$schema")
    if schema_id is None:
        raise SchemaVersionError("plugin.json is missing the required '$schema' field")
    if not isinstance(schema_id, str):
        raise SchemaVersionError("'$schema' must be a string")
    if schema_id != SUPPORTED_SCHEMA_ID:
        raise SchemaVersionError(
            f"unsupported '$schema' value: {schema_id!r}; "
            f"expected {SUPPORTED_SCHEMA_ID!r}"
        )

    # --- name (required) ---
    if "name" not in data:
        raise ManifestError("plugin.json is missing the required 'name' field")
    validate_name(data["name"])

    # --- typed optional fields (type violations are fatal) ---
    _check_type(data, "version", str)
    _check_type(data, "description", str)
    _check_type(data, "homepage", str)
    _check_type(data, "repository", str)
    _check_type(data, "license", str)

    if "author" in data and not isinstance(data["author"], dict):
        raise ManifestError("'author' must be an object")

    if "keywords" in data:
        kw = data["keywords"]
        if not isinstance(kw, list) or not all(isinstance(i, str) for i in kw):
            raise ManifestError("'keywords' must be an array of strings")

    # --- extensions: non-object is non-fatal (report + ignore) ---
    unknown: list[str] = []
    if "extensions" in data and not isinstance(data["extensions"], dict):
        unknown.append("extensions")

    # --- unknown fields: non-fatal (report + ignore) ---
    for key in data:
        if key not in ALLOWED_FIELDS:
            unknown.append(key)

    return unknown


def _check_type(data: dict, key: str, expected: type) -> None:
    if key in data and not isinstance(data[key], expected):
        raise ManifestError(f"'{key}' must be a {expected.__name__}")


__all__ = [
    "SUPPORTED_SCHEMA_ID",
    "PLUGIN_SCHEMA",
    "ALLOWED_FIELDS",
    "validate_name",
    "validate_manifest",
]
