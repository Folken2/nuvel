"""Tests for mcp.json reading + validation."""

from __future__ import annotations

import json

from nuvel.agent_plugins import read_mcp_config

from .conftest import FIXTURES, write_plugin


def _write_mcp(root, servers, schema=True):
    data = {}
    if schema:
        data["$schema"] = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    data["mcpServers"] = servers
    (root / "mcp.json").write_text(json.dumps(data), encoding="utf-8")


def test_missing_mcp_returns_empty(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    assert read_mcp_config(root) == {}


def test_valid_stdio_loads(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    _write_mcp(
        root,
        {"srv": {"type": "stdio", "command": "python", "args": ["-m", "x"]}},
    )
    servers = read_mcp_config(root)
    assert "srv" in servers
    assert servers["srv"].transport == "stdio"
    assert servers["srv"].command == "python"
    assert servers["srv"].args == ["-m", "x"]


def test_valid_streamable_http_loads(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    _write_mcp(
        root,
        {"api": {"type": "streamable-http", "url": "https://x/mcp", "headers": {"A": "b"}}},
    )
    servers = read_mcp_config(root)
    assert servers["api"].transport == "streamable-http"
    assert servers["api"].url == "https://x/mcp"
    assert servers["api"].headers == {"A": "b"}
    assert servers["api"].deprecated is False


def test_http_requires_https(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    _write_mcp(root, {"api": {"type": "streamable-http", "url": "http://x/mcp"}})
    assert read_mcp_config(root) == {}


def test_sse_loads_and_marked_deprecated(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    _write_mcp(root, {"old": {"type": "sse", "url": "https://x/sse"}})
    servers = read_mcp_config(root)
    assert servers["old"].transport == "sse"
    assert servers["old"].deprecated is True


def test_invalid_server_skipped_others_load(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    _write_mcp(
        root,
        {
            "broken": {"type": "stdio"},  # missing command
            "unknown": {"type": "carrier-pigeon", "command": "x"},
            "good": {"type": "stdio", "command": "node"},
        },
    )
    servers = read_mcp_config(root)
    assert set(servers) == {"good"}


def test_invalid_mcp_json_disables_all(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    (root / "mcp.json").write_text(
        json.dumps({"mcpServers": ["not", "an", "object"]}), encoding="utf-8"
    )
    assert read_mcp_config(root) == {}


def test_malformed_mcp_json_disables_all(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    (root / "mcp.json").write_text("{ broken", encoding="utf-8")
    assert read_mcp_config(root) == {}


def test_placeholder_expansion(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_mcp(
        root,
        {
            "srv": {
                "type": "stdio",
                "command": "python",
                "args": ["--root", "${PLUGIN_ROOT}"],
                "env": {"DATA": "${PLUGIN_DATA}/cache"},
                "cwd": "${PLUGIN_DATA}",
            }
        },
    )
    servers = read_mcp_config(root, plugin_data=data_dir)
    srv = servers["srv"]
    assert srv.args == ["--root", str(root)]
    assert srv.env == {"DATA": f"{data_dir}/cache"}
    assert srv.cwd == str(data_dir)


def test_relative_cwd_within_root(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    _write_mcp(root, {"srv": {"type": "stdio", "command": "python", "cwd": "./sub"}})
    servers = read_mcp_config(root)
    assert servers["srv"].cwd == "./sub"


def test_cwd_path_escape_caught(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    _write_mcp(
        root, {"srv": {"type": "stdio", "command": "python", "cwd": "./../../etc"}}
    )
    assert read_mcp_config(root) == {}


def test_command_path_escape_caught(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    _write_mcp(root, {"srv": {"type": "stdio", "command": "./../../bin/sh"}})
    assert read_mcp_config(root) == {}


def test_command_with_slash_rejected(tmp_path):
    root = write_plugin(tmp_path / "p", "p")
    _write_mcp(root, {"srv": {"type": "stdio", "command": "/usr/bin/python"}})
    assert read_mcp_config(root) == {}


def test_fixture_valid_full_mcp():
    servers = read_mcp_config(FIXTURES / "valid-full")
    assert set(servers) == {"local-tool", "remote-api"}
    assert servers["local-tool"].transport == "stdio"
    assert servers["remote-api"].transport == "streamable-http"
