"""Integrity tests for the bundled knowledge skills.

These exist because four PRs between v0.2.0 and 643994f shipped subsystems with no
skill coverage. They turn "remember to document the skill" into a failing build.

Three guards, all static checks over `nuvel/backends/*/skills/`:

1. `test_referenced_files_exist` — a SKILL.md must not cite a `references/*.md`
   that isn't on disk.
2. `test_frontmatter_is_valid` — valid YAML frontmatter, `name` matching the
   directory, non-empty `description`.
3. `test_skill_count_matches_expectation` — per-framework skill counts match
   `EXPECTED_SKILL_COUNTS`, so adding a skill forces an explicit update here.

Scope note: this file asserts nothing about the template env surface — there is no
`.env.example` parity check here yet.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKENDS = REPO_ROOT / "nuvel" / "backends"

FRAMEWORK_DIRS = {
    "adk": BACKENDS / "adk" / "skills",
    "claude_agent_sdk": BACKENDS / "claude_agent_sdk" / "skills",
    "anthropic_managed_agents": BACKENDS / "anthropic_managed_agents" / "skills",
}

EXPECTED_SKILL_COUNTS = {"adk": 15, "claude_agent_sdk": 6, "anthropic_managed_agents": 5}

REFERENCE_RE = re.compile(r"references/([a-z0-9][a-z0-9-]*\.md)")


def _skill_dirs(framework: str) -> list[Path]:
    root = FRAMEWORK_DIRS[framework]
    return sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file())


def _all_skill_dirs() -> list[Path]:
    out: list[Path] = []
    for framework in FRAMEWORK_DIRS:
        out.extend(_skill_dirs(framework))
    return out


def _frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{skill_md} has no YAML frontmatter"
    _, _, rest = text.partition("---")
    front, _, _ = rest.partition("---")
    return yaml.safe_load(front) or {}


@pytest.mark.parametrize("skill_dir", _all_skill_dirs(), ids=lambda p: p.name)
def test_referenced_files_exist(skill_dir: Path) -> None:
    """A SKILL.md must not promise a reference file that isn't on disk."""
    cited = set(REFERENCE_RE.findall((skill_dir / "SKILL.md").read_text(encoding="utf-8")))
    missing = sorted(n for n in cited if not (skill_dir / "references" / n).is_file())
    assert not missing, f"{skill_dir.name} cites missing reference files: {missing}"


@pytest.mark.parametrize("skill_dir", _all_skill_dirs(), ids=lambda p: p.name)
def test_frontmatter_is_valid(skill_dir: Path) -> None:
    """Every skill needs a name matching its directory and a non-empty description."""
    meta = _frontmatter(skill_dir / "SKILL.md")
    name = meta.get("name")
    description = str(meta.get("description", "")).strip()
    assert name, f"{skill_dir.name}: frontmatter 'name' is missing or empty"
    assert description, f"{skill_dir.name}: frontmatter 'description' is missing or empty"
    assert name == skill_dir.name, (
        f"{skill_dir.name}: frontmatter name {name!r} does not match directory name"
    )


@pytest.mark.parametrize("framework", sorted(EXPECTED_SKILL_COUNTS))
def test_skill_count_matches_expectation(framework: str) -> None:
    """A new skill must be registered here, so counts in docs cannot silently drift."""
    actual = len(_skill_dirs(framework))
    expected = EXPECTED_SKILL_COUNTS[framework]
    assert actual == expected, (
        f"{framework}: found {actual} skills, expected {expected}. "
        "If this is intentional, update EXPECTED_SKILL_COUNTS and every documented "
        "count (.claude/skills/nuvel/SKILL.md, CLAUDE.md, README.md)."
    )
