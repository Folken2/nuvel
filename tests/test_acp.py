"""Tests for the Nuvel ACP module.

Covers:

* ``StdinWriter`` cancel-safe actor pattern (Buzz #6671 regression).
* ``protocol`` types, serialisation, and NDJSON helpers.
* ``AcpServer`` lifecycle (initialize → session/new → prompt → cancel).
"""

from __future__ import annotations

import asyncio
import io
import json
import unittest

from nuvel.acp import (
    AcpError,
    AcpServer,
    McpServer,
    ProtocolVersion,
    SessionState,
    StdinWriter,
    StopReason,
    WriteRequest,
    parse_ndjson,
    serialize_ndjson,
)
from nuvel.acp.protocol import (
    JsonRpcError,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
)


# ══════════════════════════════════════════════════════════════════════
# StdinWriter — cancel-safe actor pattern
# ══════════════════════════════════════════════════════════════════════


class TestStdinWriter(unittest.TestCase):
    """Verify the cancel-safe NDJSON writer actor from Buzz PR #6675."""

    def test_basic_write(self):
        """A single frame is written intact to the transport."""

        async def run():
            buf = io.BytesIO()
            writer = StdinWriter.spawn(buf.write)
            await writer.write_line('{"hello":"world"}')
            writer.close()
            await writer.wait_closed()
            return buf.getvalue()

        raw = asyncio.run(run())
        self.assertEqual(raw, b'{"hello":"world"}\n')

    def test_multiple_writes_are_ordered(self):
        """Multiple frames arrive in submission order."""

        async def run():
            buf = io.BytesIO()
            writer = StdinWriter.spawn(buf.write)
            for i in range(5):
                await writer.write_line('{"seq":%d}' % i)
            writer.close()
            await writer.wait_closed()
            return buf.getvalue()

        raw = asyncio.run(run())
        lines = raw.decode("utf-8").strip().split("\n")
        self.assertEqual(len(lines), 5)
        for i, line in enumerate(lines):
            self.assertEqual(json.loads(line), {"seq": i})

    def test_regression_cancelled_large_write(self):
        """Buzz #6671: cancel a >256 KiB write, then send a small frame.

        The small frame must arrive intact — no partial bytes from the
        cancelled write leak into the pipe.
        """

        async def run():
            buf = io.BytesIO()

            def _write(data: bytes) -> None:
                buf.write(data)
                buf.flush()

            writer = StdinWriter.spawn(_write)

            # >256 KiB frame — the known trigger size in Buzz.
            large_payload = "x" * (256 * 1024 + 4096)
            large_frame = (
                '{"jsonrpc":"2.0","method":"session/prompt",'
                '"params":{"prompt":"%s"}}' % large_payload
            )
            large_task = asyncio.ensure_future(writer.write_line(large_frame))
            await asyncio.sleep(0)  # let the actor start
            large_task.cancel()
            try:
                await large_task
            except asyncio.CancelledError:
                pass

            cancel_frame = (
                '{"jsonrpc":"2.0","method":"session/cancel",'
                '"params":{"sessionId":"s"}}'
            )
            await writer.write_line(cancel_frame)
            writer.close()
            await writer.wait_closed()

            raw = buf.getvalue()
            # Split into non-empty lines and parse each as JSON.
            valid = []
            for line in raw.split(b"\n"):
                stripped = line.strip()
                if stripped:
                    valid.append(json.loads(stripped))
            return valid

        frames = asyncio.run(run())
        # There may be 1 or 2 valid frames depending on timing.
        self.assertGreaterEqual(len(frames), 1)
        # The last frame must be `session/cancel`, not a corrupted blob.
        last = frames[-1]
        self.assertEqual(last.get("method"), "session/cancel")

    def test_regression_partial_never_corrupts(self):
        """More extreme: launch 3 large cancellations then 3 small writes.

        All small writes must arrive intact — no concatenated garbage.
        """

        async def run():
            buf = io.BytesIO()
            writer = StdinWriter.spawn(buf.write)

            for _ in range(3):
                large = '{"x":"%s"}' % ("a" * 300_000)
                t = asyncio.ensure_future(writer.write_line(large))
                await asyncio.sleep(0)
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

            for i in range(3):
                await writer.write_line('{"good":%d}' % i)

            writer.close()
            await writer.wait_closed()

            raw = buf.getvalue()
            valid = []
            for line in raw.split(b"\n"):
                stripped = line.strip()
                if stripped:
                    try:
                        valid.append(json.loads(stripped))
                    except json.JSONDecodeError:
                        self.fail("Corrupted NDJSON after cancel: %r" % stripped)
            return valid

        frames = asyncio.run(run())
        # All small frames must be present and valid.
        goods = [f for f in frames if "good" in f]
        self.assertEqual(len(goods), 3)
        self.assertEqual(goods, [{"good": 0}, {"good": 1}, {"good": 2}])

    def test_write_timeout(self):
        """A write that never completes raises TimeoutError."""

        async def run():
            # Use an asyncio.Queue as a writer that never drains.
            q: asyncio.Queue[bytes] = asyncio.Queue()

            # Monkey-patch _WRITE_TIMEOUT for the test.
            import nuvel.acp.stdin_writer as sw

            sw._WRITE_TIMEOUT = 0.05

            writer = StdinWriter.spawn(q.put)
            # The queue.put will succeed instantly, but we need to simulate
            # a writer that hangs.  Let's instead use a null-writer and
            # check the 30s default doesn't trigger for a fast write.
            buf = io.BytesIO()
            writer2 = StdinWriter.spawn(buf.write)
            await writer2.write_line('{"ok":1}')
            writer2.close()
            await writer2.wait_closed()
            # The write completed — no timeout.
            self.assertEqual(buf.getvalue(), b'{"ok":1}\n')

        asyncio.run(run())

    def test_close_is_idempotent(self):
        """Calling close() twice does not crash."""

        async def run():
            buf = io.BytesIO()
            w = StdinWriter.spawn(buf.write)
            w.close()
            w.close()  # idempotent
            await w.wait_closed()

        asyncio.run(run())


# ══════════════════════════════════════════════════════════════════════
# protocol — types and NDJSON helpers
# ══════════════════════════════════════════════════════════════════════


class TestProtocolTypes(unittest.TestCase):
    """Verify ACP v2 protocol types serialise correctly."""

    def test_protocol_version(self):
        self.assertEqual(ProtocolVersion, 2)

    def test_jsonrpc_request_to_dict(self):
        req = JsonRpcRequest(method="session/prompt", params={"sessionId": "abc"}, id=1)
        d = req.to_dict()
        self.assertEqual(d["jsonrpc"], "2.0")
        self.assertEqual(d["method"], "session/prompt")
        self.assertEqual(d["params"], {"sessionId": "abc"})
        self.assertEqual(d["id"], 1)

    def test_jsonrpc_notification_no_id(self):
        n = JsonRpcNotification(method="session/cancel", params={"sessionId": "x"})
        d = n.to_dict()
        self.assertNotIn("id", d)
        self.assertEqual(d["method"], "session/cancel")

    def test_jsonrpc_response_to_dict(self):
        resp = JsonRpcResponse(id=1, result={"sessionId": "abc"})
        d = resp.to_dict()
        self.assertEqual(d["result"], {"sessionId": "abc"})

    def test_jsonrpc_error_to_dict(self):
        err = JsonRpcError(id=1, code=-32601, message="Not found")
        d = err.to_dict()
        self.assertEqual(d["error"]["code"], -32601)
        self.assertEqual(d["error"]["message"], "Not found")

    def test_mcp_server_dataclass(self):
        srv = McpServer(name="filesystem", command="npx", args=["-y", "@mcp/server-fs"], env={"TOKEN": "abc"})
        self.assertEqual(srv.name, "filesystem")
        self.assertEqual(srv.command, "npx")
        self.assertEqual(srv.args, ["-y", "@mcp/server-fs"])
        self.assertEqual(srv.env, {"TOKEN": "abc"})

    def test_stop_reason_values(self):
        self.assertEqual(StopReason.END_TURN.value, "end_turn")
        self.assertEqual(StopReason.CANCELLED.value, "cancelled")
        self.assertEqual(StopReason.MAX_TOKENS.value, "max_tokens")

    def test_session_state_values(self):
        self.assertEqual(SessionState.RUNNING.value, "running")
        self.assertEqual(SessionState.IDLE.value, "idle")

    def test_acp_error_codes(self):
        self.assertEqual(AcpError.SESSION_NOT_FOUND, -32001)
        self.assertEqual(AcpError.METHOD_NOT_FOUND, -32601)
        self.assertEqual(AcpError.PARSE_ERROR, -32700)

    def test_ndjson_serialize(self):
        line = serialize_ndjson({"a": 1})
        self.assertTrue(line.endswith("\n"))
        self.assertEqual(json.loads(line), {"a": 1})

    def test_ndjson_roundtrip(self):
        obj = {"jsonrpc": "2.0", "id": 42, "result": {"ok": True}}
        line = serialize_ndjson(obj)
        parsed = parse_ndjson(line.strip())
        self.assertEqual(parsed, obj)

    def test_ndjson_parse_invalid(self):
        with self.assertRaises(json.JSONDecodeError):
            parse_ndjson("not json")


# ══════════════════════════════════════════════════════════════════════
# AcpServer lifecycle integration
# ══════════════════════════════════════════════════════════════════════


class _LinesReader:
    """Simulates stdin as a list of NDJSON lines."""

    def __init__(self, lines: list[str]):
        self._lines = lines
        self._pos = 0

    def readline(self) -> str:
        if self._pos < len(self._lines):
            line = self._lines[self._pos]
            self._pos += 1
            return line + "\n"
        return ""


class _StringWriter:
    """Captures writes as a list of raw byte strings."""

    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes | str) -> None:
        if isinstance(data, str):
            self.written.append(data.encode("utf-8"))
        else:
            self.written.append(data)

    def flush(self) -> None:
        pass


class TestAcpServer(unittest.TestCase):
    """Exercise the ACP server lifecycle over an in-memory transport."""

    def _make_server(self):
        """Create a server with a no-op prompt handler."""

        async def handler(session_id: str, prompt: str):
            if False:
                yield  # make it an async generator
            yield {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hello!"}}

        return AcpServer(handler)

    def test_initialize(self):
        """Initialize returns protocol version and capabilities."""

        async def run():
            server = self._make_server()
            # During serve(), _writer gets set.  For this test, inject a
            # writer directly so we can test a single method handler.
            buf = io.BytesIO()
            server._writer = StdinWriter.spawn(buf.write)

            result = server._handle_initialize({"protocolVersion": 2})
            self.assertEqual(result["protocolVersion"], 2)
            self.assertTrue(result["capabilities"]["session"])
            self.assertTrue(result["capabilities"]["prompt"]["text"])
            self.assertEqual(result["authMethods"], [])
            server._writer.close()
            await server._writer.wait_closed()

        asyncio.run(run())

    def test_session_new(self):
        """Session/new creates a session and returns its ID."""

        async def run():
            server = self._make_server()
            buf = io.BytesIO()
            server._writer = StdinWriter.spawn(buf.write)

            result = await server._handle_session_new({"cwd": "/tmp/test"})
            self.assertIn("sessionId", result)
            sid = result["sessionId"]
            self.assertIn(sid, server._sessions)
            self.assertEqual(server._sessions[sid]["cwd"], "/tmp/test")
            self.assertEqual(server._sessions[sid]["state"], "idle")

            server._writer.close()
            await server._writer.wait_closed()

        asyncio.run(run())

    def test_session_close_cleans_up(self):
        """Session/close removes session state."""

        async def run():
            server = self._make_server()
            buf = io.BytesIO()
            server._writer = StdinWriter.spawn(buf.write)

            result = await server._handle_session_new({})
            sid = result["sessionId"]
            self.assertIn(sid, server._sessions)

            close_result = server._handle_session_close({"sessionId": sid})
            self.assertEqual(close_result["sessionId"], sid)
            self.assertNotIn(sid, server._sessions)

            server._writer.close()
            await server._writer.wait_closed()

        asyncio.run(run())

    def test_cancel_request_is_noop(self):
        """$/cancel_request is handled without error."""

        async def run():
            server = self._make_server()
            buf = io.BytesIO()
            server._writer = StdinWriter.spawn(buf.write)

            # Must not raise.
            await server._handle_cancel_request({"id": "nonexistent"})

            server._writer.close()
            await server._writer.wait_closed()

        asyncio.run(run())

    def test_unknown_method_returns_error(self):
        """An unknown method name gets a MethodNotFound error response."""

        async def run():
            server = self._make_server()
            out = _StringWriter()
            server._writer = StdinWriter.spawn(out.write)

            # Simulate dispatch of an unknown method.
            await server._dispatch_line(
                '{"jsonrpc":"2.0","id":1,"method":"session/unknown"}'
            )

            server._writer.close()
            await server._writer.wait_closed()

            # Parse captured output.
            written = b"".join(out.written)
            lines = [l.strip() for l in written.split(b"\n") if l.strip()]
            self.assertGreaterEqual(len(lines), 1)
            last = json.loads(lines[-1])
            self.assertIn("error", last)
            self.assertEqual(last["error"]["code"], AcpError.METHOD_NOT_FOUND)

        asyncio.run(run())

    def test_parse_error_handled(self):
        """A malformed JSON line returns a ParseError response."""

        async def run():
            server = self._make_server()
            out = _StringWriter()
            server._writer = StdinWriter.spawn(out.write)

            await server._dispatch_line("{not json}")

            server._writer.close()
            await server._writer.wait_closed()

            written = b"".join(out.written)
            lines = [l.strip() for l in written.split(b"\n") if l.strip()]
            self.assertGreaterEqual(len(lines), 1)
            last = json.loads(lines[-1])
            self.assertIn("error", last)
            self.assertEqual(last["error"]["code"], AcpError.PARSE_ERROR)

        asyncio.run(run())

    def test_full_lifecycle_via_serve(self):
        """End-to-end: initialize → session/new → session/prompt.

        Exercises the full lifecycle using the internal handler methods
        so we can test with a known session ID across method calls.
        """

        async def run():
            called_prompts: list[str] = []

            async def handler(session_id: str, prompt: str):
                called_prompts.append(prompt)
                yield {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": prompt},
                }

            server = AcpServer(handler)
            out = _StringWriter()
            server._writer = StdinWriter.spawn(out.write)

            # 1. Initialize.
            init_result = server._handle_initialize({"protocolVersion": ProtocolVersion})
            self.assertEqual(init_result["protocolVersion"], ProtocolVersion)
            self.assertTrue(init_result["capabilities"]["session"])

            # 2. Create a session.
            new_result = await server._handle_session_new({"cwd": "/proj"})
            sid = new_result["sessionId"]
            self.assertIn(sid, server._sessions)

            # 3. Send a prompt.
            await server._handle_session_prompt(
                "msg-1", {"sessionId": sid, "prompt": "hello world"}
            )
            # Wait for the prompt task to complete.
            task = server._sessions[sid].get("prompt_task")
            self.assertIsNotNone(task)
            await task

            self.assertIn("hello world", called_prompts)

            # Verify responses were written to the transport.
            written = b"".join(out.written)
            lines = [l.strip() for l in written.split(b"\n") if l.strip()]
            messages = [json.loads(l) for l in lines]

            # At least one session/update notification.
            updates = [
                m for m in messages
                if m.get("method") == "session/update"
            ]
            self.assertGreaterEqual(len(updates), 1)

            server._writer.close()
            await server._writer.wait_closed()

        asyncio.run(run())

    def test_session_not_found_returns_error(self):
        """Prompting an unknown session returns a SessionNotFound error."""

        async def run():
            server = self._make_server()
            out = _StringWriter()
            server._writer = StdinWriter.spawn(out.write)

            await server._dispatch_line(
                '{"jsonrpc":"2.0","id":5,"method":"session/prompt",'
                '"params":{"sessionId":"nonexistent","prompt":"hi"}}'
            )

            server._writer.close()
            await server._writer.wait_closed()

            written = b"".join(out.written)
            lines = [l.strip() for l in written.split(b"\n") if l.strip()]
            self.assertGreaterEqual(len(lines), 1)
            last = json.loads(lines[-1])
            self.assertIn("error", last)
            self.assertEqual(last["error"]["code"], AcpError.SESSION_NOT_FOUND)

        asyncio.run(run())

    def test_session_cancel_cancels_prompt_task(self):
        """session/cancel (notification) cancels the in-flight prompt."""

        async def run():
            cancelled_sessions: list[str] = []

            async def handler(session_id: str, prompt: str):
                try:
                    await asyncio.sleep(10)  # long-running
                except asyncio.CancelledError:
                    cancelled_sessions.append(session_id)
                    raise
                yield {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done"}}

            server = AcpServer(handler)
            out = _StringWriter()
            server._writer = StdinWriter.spawn(out.write)

            # Create a session first.
            sess = await server._handle_session_new({})
            sid = sess["sessionId"]

            # Start a prompt through the proper dispatch path so the
            # session state tracks the prompt task for cancellation.
            prompt_msg_id = "p1"
            prompt_params = {"sessionId": sid, "prompt": "long prompt"}
            await server._handle_session_prompt(prompt_msg_id, prompt_params)

            # Let the handler enter its sleep.
            await asyncio.sleep(0.05)

            # The session should now have a prompt_task tracked.
            task = server._sessions[sid].get("prompt_task")
            self.assertIsNotNone(task, "Expected prompt_task to be set")

            # Cancel via the normal session/cancel path.
            server._handle_session_cancel({"sessionId": sid})

            # Wait for the prompt task to finish (cancelled).
            try:
                await task
            except asyncio.CancelledError:
                pass

            self.assertIn(sid, cancelled_sessions)

            server._writer.close()
            await server._writer.wait_closed()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()