"""Meta-agent runtime configuration derived from environment variables."""

from __future__ import annotations

import os
import pathlib


def _parse_csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def get_skills_dir(default: pathlib.Path) -> pathlib.Path:
    """Return the skills directory, honoring ``META_AGENT_SKILLS_DIR``."""
    override = os.getenv("META_AGENT_SKILLS_DIR", "").strip()
    if override:
        return pathlib.Path(override).expanduser().resolve()
    return default


def get_plugin_dirs(workdir: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Return the Agent Plugin directories to scan.

    Honors ``NUVEL_PLUGIN_DIRS`` (a comma-separated list of paths). When unset,
    defaults to ``["./plugins"]``. Relative paths are resolved against
    ``workdir`` (the current working directory by default).
    """
    base = workdir or pathlib.Path.cwd()
    raw = os.getenv("NUVEL_PLUGIN_DIRS", "").strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()] or ["./plugins"]
    dirs: list[pathlib.Path] = []
    for part in parts:
        path = pathlib.Path(part).expanduser()
        if not path.is_absolute():
            path = base / path
        dirs.append(path)
    return dirs


def is_skill_enabled(skill_name: str) -> bool:
    """Return True if ``skill_name`` is in the ``META_AGENT_SKILLS`` allowlist.

    Empty or ``*`` means all skills are enabled (default).
    """
    raw = os.getenv("META_AGENT_SKILLS", "*").strip()
    if not raw or raw == "*":
        return True
    return skill_name in _parse_csv(raw)


def is_tool_disabled(tool_name: str) -> bool:
    """Return True if ``tool_name`` is in the ``META_AGENT_DISABLED_TOOLS`` denylist."""
    raw = os.getenv("META_AGENT_DISABLED_TOOLS", "").strip()
    if not raw:
        return False
    return tool_name in _parse_csv(raw)
