"""The CLI's advertised surface: real ``--help`` output, parsed from stdout.

Support for v1 is adk and hermes. ACP, buzz and anthropic_managed_agents are
frozen; claude_agent_sdk stays off the list until `nuvel agent create` can
scaffold it end to end. Frozen code stays importable but must not be offered.
"""

from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from nuvel.cli import SUPPORTED_FRAMEWORKS

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_FRAMEWORKS = {"adk", "hermes"}

_FROZEN = re.compile(
    r"\bacp\b|agent client protocol|\bbuzz\b|anthropic[-_ ]*managed|"
    r"claude[-_ ]*agent[-_ ]*sdk",
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


def test_no_frozen_name_in_any_help_page(help_pages):
    leaks = {
        command or "<root>": _FROZEN.findall(text)
        for command, text in help_pages.items()
        if _FROZEN.search(text)
    }
    assert not leaks


@pytest.mark.parametrize(
    "text",
    ["acp", "acp serve", "--with-acp", "python -m pkg.acp", "ACP v2", "buzz",
     "anthropic-managed", "anthropic-managed-agents", "anthropic_managed_agents",
     "claude-agent-sdk", "claude_agent_sdk"],
)
def test_frozen_pattern_catches_every_spelling(text):
    assert _FROZEN.search(text)


@pytest.mark.parametrize("text", ["space", "workspace", "buzzword", "Claude model"])
def test_frozen_pattern_ignores_innocent_words(text):
    assert not _FROZEN.search(text)


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
    ["nope", "buzz", "anthropic-managed-agents", "anthropic_managed_agents",
     "claude-agent-sdk", "claude_agent_sdk"],
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


def test_unknown_command_error_does_not_list_frozen_commands():
    result = _nuvel("no-such-command")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert "agent" in result.stderr
    assert not _FROZEN.search(result.stderr)


@pytest.mark.parametrize("path", [("acp",), ("acp", "serve")])
def test_frozen_acp_stays_reachable_and_says_it_is_unsupported(path):
    assert "experimental and unsupported" in " ".join(_help(*path).lower().split())


def test_with_acp_flag_warns_it_is_unsupported(tmp_path):
    result = _nuvel(
        "agent", "create", "my-agent", "--with-acp", "--output-dir", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert "--with-acp is experimental and unsupported" in result.stderr


@pytest.mark.parametrize(
    "module",
    ["nuvel.backends.buzz.scaffold",
     "nuvel.backends.anthropic_managed_agents.scaffold",
     "nuvel.backends.claude_agent_sdk.scaffold",
     "nuvel.acp.server",
     "nuvel.commands.acp_serve"],
)
def test_frozen_modules_stay_importable(module):
    importlib.import_module(module)
