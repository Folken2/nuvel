"""The CLI's advertised surface: real ``--help`` output, parsed from stdout.

Support for v1 is adk, hermes and claude-agent-sdk. ACP, buzz and
anthropic_managed_agents were frozen, then removed (see docs/experimental.md):
they must not be offered, and their modules must not import.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from nuvel.cli import SUPPORTED_FRAMEWORKS

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_FRAMEWORKS = {"adk", "hermes", "claude-agent-sdk"}

_REMOVED = re.compile(
    r"\bacp\b|agent client protocol|\bbuzz\b|anthropic[-_ ]*managed",
    re.IGNORECASE,
)
_SUBCOMMANDS = re.compile(r"^  \{([^}]+)\}$", re.MULTILINE)
_FRAMEWORK_CHOICES = re.compile(r"--framework \{([^}]+)\}")


def _nuvel(*argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "COLUMNS": "200", "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "nuvel", *argv],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT, timeout=120,
    )


def _help(*path: str) -> str:
    result = _nuvel(*path, "--help")
    assert result.returncode == 0, f"nuvel {' '.join(path)} --help: {result.stderr}"
    return result.stdout


@pytest.fixture(scope="module")
def help_pages() -> dict[str, str]:
    """Every help page reachable by following the subcommands help advertises."""
    pages: dict[str, str] = {}
    pending: list[tuple[str, ...]] = [()]
    while pending:
        path = pending.pop()
        text = _help(*path)
        pages[" ".join(path)] = text
        match = _SUBCOMMANDS.search(text)
        if match:
            pending.extend((*path, name) for name in match.group(1).split(","))
    return pages


def test_walk_covers_the_real_command_tree(help_pages):
    for command in ("", "agent create", "new", "skills list", "skills search", "mcp serve", "run"):
        assert command in help_pages


def test_no_removed_name_in_any_help_page(help_pages):
    leaks = {
        command or "<root>": _REMOVED.findall(text)
        for command, text in help_pages.items()
        if _REMOVED.search(text)
    }
    assert not leaks


@pytest.mark.parametrize(
    "text",
    ["acp", "acp serve", "--with-acp", "python -m pkg.acp", "ACP v2", "buzz",
     "anthropic-managed", "anthropic-managed-agents", "anthropic_managed_agents"],
)
def test_removed_pattern_catches_every_spelling(text):
    assert _REMOVED.search(text)


@pytest.mark.parametrize("text", ["space", "workspace", "buzzword", "Claude model"])
def test_removed_pattern_ignores_innocent_words(text):
    assert not _REMOVED.search(text)


@pytest.mark.parametrize(
    "command", ["agent create", "new", "skills list", "skills search"]
)
def test_framework_choices_are_the_supported_set(help_pages, command):
    match = _FRAMEWORK_CHOICES.search(help_pages[command])
    assert match, f"no --framework choices in `nuvel {command} --help`"
    assert set(match.group(1).split(",")) == EXPECTED_FRAMEWORKS


def test_supported_frameworks_constant():
    assert set(SUPPORTED_FRAMEWORKS) == EXPECTED_FRAMEWORKS


@pytest.mark.parametrize(
    "framework",
    ["nope", "buzz", "anthropic-managed-agents", "anthropic_managed_agents"],
)
def test_unsupported_framework_error_lists_only_supported(framework, tmp_path):
    result = _nuvel(
        "agent", "create", "my-agent", "--framework", framework,
        "--output-dir", str(tmp_path),
    )
    assert result.returncode == 2
    choices = re.search(r"invalid choice: .*\(choose from (.+)\)", result.stderr)
    assert choices, result.stderr
    assert set(re.findall(r"[\w-]+", choices.group(1))) == EXPECTED_FRAMEWORKS
    assert not list(tmp_path.iterdir())


def test_unknown_command_error_does_not_list_removed_commands():
    result = _nuvel("no-such-command")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert "agent" in result.stderr
    assert not _REMOVED.search(result.stderr)


@pytest.mark.parametrize("path", [("acp",), ("acp", "serve")])
def test_removed_acp_command_is_unknown(path):
    result = _nuvel(*path)
    assert result.returncode == 2
    assert "invalid choice: 'acp'" in result.stderr


def test_removed_with_acp_flag_is_unknown(tmp_path):
    result = _nuvel(
        "agent", "create", "my-agent", "--with-acp", "--output-dir", str(tmp_path),
    )
    assert result.returncode == 2
    assert "unrecognized arguments: --with-acp" in result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "module",
    ["nuvel.backends.buzz",
     "nuvel.backends.anthropic_managed_agents",
     "nuvel.acp",
     "nuvel.commands.acp_serve"],
)
def test_removed_modules_are_gone(module):
    # Checked on disk, not by importing: an editable install of another checkout
    # can still satisfy the import.
    path = REPO_ROOT.joinpath(*module.split("."))
    assert not path.exists() and not path.with_suffix(".py").exists()


def test_version_flag_prints_the_installed_distribution_version():
    from importlib.metadata import version

    result = _nuvel("--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"nuvel {version('nuvel-cli')}"
