"""Tests for skills/ discovery."""

from __future__ import annotations

import os

import pytest

from nuvel.agent_plugins import discover_skills
from nuvel.agent_plugins.exceptions import ComponentDiscoveryError

from .conftest import FIXTURES, write_plugin, write_skill

VALID_SKILL = "---\nname: greet\ndescription: Say hello.\n---\n\n# Greet\n"


def test_missing_skills_returns_empty(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    assert discover_skills(root) == []


def test_empty_skills_dir_returns_empty(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    (root / "skills").mkdir()
    assert discover_skills(root) == []


def test_one_skill_discovered():
    skills = discover_skills(FIXTURES / "valid-full")
    assert len(skills) == 1
    assert skills[0].name == "greet"
    assert skills[0].skill_md_path.name == "SKILL.md"


def test_skill_without_frontmatter_skipped(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    write_skill(root, "nofm", "# Just a heading, no frontmatter\n")
    assert discover_skills(root) == []


def test_skill_missing_required_fields_skipped(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    write_skill(root, "partial", "---\nname: only-name\n---\n\n# body\n")
    assert discover_skills(root) == []


def test_dir_without_skill_md_skipped(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    (root / "skills" / "empty").mkdir(parents=True)
    write_skill(root, "good", VALID_SKILL)
    names = [s.name for s in discover_skills(root)]
    assert names == ["good"]


def test_nested_subdirectories_not_searched(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    nested = root / "skills" / "outer" / "inner"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")
    # outer itself has no SKILL.md, inner should not be reached
    assert discover_skills(root) == []


def test_skills_as_file_raises(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    (root / "skills").write_text("i am a file", encoding="utf-8")
    with pytest.raises(ComponentDiscoveryError):
        discover_skills(root)


def test_path_escape_caught(tmp_path):
    """A skill directory that is a symlink pointing outside is not discovered."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")

    root = write_plugin(tmp_path / "p", "p")
    (root / "skills").mkdir()
    link = root / "skills" / "escape"
    os.symlink(outside, link, target_is_directory=True)

    skills = discover_skills(root)
    assert all(s.name != "escape" for s in skills)
    assert skills == []
