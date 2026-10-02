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
    permission = _load("permission")

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

        async def ensure_session(
            self, user_id, session_id, *, extra_tools=None, permission_requester=None
        ):
            self.ensured.append((user_id, session_id, extra_tools, permission_requester))

        async def aclose(self):
            self.closed = True

    runtime.AgentRuntime = _FakeRuntime
    runtime.AgentUpdate = _AgentUpdate
    runtime.jsonable = _jsonable
    sys.modules[f"{_PKG}.runtime"] = runtime

    server = _load("server")
    return jsonrpc, fs, mcp, permission, server


_jsonrpc, _fs, _mcp, _permission, _server = _load_overlay_package()


def _adk_function_tool_available() -> bool:
    """Whether ADK's FunctionTool can be imported (installed in CI, absent in the lightweight lane)."""
    try:
        import google.adk.tools.function_tool  # noqa: F401

        return True
    except Exception:
        return False


class _FakeTransport:
    """Captures writes; never yields a client message on its own."""

    def __init__(self):
        self.written = []

    async def write(self, message):
        self.written.append(message)

    async def read(self):
        return None

    def close(self):
        pass

    async def wait_closed(self):
        pass


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

    def test_build_toolsets_returns_list_and_never_raises(self):
        # Without ADK/mcp installed → []; with them → one toolset. Either way a
        # list, and a malformed entry never crashes the build.
        out = _mcp.build_mcp_toolsets(
            [{"name": "fs", "command": "npx", "args": []}, {"bad": True}], cwd="/proj"
        )
        self.assertIsInstance(out, list)
        self.assertLessEqual(len(out), 1)  # the malformed entry is dropped


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

    def test_function_tools_shape(self):
        # With ADK: one tool per enabled capability. Without it: empty, never raises.
        both = _fs.FsBridge("s", None).function_tools()
        self.assertIsInstance(both, list)
        if _adk_function_tool_available():
            self.assertEqual(len(both), 2)  # read + write
            # Capabilities gate which tools are exposed.
            self.assertEqual(len(_fs.FsBridge("s", None, can_write=False).function_tools()), 1)
            self.assertEqual(
                _fs.FsBridge("s", None, can_read=False, can_write=False).function_tools(), []
            )
        else:
            self.assertEqual(both, [])


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
        self.assertTrue(caps["promptCapabilities"]["image"])  # multimodal prompts
        self.assertTrue(caps["promptCapabilities"]["embeddedContext"])
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
        user_id, session_id, extra, requester = agent._runtime.ensured[-1]
        self.assertEqual(session_id, out["sessionId"])
        self.assertIsInstance(extra, list)  # [] here (no ADK), but wired through
        # The permission gate needs a requester to reach the client.
        self.assertEqual(requester, agent.request)

    def test_serve_closes_runtime(self):
        agent = self._agent()  # FakeTransport.read() returns None → EOF immediately
        asyncio.run(agent.serve())
        self.assertTrue(agent._runtime.closed)


# ── prompt content blocks (text + image + embedded context) ──────────


class TestPromptParts(unittest.TestCase):
    @staticmethod
    def _b64(raw: bytes) -> str:
        import base64

        return base64.b64encode(raw).decode()

    def test_text_block(self):
        parts = _server._prompt_parts([{"type": "text", "text": "hello"}])
        self.assertEqual(parts, [{"kind": "text", "text": "hello"}])

    def test_image_block_is_base64_decoded(self):
        blob = b"\x89PNG\r\n\x1a\n"
        parts = _server._prompt_parts(
            [{"type": "image", "mimeType": "image/png", "data": self._b64(blob)}]
        )
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["kind"], "image")
        self.assertEqual(parts[0]["mime_type"], "image/png")
        self.assertEqual(parts[0]["data"], blob)  # decoded to raw bytes

    def test_embedded_text_resource(self):
        parts = _server._prompt_parts(
            [{"type": "resource", "resource": {"text": "file contents"}}]
        )
        self.assertEqual(parts, [{"kind": "text", "text": "file contents"}])

    def test_embedded_image_blob_resource(self):
        blob = b"\xff\xd8\xff"  # jpeg magic
        parts = _server._prompt_parts(
            [
                {
                    "type": "resource",
                    "resource": {"mimeType": "image/jpeg", "blob": self._b64(blob)},
                }
            ]
        )
        self.assertEqual(parts[0]["kind"], "image")
        self.assertEqual(parts[0]["mime_type"], "image/jpeg")
        self.assertEqual(parts[0]["data"], blob)

    def test_mixed_and_ordered(self):
        blob = b"abc"
        parts = _server._prompt_parts(
            [
                {"type": "text", "text": "look:"},
                {"type": "image", "mimeType": "image/gif", "data": self._b64(blob)},
            ]
        )
        self.assertEqual([p["kind"] for p in parts], ["text", "image"])

    def test_undecodable_image_is_dropped(self):
        parts = _server._prompt_parts(
            [{"type": "image", "mimeType": "image/png", "data": 12345}]
        )
        self.assertEqual(parts, [])

    def test_non_list_prompt(self):
        self.assertEqual(_server._prompt_parts(None), [])
        self.assertEqual(_server._prompt_parts("nope"), [])


# ── permission gate (session/request_permission HITL) ────────────────


class _FakeTool:
    def __init__(self, name):
        self.name = name


class _FakeToolContext:
    def __init__(self, function_call_id="fc-1"):
        self.function_call_id = function_call_id
        self.state = {}


def _permission_request_capture(outcome_option):
    """A requester that records the request and returns a chosen outcome."""
    calls = []

    async def requester(method, params):
        calls.append((method, params))
        if outcome_option == "__cancelled__":
            return {"outcome": {"outcome": "cancelled"}}
        if outcome_option == "__raise__":
            raise RuntimeError("client blew up")
        return {"outcome": {"outcome": "selected", "optionId": outcome_option}}

    return requester, calls


class TestPermissionSelection(unittest.TestCase):
    def test_needs_permission_matrix(self):
        needs = _permission._needs_permission
        # off → never
        self.assertFalse(needs("delete_record", "off", None))
        # sensitive → built-in set + destructive prefixes, but not benign
        self.assertTrue(needs("delete_record", "sensitive", None))
        self.assertTrue(needs("write_text_file", "sensitive", None))
        self.assertTrue(needs("purge_cache", "sensitive", None))
        self.assertFalse(needs("get_weather", "sensitive", None))
        # always-allowed never gated, even under "all"
        self.assertFalse(needs("read_text_file", "all", None))
        self.assertTrue(needs("get_weather", "all", None))
        # explicit set replaces the built-in set under sensitive
        self.assertTrue(needs("custom_tool", "sensitive", {"custom_tool"}))
        self.assertFalse(needs("delete_record", "sensitive", {"custom_tool"}))


class TestPermissionCallback(unittest.TestCase):
    def _run(self, callback, tool_name, ctx=None):
        tool = _FakeTool(tool_name)
        ctx = ctx or _FakeToolContext()
        return asyncio.run(callback(tool, {"x": 1}, ctx))

    def test_allow_once_lets_tool_run(self):
        requester, calls = _permission_request_capture("allow-once")
        cb = _permission.make_permission_callback("s", requester, mode="sensitive")
        self.assertIsNone(self._run(cb, "delete_record"))
        # It asked, with the right method + toolCall payload.
        method, params = calls[0]
        self.assertEqual(method, "session/request_permission")
        self.assertEqual(params["sessionId"], "s")
        self.assertEqual(params["toolCall"]["title"], "delete_record")
        self.assertEqual(params["toolCall"]["rawInput"], {"x": 1})

    def test_reject_once_blocks_with_message(self):
        requester, _ = _permission_request_capture("reject-once")
        cb = _permission.make_permission_callback("s", requester, mode="sensitive")
        result = self._run(cb, "delete_record")
        self.assertEqual(result["status"], "rejected")
        self.assertIn("delete_record", result["message"])

    def test_non_sensitive_tool_is_not_gated(self):
        requester, calls = _permission_request_capture("reject-once")
        cb = _permission.make_permission_callback("s", requester, mode="sensitive")
        self.assertIsNone(self._run(cb, "get_weather"))
        self.assertEqual(calls, [])  # never asked the client

    def test_allow_always_is_remembered(self):
        requester, calls = _permission_request_capture("allow-always")
        cb = _permission.make_permission_callback("s", requester, mode="sensitive")
        self.assertIsNone(self._run(cb, "delete_record"))
        self.assertIsNone(self._run(cb, "delete_record"))  # second call
        self.assertEqual(len(calls), 1)  # only asked once

    def test_reject_always_is_remembered(self):
        requester, calls = _permission_request_capture("reject-always")
        cb = _permission.make_permission_callback("s", requester, mode="sensitive")
        first = self._run(cb, "delete_record")
        second = self._run(cb, "delete_record")
        self.assertEqual(first["status"], "rejected")
        self.assertEqual(second["status"], "rejected")
        self.assertEqual(len(calls), 1)  # remembered, didn't re-ask

    def test_cancelled_outcome_blocks(self):
        requester, _ = _permission_request_capture("__cancelled__")
        cb = _permission.make_permission_callback("s", requester, mode="all")
        self.assertEqual(self._run(cb, "any_tool")["status"], "rejected")

    def test_client_error_fails_closed(self):
        requester, _ = _permission_request_capture("__raise__")
        cb = _permission.make_permission_callback("s", requester, mode="all")
        self.assertEqual(self._run(cb, "any_tool")["status"], "rejected")

    def test_off_mode_returns_none_callback(self):
        requester, _ = _permission_request_capture("allow-once")
        self.assertIsNone(
            _permission.make_permission_callback("s", requester, mode="off")
        )

    def test_chained_callback_runs_first_and_can_block(self):
        requester, calls = _permission_request_capture("allow-once")

        def chained(tool, args, ctx):
            return {"status": "blocked", "message": "policy"}

        cb = _permission.make_permission_callback(
            "s", requester, mode="off", chained=chained
        )
        self.assertIsNotNone(cb)  # chained keeps the callback alive even in off mode
        result = self._run(cb, "delete_record")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(calls, [])  # blocked before asking

    def test_chained_async_callback_supported(self):
        requester, _ = _permission_request_capture("reject-once")

        async def chained(tool, args, ctx):
            return None  # allow through to the gate

        cb = _permission.make_permission_callback(
            "s", requester, mode="sensitive", chained=chained
        )
        self.assertEqual(self._run(cb, "delete_record")["status"], "rejected")


class TestPermissionFromEnv(unittest.TestCase):
    def setUp(self):
        self._saved = {
            k: __import__("os").environ.get(k)
            for k in ("ACP_PERMISSION_MODE", "ACP_PERMISSION_TOOLS")
        }

    def tearDown(self):
        os = __import__("os")
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _set(self, **kw):
        os = __import__("os")
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_mode_is_sensitive(self):
        self._set(ACP_PERMISSION_MODE=None, ACP_PERMISSION_TOOLS=None)
        requester, calls = _permission_request_capture("reject-once")
        cb = _permission.permission_callback_from_env("s", requester)
        self.assertIsNotNone(cb)
        # A sensitive tool is gated by default.
        out = asyncio.run(cb(_FakeTool("delete_record"), {}, _FakeToolContext()))
        self.assertEqual(out["status"], "rejected")

    def test_off_mode_disables(self):
        self._set(ACP_PERMISSION_MODE="off", ACP_PERMISSION_TOOLS=None)
        requester, _ = _permission_request_capture("reject-once")
        self.assertIsNone(_permission.permission_callback_from_env("s", requester))

    def test_explicit_tools_override(self):
        self._set(ACP_PERMISSION_MODE="sensitive", ACP_PERMISSION_TOOLS="custom_tool, other")
        requester, calls = _permission_request_capture("reject-once")
        cb = _permission.permission_callback_from_env("s", requester)
        # A default-sensitive tool is NOT gated now; the explicit one is.
        self.assertIsNone(asyncio.run(cb(_FakeTool("delete_record"), {}, _FakeToolContext())))
        out = asyncio.run(cb(_FakeTool("custom_tool"), {}, _FakeToolContext()))
        self.assertEqual(out["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
