"""Behavioral tests for the ACP editor-integration bridge.

Covers the three pieces that turn the ACP adapter from a proof-of-concept
into something useful inside an editor:

* ``mcpServers`` passthrough — parsing editor-supplied MCP server declarations
  (``acp/mcp.py``);
* the ``fs/read_text_file`` / ``fs/write_text_file`` bridge (``acp/fs.py``);
* agent→client request/response correlation and session wiring in the ACP
  server (``acp/server.py``).

These load the overlay modules **directly from the template tree** with a
stubbed ``runtime`` module, so the whole file runs without ``google-adk`` /
``google-genai`` installed — the ADK-dependent code paths degrade gracefully
(no MCP toolsets / no fs FunctionTools) and are exercised in that mode.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path

_ACP_DIR = (
    Path(__file__).resolve().parent.parent
    / "nuvel"
    / "backends"
    / "adk"
    / "templates_overlays"
    / "acp"
    / "{{agent_package}}"
    / "acp"
)

# Unique synthetic package name so these loads never clobber a real install.
_PKG = "_acp_bridge_under_test"


def _load_overlay_package():
    """Load the overlay ``acp`` modules under a synthetic package.

    ``runtime`` is stubbed (it imports the generated agent, which needs ADK);
    everything else is the real overlay source.
    """
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_ACP_DIR)]
    pkg.PROTOCOL_VERSION = 1
    sys.modules[_PKG] = pkg

    def _load(mod: str):
        name = f"{_PKG}.{mod}"
        spec = importlib.util.spec_from_file_location(name, _ACP_DIR / f"{mod}.py")
        module = importlib.util.module_from_spec(spec)
        module.__package__ = _PKG
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    jsonrpc = _load("jsonrpc")
    fs = _load("fs")
    mcp = _load("mcp")

    # Stub the ADK-dependent runtime so server.py imports cleanly.
    runtime = types.ModuleType(f"{_PKG}.runtime")
    runtime.__package__ = _PKG

    class _AgentUpdate:  # only referenced by name in server.py
        pass

    def _jsonable(value):
        return value

    class _FakeRuntime:
        def __init__(self):
            self.ensured = []
            self.closed = False

        async def ensure_session(self, user_id, session_id, *, extra_tools=None):
            self.ensured.append((user_id, session_id, extra_tools))

        async def aclose(self):
            self.closed = True

    runtime.AgentRuntime = _FakeRuntime
    runtime.AgentUpdate = _AgentUpdate
    runtime.jsonable = _jsonable
    sys.modules[f"{_PKG}.runtime"] = runtime

    server = _load("server")
    return jsonrpc, fs, mcp, server


_jsonrpc, _fs, _mcp, _server = _load_overlay_package()


class _FakeTransport:
    """Captures writes; never yields a client message on its own."""

    def __init__(self):
        self.written = []

    async def write(self, message):
        self.written.append(message)

    async def read(self):
        return None


# ── mcpServers parsing ───────────────────────────────────────────────


class TestMcpServerParsing(unittest.TestCase):
    def test_stdio_entry(self):
        spec = _mcp.parse_mcp_server(
            {
                "name": "filesystem",
                "command": "npx",
                "args": ["-y", "@mcp/server-fs", "/tmp"],
                "env": [{"name": "TOKEN", "value": "abc"}],
            }
        )
        self.assertIsNotNone(spec)
        self.assertEqual(spec.transport, "stdio")
        self.assertEqual(spec.command, "npx")
        self.assertEqual(spec.args, ["-y", "@mcp/server-fs", "/tmp"])
        self.assertEqual(spec.env, {"TOKEN": "abc"})

    def test_http_and_sse_entries(self):
        http = _mcp.parse_mcp_server(
            {
                "name": "remote",
                "type": "http",
                "url": "https://h/mcp",
                "headers": [{"name": "Authorization", "value": "Bearer z"}],
            }
        )
        self.assertEqual(http.transport, "http")
        self.assertEqual(http.url, "https://h/mcp")
        self.assertEqual(http.headers, {"Authorization": "Bearer z"})

        sse = _mcp.parse_mcp_server({"name": "s", "type": "sse", "url": "https://s/sse"})
        self.assertEqual(sse.transport, "sse")
        self.assertEqual(sse.url, "https://s/sse")

    def test_invalid_entries_are_dropped(self):
        self.assertIsNone(_mcp.parse_mcp_server({"name": "nocmd"}))
        self.assertIsNone(_mcp.parse_mcp_server({"command": "x"}))  # no name
        self.assertIsNone(_mcp.parse_mcp_server("not-a-dict"))
        self.assertIsNone(_mcp.parse_mcp_server({"name": "h", "type": "http"}))  # no url

    def test_parse_list_skips_unsupported(self):
        specs = _mcp.parse_mcp_servers(
            [
                {"name": "a", "command": "x"},
                {"bad": True},
                {"name": "b", "type": "sse", "url": "https://b"},
            ]
        )
        self.assertEqual([s.name for s in specs], ["a", "b"])

    def test_pairs_to_dict_forms(self):
        self.assertEqual(_mcp._pairs_to_dict([{"name": "A", "value": 1}]), {"A": "1"})
        self.assertEqual(_mcp._pairs_to_dict({"A": "1"}), {"A": "1"})
        self.assertEqual(_mcp._pairs_to_dict(None), {})

    def test_build_toolsets_degrades_without_adk(self):
        # No google-adk here → no toolsets, but never raises.
        out = _mcp.build_mcp_toolsets(
            [{"name": "fs", "command": "npx", "args": []}], cwd="/proj"
        )
        self.assertEqual(out, [])


# ── fs bridge ────────────────────────────────────────────────────────


class TestFsBridge(unittest.TestCase):
    def test_read_sends_request_and_returns_content(self):
        calls = []

        async def requester(method, params):
            calls.append((method, params))
            return {"content": "hello world"}

        async def go():
            bridge = _fs.FsBridge("sess-1", requester)
            return await bridge.read_text_file("/abs/file.py")

        result = asyncio.run(go())
        self.assertEqual(result, "hello world")
        self.assertEqual(
            calls[0],
            ("fs/read_text_file", {"sessionId": "sess-1", "path": "/abs/file.py"}),
        )

    def test_write_sends_request_and_confirms(self):
        calls = []

        async def requester(method, params):
            calls.append((method, params))
            return None

        async def go():
            bridge = _fs.FsBridge("sess-2", requester)
            return await bridge.write_text_file("/abs/out.py", "abcd")

        msg = asyncio.run(go())
        self.assertIn("4 characters", msg)
        self.assertIn("/abs/out.py", msg)
        self.assertEqual(
            calls[0],
            (
                "fs/write_text_file",
                {"sessionId": "sess-2", "path": "/abs/out.py", "content": "abcd"},
            ),
        )

    def test_read_tolerates_non_dict_response(self):
        async def requester(method, params):
            return None

        async def go():
            return await _fs.FsBridge("s", requester).read_text_file("/x")

        self.assertEqual(asyncio.run(go()), "")

    def test_function_tools_degrades_without_adk(self):
        # No ADK FunctionTool available here → empty, but never raises.
        self.assertEqual(_fs.FsBridge("s", None).function_tools(), [])


# ── server: initialize + request correlation + session wiring ────────


class TestACPServer(unittest.TestCase):
    def _agent(self):
        return _server.ACPAgent(_FakeTransport())

    def test_initialize_captures_fs_caps_and_advertises_mcp(self):
        agent = self._agent()
        res = agent._handle_initialize(
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": False}
                },
            }
        )
        self.assertEqual(agent._client_fs, {"read": True, "write": False})
        caps = res["agentCapabilities"]
        self.assertEqual(caps["mcpCapabilities"], {"http": True, "sse": True})
        self.assertFalse(caps["loadSession"])
        self.assertEqual(res["protocolVersion"], 1)

    def test_request_resolves_on_response(self):
        agent = self._agent()

        async def go():
            task = asyncio.ensure_future(
                agent.request("fs/read_text_file", {"sessionId": "s", "path": "/x"})
            )
            await asyncio.sleep(0)  # let request() write
            sent = agent._t.written[-1]
            self.assertEqual(sent["method"], "fs/read_text_file")
            self.assertEqual(sent["id"], "acp-1")
            agent._resolve_response({"id": "acp-1", "result": {"content": "data"}})
            return await task

        self.assertEqual(asyncio.run(go()), {"content": "data"})
        self.assertEqual(agent._pending, {})  # cleaned up

    def test_request_raises_on_client_error(self):
        agent = self._agent()

        async def go():
            task = asyncio.ensure_future(agent.request("fs/write_text_file", {}))
            await asyncio.sleep(0)
            agent._resolve_response(
                {"id": "acp-1", "error": {"code": -32000, "message": "denied"}}
            )
            await task

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(go())
        self.assertIn("denied", str(ctx.exception))

    def test_dispatch_routes_response_to_pending(self):
        agent = self._agent()

        async def go():
            task = asyncio.ensure_future(agent.request("fs/read_text_file", {}))
            await asyncio.sleep(0)
            req_id = agent._t.written[-1]["id"]
            await agent._dispatch({"jsonrpc": "2.0", "id": req_id, "result": {"content": "ok"}})
            return await task

        self.assertEqual(asyncio.run(go()), {"content": "ok"})

    def test_unknown_response_id_is_ignored(self):
        agent = self._agent()
        # Must not raise even with no matching pending future.
        agent._resolve_response({"id": "acp-nope", "result": 1})

    def test_new_session_wires_extra_tools_and_ensures_session(self):
        agent = self._agent()
        agent._client_fs = {"read": True, "write": True}

        async def go():
            return await agent._handle_new_session(
                {
                    "cwd": "/proj",
                    "mcpServers": [{"name": "fs", "command": "npx", "args": []}],
                }
            )

        out = asyncio.run(go())
        self.assertIn("sessionId", out)
        user_id, session_id, extra = agent._runtime.ensured[-1]
        self.assertEqual(session_id, out["sessionId"])
        self.assertIsInstance(extra, list)  # [] here (no ADK), but wired through

    def test_serve_closes_runtime(self):
        agent = self._agent()  # FakeTransport.read() returns None → EOF immediately
        asyncio.run(agent.serve())
        self.assertTrue(agent._runtime.closed)


if __name__ == "__main__":
    unittest.main()
