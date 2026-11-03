"""`nuvel agent create` end to end: every supported framework must scaffold.

Runs the real CLI, so a backend that cannot create an agent fails CI. Also pins
the dispatcher (no kwarg reaches a backend blind) and that the docs name the
same framework set as `--framework`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from nuvel.cli import (
    _SCAFFOLD_OPTIONS,
    SUPPORTED_FRAMEWORKS,
    _add_new_agent_args,
    _cmd_new,
    build_parser,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Package layout + entrypoint, relative to the generated project root.
EXPECTED_OUTPUT = {
    "adk": ("my_agent/__init__.py", "my_agent/agent.py", "run_adk.py"),
    "hermes": ("my_agent/SOUL.md", "my_agent/config.yaml", "my_agent/skills"),
    "claude-agent-sdk": ("my_agent/__init__.py", "my_agent/agent.py", "server.py"),
}
ALL_BACKENDS = (*SUPPORTED_FRAMEWORKS, "buzz", "anthropic-managed-agents")


def _create(tmp_path: Path, framework: str, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "nuvel", "agent", "create", "my-agent",
         "--framework", framework, "--output-dir", str(tmp_path), *flags],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=120,
        env={"PYTHONPATH": str(REPO_ROOT), "PATH": ""},
    )


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


@pytest.mark.parametrize("framework", ["hermes", "claude-agent-sdk"])
def test_adk_only_flag_names_the_flag_and_framework(tmp_path, framework):
    result = _create(tmp_path, framework, "--with-litellm")
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "--with-litellm" in result.stderr
    assert framework in result.stderr
    assert not list(tmp_path.iterdir())


def test_hermes_keeps_its_telegram_overlay(tmp_path):
    result = _create(tmp_path, "hermes", "--with-telegram")
    assert result.returncode == 0, result.stderr
    assert "Channels: telegram" in result.stdout


def _create_args(**overrides) -> argparse.Namespace:
    args = build_parser().parse_args(["agent", "create", "my-agent"])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


@pytest.mark.parametrize("framework", ALL_BACKENDS)
def test_no_backend_raises_typeerror_through_the_dispatcher(tmp_path, framework):
    args = _create_args(framework=framework, output_dir=str(tmp_path))
    assert _cmd_new(args) == 0
    assert (tmp_path / "my-agent").is_dir()


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


def test_every_create_flag_goes_through_the_dispatcher():
    parser = argparse.ArgumentParser()
    _add_new_agent_args(parser)
    dests = {action.dest for action in parser._actions} - {"help", "name", "framework"}
    assert dests == set(_SCAFFOLD_OPTIONS)




def _doc_paths() -> list[Path]:
    docs = [
        path for path in (REPO_ROOT / "docs").rglob("*")
        if path.suffix in {".md", ".tape"} and "superpowers" not in path.parts
    ]
    return [REPO_ROOT / "README.md", REPO_ROOT / "CONTRIBUTING.md", *docs]


def test_docs_only_pass_supported_frameworks():
    wrong = {}
    for path in _doc_paths():
        names = re.findall(r"--framework[ =]([a-z][a-z-]*)", path.read_text(encoding="utf-8"))
        bad = set(names) - set(SUPPORTED_FRAMEWORKS)
        if bad:
            wrong[str(path.relative_to(REPO_ROOT))] = bad
    assert not wrong


@pytest.mark.parametrize(
    "doc", ["README.md", "docs/reference/cli.md", "docs/getting-started/concepts.md"]
)
def test_docs_list_every_supported_framework(doc):
    text = (REPO_ROOT / doc).read_text(encoding="utf-8")
    for framework in SUPPORTED_FRAMEWORKS:
        assert f"`{framework}`" in text or f"--framework {framework}" in text


@pytest.mark.parametrize(
    "doc",
    ["README.md", "docs/index.md", "docs/reference/cli.md",
     "docs/getting-started/concepts.md", "docs/how-to/pick-a-backend.md",
     "mkdocs.yml"],
)
def test_docs_do_not_advertise_frozen_frameworks(doc):
    text = (REPO_ROOT / doc).read_text(encoding="utf-8")
    assert not re.search(r"\bbuzz\b|managed agents", text, re.IGNORECASE)
