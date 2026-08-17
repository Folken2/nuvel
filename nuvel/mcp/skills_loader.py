"""Skill discovery and loading for the Nuvel Skills MCP server.

Reads a skills-hub layout: a directory containing ``index.json`` plus
``<theme>/<name>/SKILL.md`` files. Stdlib only — no YAML or other third-party
dependency — so ``nuvel mcp serve`` runs in a bare environment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator


class SkillsError(Exception):
    """Raised when the skills directory or a skill file can't be resolved."""


def resolve_skills_dir(base: str | os.PathLike[str] | None) -> Path:
    """Resolve the directory that contains ``index.json``.

    Accepts either the skills directory itself (has ``index.json``) or a repo
    root that has a ``skills/`` subdirectory with ``index.json``. Defaults to
    the current working directory when ``base`` is falsy.
    """
    base_path = Path(base).expanduser().resolve() if base else Path.cwd()
    if (base_path / "index.json").is_file():
        return base_path
    nested = base_path / "skills"
    if (nested / "index.json").is_file():
        return nested
    raise SkillsError(
        f"No index.json found in {base_path} or {nested}. Point --skills-dir at "
        "a skills hub (a directory with index.json, or a repo root containing "
        "skills/index.json)."
    )


def parse_frontmatter(text: str) -> dict:
    """Minimal YAML-frontmatter parser for simple ``key: value`` pairs.

    Returns a dict of the leading ``---`` fenced block (empty if none). Avoids a
    YAML dependency; nested structures are ignored.
    """
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


class SkillsLoader:
    """Discovers and reads skills from a skills-hub directory."""

    def __init__(self, skills_dir: Path):
        self.skills_dir = Path(skills_dir)

    @classmethod
    def from_base(cls, base: str | os.PathLike[str] | None) -> "SkillsLoader":
        """Build a loader from a base path (skills dir or repo root)."""
        return cls(resolve_skills_dir(base))

    @property
    def index_path(self) -> Path:
        return self.skills_dir / "index.json"

    def load_index(self) -> dict:
        """Load and return the parsed ``index.json``."""
        try:
            with open(self.index_path, encoding="utf-8") as f:
                return json.load(f)
        except OSError as exc:
            raise SkillsError(f"Cannot read {self.index_path}: {exc}")
        except json.JSONDecodeError as exc:
            raise SkillsError(f"Invalid JSON in {self.index_path}: {exc}")

    def iter_entries(self) -> Iterator[tuple[str, dict]]:
        """Yield ``(theme, entry)`` for every skill across every theme."""
        index = self.load_index()
        for theme, entries in index.get("themes", {}).items():
            for entry in entries:
                yield theme, entry

    def find(self, theme: str | None, name: str) -> tuple[str | None, dict | None]:
        """Find a skill by name, optionally constrained to a theme."""
        for t, entry in self.iter_entries():
            if entry.get("name") != name:
                continue
            if theme is not None and t != theme:
                continue
            return t, entry
        return None, None

    def skill_path(self, theme: str, entry: dict) -> Path:
        """Local path to a skill's SKILL.md.

        Honors an explicit ``path`` field on the entry if present, otherwise
        derives ``<theme>/<name>/SKILL.md``. Index ``path`` values are
        repo-root-relative (e.g. ``skills/hr/onboarding/SKILL.md``); since
        ``skills_dir`` already points at ``<repo>/skills``, the leading
        ``skills/`` is stripped to avoid a double prefix.
        """
        path = entry.get("path")
        if path:
            if path.startswith("skills/"):
                path = path[len("skills/"):]
            candidate = self.skills_dir / path
            if candidate.is_dir():
                candidate = candidate / "SKILL.md"
            return candidate
        return self.skills_dir / theme / entry["name"] / "SKILL.md"

    def read_skill(self, theme: str, entry: dict) -> str:
        """Read a skill's SKILL.md content."""
        path = self.skill_path(theme, entry)
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillsError(
                f"SKILL.md not available for {theme}/{entry.get('name')}: {exc}"
            )
