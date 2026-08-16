"""Tests for the PluginRegistry."""

from __future__ import annotations

import threading

import pytest

from nuvel.agent_plugins import PluginRegistry
from nuvel.agent_plugins.exceptions import ManifestError

from .conftest import FIXTURES, write_plugin, write_skill

VALID_SKILL = "---\nname: s\ndescription: A skill.\n---\n\n# S\n"


def test_no_dirs_returns_empty():
    reg = PluginRegistry([])
    reg.discover_plugins()
    assert reg.get_all_plugins() == []
    assert reg.get_skills() == []
    assert reg.get_mcp_servers() == {}


def test_nonexistent_dir_is_ignored(tmp_path):
    reg = PluginRegistry([tmp_path / "nope"])
    reg.discover_plugins()
    assert reg.get_all_plugins() == []


def test_one_plugin_loads(tmp_path, monkeypatch):
    base = tmp_path / "plugins"
    base.mkdir()
    # Copy nothing; build our own plugin with a skill + mcp.
    p = write_plugin(base / "alpha", "alpha")
    write_skill(p, "greet", VALID_SKILL)

    reg = PluginRegistry([base])
    reg.discover_plugins()

    plugins = reg.get_all_plugins()
    assert len(plugins) == 1
    info = reg.get_plugin("alpha")
    assert info is not None
    assert info.manifest.name == "alpha"
    assert [s.name for s in info.skills] == ["greet"]
    assert reg.get_skills()[0].name == "greet"


def test_multiple_plugins_load_independently(tmp_path):
    base = tmp_path / "plugins"
    base.mkdir()
    write_skill(write_plugin(base / "a", "a"), "sa", VALID_SKILL)
    write_skill(write_plugin(base / "b", "b"), "sb", VALID_SKILL)

    reg = PluginRegistry([base])
    reg.discover_plugins()

    assert {p.manifest.name for p in reg.get_all_plugins()} == {"a", "b"}
    assert {s.name for s in reg.get_skills()} == {"sa", "sb"}


def test_fatal_manifest_does_not_crash_others(tmp_path):
    base = tmp_path / "plugins"
    base.mkdir()
    write_plugin(base / "good", "good")
    # bad: has plugin.json but missing $schema
    bad = base / "bad"
    bad.mkdir()
    (bad / "plugin.json").write_text('{"name": "bad"}', encoding="utf-8")

    reg = PluginRegistry([base])
    reg.discover_plugins()

    assert {p.manifest.name for p in reg.get_all_plugins()} == {"good"}
    assert any(e.component == "manifest" for e in reg.errors)


def test_load_plugin_raises_on_fatal(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "plugin.json").write_text('{"name": "bad"}', encoding="utf-8")
    reg = PluginRegistry([])
    with pytest.raises(ManifestError):
        reg.load_plugin(bad)
    assert reg.errors and reg.errors[0].component == "manifest"


def test_component_failure_isolation(tmp_path):
    """A broken mcp.json must not prevent skills from loading."""
    reg = PluginRegistry([FIXTURES])
    reg.discover_plugins()
    info = reg.get_plugin("bad-mcp")
    assert info is not None
    # mcp disabled (both servers invalid -> {})
    assert info.mcp_servers == {}
    # skills still loaded
    assert [s.name for s in info.skills] == ["hello"]


def test_plugin_data_dir_created(tmp_path):
    base = tmp_path / "plugins"
    base.mkdir()
    write_plugin(base / "alpha", "alpha")
    data = tmp_path / "data"

    reg = PluginRegistry([base], plugin_data_dir=data)
    reg.discover_plugins()

    assert (data / "alpha").is_dir()


def test_get_mcp_servers_grouped(tmp_path):
    reg = PluginRegistry([FIXTURES])
    reg.discover_plugins()
    grouped = reg.get_mcp_servers()
    assert "local-tool" in grouped
    assert isinstance(grouped["local-tool"], list)
    assert grouped["local-tool"][0].transport == "stdio"


def test_concurrent_load_safety(tmp_path):
    base = tmp_path / "plugins"
    base.mkdir()
    dirs = []
    for i in range(20):
        p = write_plugin(base / f"p{i}", f"p{i}")
        write_skill(p, "s", VALID_SKILL)
        dirs.append(p)

    reg = PluginRegistry([base], plugin_data_dir=tmp_path / "data")
    errors: list[Exception] = []

    def worker(path):
        try:
            reg.load_plugin(path)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(d,)) for d in dirs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(reg.get_all_plugins()) == 20
    assert len(reg.get_skills()) == 20
