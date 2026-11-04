"""The docs surface tells the truth about backends.

Frozen capabilities are named only on docs/experimental.md, the framework list
comes from nuvel.cli, and every page agrees with the real scaffolders on which
flags work where.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import tomllib
from pathlib import Path

import pytest

from nuvel.cli import SUPPORTED_FRAMEWORKS, _add_new_agent_args, main

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTAL = ROOT / "docs/experimental.md"

# The only places under docs/ that may name a frozen capability.
EXCLUDED = ("docs/experimental.md", "docs/superpowers/**")
# Not text, so there is nothing to scan. Any other file that fails to decode is
# an error rather than a silent gap in coverage.
BINARY_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".webp", ".ico"}

FROZEN = {
    "acp": re.compile(r"(?<![a-z0-9])acp(?![a-z0-9])|agent\s*client\s*protocol", re.I),
    "buzz": re.compile(r"(?<![a-z0-9])buzz(?![a-z0-9])", re.I),
    "anthropic_managed_agents": re.compile(r"anthropic[\s_-]*managed|managed[\s_-]*agents?\b", re.I),
}
FRAMEWORK_USE = re.compile(r"--framework[\s=]+([a-z][a-z_-]*)")
# Column headings of the flag table on docs/how-to/pick-a-backend.md.
FLAG_TABLE_COLUMNS = {"ADK": "adk", "Claude SDK": "claude-agent-sdk", "Hermes": "hermes"}


def _surface(root: Path = ROOT) -> list[Path]:
    """README, CONTRIBUTING, mkdocs.yml and every text file under docs/ not in EXCLUDED.

    mkdocs.yml is here because its site_description renders on every built page.
    Source, tests, CHANGELOG history, packaging metadata and the agent guides
    (CLAUDE.md, AGENTS.md) are not reader-facing pages, so they are not scanned.
    """
    files = [root / "README.md", root / "CONTRIBUTING.md", root / "mkdocs.yml"]
    for path in sorted((root / "docs").rglob("*")):
        if not path.is_file() or path.suffix in BINARY_SUFFIXES:
            continue
        relative = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(relative, pattern) for pattern in EXCLUDED):
            continue
        files.append(path)
    return files


def _hits(pattern: re.Pattern[str], text: str) -> list[int]:
    """Line numbers of each match, searched over the whole text so a split name still matches."""
    return [text.count("\n", 0, match.start()) + 1 for match in pattern.finditer(text)]


def test_surface_covers_the_pages_that_matter():
    surface = {p.relative_to(ROOT).as_posix() for p in _surface()}
    assert {
        "README.md",
        "CONTRIBUTING.md",
        "mkdocs.yml",
        "docs/index.md",
        "docs/contributing.md",
        "docs/reference/cli.md",
        "docs/reference/repo-layout.md",
        "docs/how-to/pick-a-backend.md",
    } <= surface
    assert "docs/experimental.md" not in surface
    assert not any(s.startswith("docs/superpowers/") for s in surface)


def test_surface_picks_up_new_files_by_default(tmp_path):
    for name in ("README.md", "CONTRIBUTING.md", "mkdocs.yml", "docs/new/page.md", "docs/new/notes.txt",
                 "docs/experimental.md", "docs/superpowers/specs/deep/spec.md"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    assert {p.relative_to(tmp_path).as_posix() for p in _surface(tmp_path)} == {
        "README.md", "CONTRIBUTING.md", "mkdocs.yml", "docs/new/page.md", "docs/new/notes.txt",
    }


@pytest.mark.parametrize(
    ("capability", "text"),
    [("acp", "the agent client\nprotocol"), ("anthropic_managed_agents", "Anthropic\nManaged Agents")],
)
def test_name_split_across_lines_is_caught(capability, text):
    assert _hits(FROZEN[capability], text) == [1]


@pytest.mark.parametrize("capability", FROZEN)
def test_frozen_capability_absent_from_surface(capability):
    bad = []
    for path in _surface():
        text = path.read_text(encoding="utf-8")
        bad += [f"{path.relative_to(ROOT)}:{number}" for number in _hits(FROZEN[capability], text)]
    assert not bad, "\n".join(bad)


def test_package_description_names_only_supported_backends():
    with (ROOT / "pyproject.toml").open("rb") as f:
        description = tomllib.load(f)["project"]["description"]
    assert not [name for name, pattern in FROZEN.items() if pattern.search(description)]
    for backend in ("Google ADK", "Hermes Agent", "Claude Agent SDK"):
        assert backend in description


@pytest.mark.parametrize("capability", FROZEN)
def test_experimental_names_frozen_capability(capability):
    assert FROZEN[capability].search(EXPERIMENTAL.read_text(encoding="utf-8"))


def test_experimental_states_the_freeze_and_supported_set():
    text = EXPERIMENTAL.read_text(encoding="utf-8")
    for framework in SUPPORTED_FRAMEWORKS:
        assert f"`{framework}`" in text or f"--framework {framework}" in text
    for term in ("frozen since", "no new work", "no support", "closed"):
        assert term in text.lower()


def test_docs_only_pass_supported_frameworks():
    wrong = {}
    for path in [*_surface(), EXPERIMENTAL]:
        bad = set(FRAMEWORK_USE.findall(path.read_text(encoding="utf-8"))) - set(SUPPORTED_FRAMEWORKS)
        if bad:
            wrong[str(path.relative_to(ROOT))] = bad
    assert not wrong


def test_framework_use_caught_when_split_across_lines():
    assert FRAMEWORK_USE.findall("--framework\nbuzz") == ["buzz"]


@pytest.mark.parametrize(
    ("doc", "row_start"),
    [("docs/reference/cli.md", "| `--framework`"), ("README.md", "| `nuvel agent create <name> --framework <fw>`")],
)
def test_framework_row_lists_exactly_the_supported_set(doc, row_start):
    text = (ROOT / doc).read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith(row_start))
    description = row.split("|")[-2]
    assert set(re.findall(r"`([a-z][a-z-]*)`", description)) == set(SUPPORTED_FRAMEWORKS)


@pytest.mark.parametrize("doc", ["README.md", "docs/getting-started/concepts.md"])
def test_page_shows_how_to_select_each_framework(doc):
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert set(SUPPORTED_FRAMEWORKS) <= set(FRAMEWORK_USE.findall(text))


def _create_flags() -> dict[str, str]:
    """Every advertised boolean `nuvel agent create` flag, with its help text."""
    parser = argparse.ArgumentParser()
    _add_new_agent_args(parser)
    return {
        action.option_strings[0]: action.help
        for action in parser._actions
        if isinstance(action, argparse._StoreTrueAction) and action.help != argparse.SUPPRESS
    }


@pytest.fixture(scope="module")
def honoured(tmp_path_factory) -> dict[str, set[str]]:
    """What the scaffolders really do: flag -> frameworks that accept it."""
    support: dict[str, set[str]] = {}
    for flag in _create_flags():
        support[flag] = set()
        for framework in SUPPORTED_FRAMEWORKS:
            out = tmp_path_factory.mktemp("scaffold")
            argv = ["agent", "create", "my-agent", "--framework", framework,
                    "--output-dir", str(out), flag]
            if main(argv) == 0:
                support[flag].add(framework)
    return support


def _flag_table_columns(lines: list[str]) -> list[str]:
    """Column headings of the flag table, after its Feature column."""
    header = next(line for line in lines if line.startswith("| Feature |"))
    return [cell.strip() for cell in header.split("|")[2:-1]]


def _marker(text: str) -> set[str]:
    """The frameworks in a leading "(adk only)" / "(adk, hermes)" marker."""
    return set(re.search(r"\(([^)]*)\)", text).group(1).replace(" only", "").split(", "))


def test_flag_support_is_not_vacuous(honoured):
    assert honoured["--with-telegram"] == {"adk", "hermes"}
    assert honoured["--with-slack"] == {"adk"}


def test_help_agrees_with_scaffolders(honoured):
    assert {flag: _marker(text) for flag, text in _create_flags().items()} == honoured


@pytest.mark.parametrize(
    ("doc", "row"),
    [("docs/reference/cli.md", "| `{flag}` | off |"), ("README.md", "| `nuvel agent create <name> {flag}` |")],
)
def test_flag_markers_agree_with_scaffolders(honoured, doc, row):
    lines = (ROOT / doc).read_text(encoding="utf-8").splitlines()
    documented = {}
    for flag in honoured:
        prefix = row.format(flag=flag)
        documented[flag] = _marker(next(line for line in lines if line.startswith(prefix)))
    assert documented == honoured


def test_flag_table_agrees_with_scaffolders(honoured):
    assert set(FLAG_TABLE_COLUMNS.values()) == set(SUPPORTED_FRAMEWORKS)
    lines = (ROOT / "docs/how-to/pick-a-backend.md").read_text(encoding="utf-8").splitlines()
    columns = _flag_table_columns(lines)
    documented = {}
    for line in lines:
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        for flag in re.findall(r"`(--[a-z-]+)`", cells[0]) if cells else ():
            documented[flag] = {
                FLAG_TABLE_COLUMNS[column]
                for column, cell in zip(columns, cells[1:])
                if cell == "\u2705"
            }
    assert documented == honoured


def test_flag_table_skill_counts_match_the_bundled_skills():
    lines = (ROOT / "docs/how-to/pick-a-backend.md").read_text(encoding="utf-8").splitlines()
    row = next(line for line in lines if line.startswith("| Knowledge skills"))
    columns = _flag_table_columns(lines)
    counts = {
        FLAG_TABLE_COLUMNS[column]: int(cell)
        for column, cell in zip(columns, (c.strip() for c in row.split("|")[2:-1]))
    }
    for framework, count in counts.items():
        skills = ROOT / "nuvel/backends" / framework.replace("-", "_") / "skills"
        assert count == len(list(skills.glob("*/SKILL.md"))), framework


def test_skills_are_not_described_as_shared_across_backends():
    bad = []
    pattern = re.compile(r"shared\s+across\s+the\s+backends|works\s+across\s+all\s+three", re.I)
    for path in _surface():
        text = path.read_text(encoding="utf-8")
        bad += [f"{path.relative_to(ROOT)}:{n}" for n in _hits(pattern, text)]
    assert not bad, "\n".join(bad)


def test_install_page_says_hermes_needs_its_own_runtime():
    text = (ROOT / "docs/getting-started/install.md").read_text(encoding="utf-8")
    assert re.search(r"Hermes\s+runtime", text)


def test_pages_marking_a_flag_link_to_the_one_table():
    table = ROOT / "docs/how-to/pick-a-backend.md"
    marker = re.compile(r"\badk[\s-]only\b|\(adk,\s*hermes\)", re.I)
    unlinked = [
        str(path.relative_to(ROOT))
        for path in _surface()
        if path != table
        and marker.search(path.read_text(encoding="utf-8"))
        and "pick-a-backend.md#nuvel-feature-support" not in path.read_text(encoding="utf-8")
    ]
    assert not unlinked


@pytest.mark.parametrize("backend", ["hermes", "claude_agent_sdk"])
def test_env_vars_page_lists_each_backend_var(backend):
    templates = ROOT / "nuvel/backends" / backend / "templates"
    names = set()
    for path in templates.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if path.name == ".env.example":
            names |= set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, re.M))
        names |= set(re.findall(r"getenv\(\s*[\"']([A-Z][A-Z0-9_]+)", text))
    assert names
    page = (ROOT / "docs/reference/env-vars.md").read_text(encoding="utf-8")
    assert not {name for name in names if f"`{name}`" not in page}
