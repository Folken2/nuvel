"""`nuvel agent create` end to end: every supported framework must scaffold.

Runs the real CLI, so a backend that cannot create an agent fails CI. Also pins
the dispatcher: no kwarg reaches a backend blind, and a flag a backend does not
support fails naming the flag and the backend. Frozen backends are only required
to import; see test_cli_surface.py.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from nuvel.cli import (
    SUPPORTED_FRAMEWORKS,
    _add_new_agent_args,
    _cmd_new,
    build_parser,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Package layout + entrypoint, relative to the generated project root. A Hermes
# profile has no Python: the runtime enters through SOUL.md and config.yaml.
EXPECTED_OUTPUT = {
    "adk": ("my_agent/__init__.py", "my_agent/agent.py", "run_adk.py"),
    "hermes": ("my_agent/SOUL.md", "my_agent/config.yaml", "my_agent/skills"),
    "claude-agent-sdk": ("my_agent/__init__.py", "my_agent/agent.py", "server.py"),
}
# Each flag a backend other than ADK refuses, per backend.
REFUSED = {
    "hermes": ("--persona", "--with-composio", "--with-slack", "--with-teams",
               "--workflow", "--with-eval", "--with-litellm", "--with-acp"),
    "claude-agent-sdk": ("--persona", "--with-composio", "--with-slack",
                         "--with-telegram", "--with-teams", "--workflow",
                         "--with-eval", "--with-litellm", "--with-acp"),
}


def _create(tmp_path: Path, framework: str, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "nuvel", "agent", "create", "my-agent",
         "--framework", framework, "--output-dir", str(tmp_path), *flags],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=120,
        env={"PYTHONPATH": str(REPO_ROOT), "PATH": ""},
    )


def test_expected_output_covers_every_supported_framework():
    assert set(EXPECTED_OUTPUT) == set(SUPPORTED_FRAMEWORKS)


@pytest.mark.parametrize("framework", SUPPORTED_FRAMEWORKS)
def test_cli_scaffolds_each_supported_framework(tmp_path, framework):
    result = _create(tmp_path, framework)
    assert result.returncode == 0, result.stderr
    assert "Agent scaffolded at:" in result.stdout
    assert "deprecated" not in result.stderr

    project = tmp_path / "my-agent"
    for relative in EXPECTED_OUTPUT[framework]:
        assert (project / relative).exists(), relative

    for generated in project.rglob("*"):
        assert "{{agent_package}}" not in str(generated)
    for module in project.rglob("*.py"):
        compile(module.read_text(encoding="utf-8"), str(module), "exec")
    for config in project.rglob("config.yaml"):
        assert yaml.safe_load(config.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("framework", "flag"),
    [(framework, flag) for framework, flags in REFUSED.items() for flag in flags],
)
def test_refused_flag_names_the_flag_and_backend(tmp_path, framework, flag):
    result = _create(tmp_path, framework, flag)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "TypeError" not in result.stderr
    assert flag in result.stderr
    assert framework in result.stderr
    assert not list(tmp_path.iterdir())


def test_refusing_with_acp_does_not_offer_a_frozen_framework(tmp_path):
    result = _create(tmp_path, "hermes", "--with-acp")
    assert result.returncode == 1
    assert "buzz" not in result.stderr


def test_hermes_keeps_its_telegram_overlay(tmp_path):
    result = _create(tmp_path, "hermes", "--with-telegram")
    assert result.returncode == 0, result.stderr
    assert "Channels: telegram" in result.stdout
    config = yaml.safe_load(
        (tmp_path / "my-agent/my_agent/config.yaml").read_text(encoding="utf-8")
    )
    assert config["platforms"]["telegram"]["enabled"] is True


def _create_args(**overrides) -> argparse.Namespace:
    args = build_parser().parse_args(["agent", "create", "my-agent"])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_dispatcher_only_passes_what_the_backend_declares(monkeypatch, capsys):
    received = {}

    def minimal_backend(name, output_dir=None):
        received.update(name=name, output_dir=output_dir)
        return {"status": "ok", "path": "x", "files_created": 0}

    monkeypatch.setattr("nuvel.cli._scaffold_agent_for", lambda _: minimal_backend)

    assert _cmd_new(_create_args(output_dir="/out")) == 0
    assert received == {"name": "my-agent", "output_dir": "/out"}

    received.clear()
    assert _cmd_new(_create_args(with_slack=True, system_prompt="hi")) == 1
    assert received == {}
    stderr = capsys.readouterr().err
    assert "--system-prompt" in stderr and "--with-slack" in stderr


def test_new_create_flag_reaches_the_backend_with_no_other_wiring(monkeypatch):
    def add_args_with_new_flag(parser):
        _add_new_agent_args(parser)
        parser.add_argument("--with-new", action="store_true")

    received = {}

    def backend(name, with_new=False):
        received.update(name=name, with_new=with_new)
        return {"status": "ok", "path": "x", "files_created": 0}

    monkeypatch.setattr("nuvel.cli._add_new_agent_args", add_args_with_new_flag)
    monkeypatch.setattr("nuvel.cli._scaffold_agent_for", lambda _: backend)

    assert _cmd_new(_create_args(with_new=True)) == 0
    assert received == {"name": "my-agent", "with_new": True}
