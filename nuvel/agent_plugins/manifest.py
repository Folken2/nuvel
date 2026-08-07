"""``plugin.json`` loader + validator."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .exceptions import ManifestError
from .schema import SUPPORTED_SCHEMA_ID, validate_manifest


@dataclass
class PluginManifest:
    """A validated Agent Plugin manifest (``plugin.json``)."""

    name: str
    version: str | None = None
    description: str | None = None
    author: dict | None = None
    homepage: str | None = None
    repository: str | None = None
    license: str | None = None
    keywords: list[str] | None = None
    extensions: dict | None = None
    unknown_fields: list[str] = field(default_factory=list)
    schema_id: str = SUPPORTED_SCHEMA_ID

    @classmethod
    def load(cls, plugin_root: Path) -> "PluginManifest":
        """Read, parse and validate ``plugin_root / 'plugin.json'``.

        Raises :class:`ManifestError` (or its subclasses) on fatal problems.
        Non-fatal issues (unknown fields, non-object ``extensions``) are
        collected into :attr:`unknown_fields`.
        """
        plugin_root = Path(plugin_root)
        manifest_path = plugin_root / "plugin.json"

        if not manifest_path.is_file():
            raise ManifestError(f"plugin.json not found in {plugin_root}")

        try:
            raw = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManifestError(f"could not read plugin.json: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ManifestError(f"plugin.json is not valid JSON: {exc}") from exc

        unknown = validate_manifest(data)

        # extensions is only carried through when it is a valid object;
        # otherwise it was already flagged in ``unknown``.
        extensions = data.get("extensions")
        if not isinstance(extensions, dict):
            extensions = None

        return cls(
            name=data["name"],
            version=data.get("version"),
            description=data.get("description"),
            author=data.get("author"),
            homepage=data.get("homepage"),
            repository=data.get("repository"),
            license=data.get("license"),
            keywords=data.get("keywords"),
            extensions=extensions,
            unknown_fields=unknown,
            schema_id=data["$schema"],
        )


__all__ = ["PluginManifest"]
