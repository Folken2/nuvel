"""Shared fixtures/helpers for Agent Plugin Registry tests."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


def write_plugin(root: Path, name: str, *, schema: bool = True) -> Path:
    """Create a minimal valid plugin.json at ``root`` and return ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    lines = ["{"]
    if schema:
        lines.append(f'  "$schema": "{SCHEMA_ID}",')
    lines.append(f'  "name": "{name}"')
    lines.append("}")
    (root / "plugin.json").write_text("\n".join(lines), encoding="utf-8")
    return root


def write_skill(plugin_root: Path, skill_name: str, body: str) -> Path:
    skill_dir = plugin_root / "skills" / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = skill_dir / "SKILL.md"
    md.write_text(body, encoding="utf-8")
    return md
