"""Frozen capabilities are named only on docs/experimental.md, not on the docs surface."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTAL = ROOT / "docs/experimental.md"
ARCHIVE = ROOT / "docs/superpowers"

FROZEN = {
    "acp": re.compile(r"(?<![a-z0-9])acp(?![a-z0-9])|agent\s*client\s*protocol", re.I),
    "buzz": re.compile(r"(?<![a-z0-9])buzz(?![a-z0-9])", re.I),
    "anthropic_managed_agents": re.compile(r"anthropic[\s_-]*managed|managed[\s_-]*agents?\b", re.I),
}
SUPPORTED = ("adk", "hermes", "claude_agent_sdk")


def _surface() -> list[Path]:
    """README, the docs site's config, and every text page under docs/.

    Source, tests, CHANGELOG history, packaging metadata and the root contributor
    guides are not reader-facing pages, so they are not scanned.
    """
    files = [ROOT / "README.md", ROOT / "mkdocs.yml"]
    for path in sorted((ROOT / "docs").rglob("*")):
        if path.suffix not in {".md", ".tape"} or path == EXPERIMENTAL:
            continue
        if path.is_relative_to(ARCHIVE):
            continue
        files.append(path)
    return files


def test_surface_covers_the_pages_that_matter():
    surface = {p.relative_to(ROOT).as_posix() for p in _surface()}
    assert {
        "README.md",
        "docs/index.md",
        "docs/contributing.md",
        "docs/reference/cli.md",
        "docs/reference/repo-layout.md",
        "docs/how-to/pick-a-backend.md",
    } <= surface
    assert "docs/experimental.md" not in surface
    assert not any(s.startswith("docs/superpowers/") for s in surface)


@pytest.mark.parametrize("capability", FROZEN)
def test_frozen_capability_absent_from_surface(capability):
    bad = []
    for path in _surface():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FROZEN[capability].search(line):
                bad.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:80]}")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("capability", FROZEN)
def test_experimental_names_frozen_capability(capability):
    assert FROZEN[capability].search(EXPERIMENTAL.read_text(encoding="utf-8"))


def test_experimental_states_the_freeze_and_supported_set():
    text = EXPERIMENTAL.read_text(encoding="utf-8")
    for backend in SUPPORTED:
        assert f"`{backend}`" in text
    for term in ("frozen since", "no new work", "no support", "closed"):
        assert term in text.lower()


def test_cli_reference_lists_only_supported_frameworks():
    text = (ROOT / "docs/reference/cli.md").read_text(encoding="utf-8")
    assert "One of `adk`, `hermes`, `claude-agent-sdk`." in text
