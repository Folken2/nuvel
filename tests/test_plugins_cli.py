"""Integration tests for the `nuvel plugins` CLI and plugin config wiring."""

from __future__ import annotations

import json
from pathlib import Path

from nuvel.cli import main
from nuvel.config import get_plugin_dirs

SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"

SKILL_MD = "---\nname: greeter\ndescription: Greets people warmly.\n---\n\n# Greeter\n"


def _make_plugin(base: Path, name: str = "demo") -> Path:
    """Create a valid plugin with one skill and one MCP server under ``base``."""
    root = base / name
    (root / "skills" / "greeter").mkdir(parents=True)
    (root / "plugin.json").write_text(
        json.dumps({"$schema": SCHEMA_ID, "name": name, "version": "0.1.0",
                    "description": "A demo plugin."}),
        encoding="utf-8",
    )
    (root / "skills" / "greeter" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (root / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": MCP_SCHEMA_ID,
                "mcpServers": {
                    "local-tool": {"type": "stdio", "command": "python", "args": ["-m", "srv"]}
                },
            }
        ),
        encoding="utf-8",
    )
    return root


# ── config ────────────────────────────────────────────────────────────


def test_get_plugin_dirs_default(monkeypatch, tmp_path):
    monkeypatch.delenv("NUVEL_PLUGIN_DIRS", raising=False)
    dirs = get_plugin_dirs(workdir=tmp_path)
    assert dirs == [tmp_path / "plugins"]


def test_get_plugin_dirs_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("NUVEL_PLUGIN_DIRS", f"{tmp_path}/a, ./b")
    dirs = get_plugin_dirs(workdir=tmp_path)
    assert dirs == [tmp_path / "a", tmp_path / "b"]


# ── CLI ───────────────────────────────────────────────────────────────


def test_plugins_list_discovers_skills_and_mcp(tmp_path, monkeypatch, capsys):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _make_plugin(plugins_dir)
    monkeypatch.setenv("NUVEL_PLUGIN_DIRS", str(plugins_dir))

    rc = main(["plugins", "list"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "demo v0.1.0" in out
    assert "greeter" in out
    assert "local-tool (stdio)" in out


def test_plugins_list_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NUVEL_PLUGIN_DIRS", str(tmp_path / "nope"))
    rc = main(["plugins", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No plugins found." in out


def test_plugins_load_single_dir(tmp_path, capsys):
    root = _make_plugin(tmp_path)
    rc = main(["plugins", "load", str(root)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "demo v0.1.0" in out
    assert "greeter" in out


def test_plugins_load_bad_manifest(tmp_path, capsys):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "plugin.json").write_text('{"name": "bad"}', encoding="utf-8")
    rc = main(["plugins", "load", str(bad)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "Error:" in err


def test_plugins_config_shows_dirs(tmp_path, monkeypatch, capsys):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    monkeypatch.setenv("NUVEL_PLUGIN_DIRS", str(plugins_dir))
    rc = main(["plugins", "config"])
    out = capsys.readouterr().out
    assert rc == 0
    assert str(plugins_dir) in out
    assert "exists" in out


# ── bridge ────────────────────────────────────────────────────────────


def test_build_registry_reads_config(tmp_path, monkeypatch):
    from nuvel.plugin_bridge import build_registry

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _make_plugin(plugins_dir)
    monkeypatch.setenv("NUVEL_PLUGIN_DIRS", str(plugins_dir))

    registry = build_registry()
    assert [p.manifest.name for p in registry.get_all_plugins()] == ["demo"]
    assert [s.name for s in registry.get_skills()] == ["greeter"]
    assert "local-tool" in registry.get_mcp_servers()
