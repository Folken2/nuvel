"""ACP v2 agent server skeleton.

Implements the agent side of the Agent Client Protocol (ACP) over
stdio JSON-RPC 2.0 with newline-delimited JSON (NDJSON).

The server reads from stdin, dispatches to method handlers, and writes
responses/notifications to stdout through a cancel-safe
:class:`~.stdin_writer.StdinWriter`.

Lifecycle
---------

1. Client sends ``initialize`` → server responds with protocol version
   and capabilities.
2. Client sends ``session/new`` → server creates a session and returns
   its ID.
3. Client sends ``session/prompt`` → server fires the user-provided
   callback, streaming ``session/update`` notifications for each chunk
   of output, then responds with ``stopReason``.
4. Client may send ``session/cancel`` at any time → server cancels the
   in-flight prompt task safely (writes go through the actor, so no
   partial NDJSON frames).
5. ``session/close`` tears down the session state.

Shutdown
--------
The loop runs until stdin reaches EOF or a SIGTERM arrives, then
drains and closes cleanly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import uuid
from typing import Any, AsyncGenerator, Callable

from .protocol import (
    AcpError,
    ProtocolVersion,
    StopReason,
    parse_ndjson,
    serialize_ndjson,
)
from .stdin_writer import StdinWriter

logger = logging.getLogger(__name__)

# ── JSON-RPC 2.0 standard error codes (re-exported for convenience) ──

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _make_response(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _make_error(msg_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _make_notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params:
        msg["params"] = params
    return msg


# ── AcpServer ────────────────────────────────────────────────────────

# Signature for the user-provided handler callback.
PromptHandler = Callable[
    [str, str],  # (session_id, prompt_text)
    AsyncGenerator[dict[str, Any], None],
]
"""A callback that takes a session ID and prompt text and yields
``session/update`` payload dictionaries asynchronously."""


class AcpServer:
    """ACP v2 server that speaks JSON-RPC 2.0 over stdio (stdin→stdout).

    Reads NDJSON lines from stdin, dispatches to typed method handlers,
    and writes responses and notifications to stdout through a
    cancel-safe :class:`~.stdin_writer.StdinWriter`.

    Args:
        handle_prompt: An async generator callback that receives
            ``(session_id: str, prompt_text: str)`` and yields
            dictionaries for ``session/update`` notifications.
    """

    def __init__(self, handle_prompt: PromptHandler) -> None:
        self._handle_prompt_cb = handle_prompt
        self._sessions: dict[str, dict[str, Any]] = {}
        """sessionId → {cwd, mcpServers, state, prompt_task}"""
        self._writer: StdinWriter | None = None
        self._shutdown_event = asyncio.Event()

    # ── Public entry point ──────────────────────────────────────────

    async def serve(
        self,
        stdin: Any = None,
        stdout_writer: Any = None,
    ) -> None:
        """Run the read/dispatch/write loop until EOF or shutdown.

        Args:
            stdin: An async iterable of lines (defaults to a background-
                threaded reader on ``sys.stdin``).
            stdout_writer: The write endpoint (defaults to
                ``sys.stdout`` via an
                :class:`asyncio.StreamWriter`).
        """
        loop = asyncio.get_running_loop()
        self._writer = self._build_writer(stdout_writer)

        # Install signal handler for graceful shutdown.
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._request_shutdown)
            except NotImplementedError:
                pass  # Windows or non-main-thread — ignore.

        try:
            async for line in self._read_lines(stdin):
                if self._shutdown_event.is_set():
                    break
                await self._dispatch_line(line)
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    def _request_shutdown(self) -> None:
        """Signal the serve loop to exit gracefully."""
        logger.info("Shutdown signal received")
        self._shutdown_event.set()

    # ── Transport helpers ───────────────────────────────────────────

    def _build_writer(self, stdout_writer: Any) -> StdinWriter:
        """Create the cancel-safe :class:`StdinWriter` for stdout."""
        # If a real asyncio.StreamWriter was passed, use it directly.
        # Otherwise wrap sys.stdout in a simple writer.
        if stdout_writer is not None and hasattr(stdout_writer, "write") and hasattr(
            stdout_writer, "drain"
        ):
            return StdinWriter.spawn(stdout_writer)
        # Fallback: wrap sys.stdout.buffer in a synchronous writer.
        out = stdout_writer if stdout_writer is not None else sys.stdout

        def _sync_write(data: bytes) -> None:
            if hasattr(out, "buffer"):
                out.buffer.write(data)
                out.buffer.flush()
            else:
                out.write(data.decode("utf-8", "replace") if isinstance(data, bytes) else data)
                out.flush()

        return StdinWriter.spawn(_sync_write)

    async def _read_lines(self, stdin: Any):
        """Yield lines from stdin, one at a time.

        Runs ``readline`` in a thread executor so the event loop stays
        free to service ``session/cancel`` while a prompt is in flight.
        """
        loop = asyncio.get_running_loop()
        inp = stdin if stdin is not None else sys.stdin
        while not self._shutdown_event.is_set():
            line = await loop.run_in_executor(None, inp.readline)
            if line == "":
                return  # EOF
            line = line.strip()
            if not line:
                continue
            yield line

    async def _write(self, message: dict[str, Any]) -> None:
        """Write one JSON-RPC message to stdout (cancel-safe)."""
        assert self._writer is not None, "Writer not initialised"
        await self._writer.write_line(json.dumps(message, ensure_ascii=False))

    # ── Dispatch ────────────────────────────────────────────────────

    async def _dispatch_line(self, line: str) -> None:
        """Parse one NDJSON line and route to the appropriate handler."""
        try:
            msg = parse_ndjson(line)
        except json.JSONDecodeError:
            await self._write(
                _make_error(None, PARSE_ERROR, "Parse error")
            )
            return

        method = msg.get("method")
        msg_id = msg.get("id")
        params: dict[str, Any] = msg.get("params") or {}

        if method is None:
            # Not a request or notification — ignore.
            return

        # Handle $/cancel_request (JSON-RPC in-band cancellation).
        if method == "$/cancel_request":
            await self._handle_cancel_request(params)
            return

        # Route to the named method handler.
        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "session/new":
                result = await self._handle_session_new(params)
            elif method == "session/prompt":
                await self._handle_session_prompt(msg_id, params)
                return  # response is sent asynchronously
            elif method == "session/cancel":
                self._handle_session_cancel(params)
                return  # notification — no response
            elif method == "session/close":
                result = self._handle_session_close(params)
            elif method == "session/resume":
                result = await self._handle_session_resume(params)
            elif method == "session/delete":
                result = self._handle_session_delete(params)
            else:
                if msg_id is not None:
                    await self._write(
                        _make_error(
                            msg_id,
                            AcpError.METHOD_NOT_FOUND,
                            f"Method not found: {method}",
                        )
                    )
                return

            if msg_id is not None:
                await self._write(_make_response(msg_id, result))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error handling method %s", method)
            if msg_id is not None:
                await self._write(
                    _make_error(msg_id, AcpError.INTERNAL_ERROR, str(exc))
                )

    # ── Method handlers ─────────────────────────────────────────────

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Negotiate protocol version and advertise agent capabilities."""
        client_version = params.get("protocolVersion", ProtocolVersion)
        version = (
            min(client_version, ProtocolVersion)
            if isinstance(client_version, int)
            else ProtocolVersion
        )
        return {
            "protocolVersion": version,
            "capabilities": {
                "session": True,
                "mcp": {"stdio": True},
                "prompt": {"text": True},
            },
            "authMethods": [],
        }

    async def _handle_session_new(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a new session and return its ID.

        Stores the client's ``cwd`` and optional ``mcpServers`` list so
        they are available during the prompt lifecycle.
        """
        session_id = uuid.uuid4().hex
        cwd = params.get("cwd", os.getcwd())
        mcp_servers: list[dict[str, Any]] = params.get("mcpServers") or []
        self._sessions[session_id] = {
            "cwd": cwd,
            "mcpServers": mcp_servers,
            "state": "idle",
            "prompt_task": None,
        }
        logger.info("Session %s created (cwd=%s)", session_id[:8], cwd)
        return {"sessionId": session_id}

    async def _handle_session_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        """Resume an existing session."""
        session_id = params.get("sessionId", "")
        if not session_id or session_id not in self._sessions:
            raise ValueError(f"Session not found: {session_id}")
        return {"sessionId": session_id}

    async def _handle_session_prompt(
        self, msg_id: Any, params: dict[str, Any]
    ) -> None:
        """Start a prompt turn, streaming updates to the client.

        Fires the user-provided handler callback and forwards each
        yielded dict as a ``session/update`` notification.  On
        completion (or cancellation), sends the JSON-RPC response with
        ``stopReason``.
        """
        session_id = params.get("sessionId", "")
        if not session_id or session_id not in self._sessions:
            await self._write(
                _make_error(msg_id, AcpError.SESSION_NOT_FOUND, "Session not found")
            )
            return

        session = self._sessions[session_id]
        prompt_text = self._extract_prompt_text(params.get("prompt"))

        # Run the prompt in a tracked task so session/cancel can cancel it.
        task = asyncio.ensure_future(
            self._run_prompt(msg_id, session_id, prompt_text)
        )
        session["prompt_task"] = task
        session["state"] = "running"

        def _cleanup(_t: asyncio.Task[Any]) -> None:
            if session.get("prompt_task") is _t:
                session["prompt_task"] = None
                session["state"] = "idle"

        task.add_done_callback(_cleanup)

    async def _run_prompt(
        self, msg_id: Any, session_id: str, prompt_text: str
    ) -> None:
        """Execute the user's prompt handler and stream updates."""
        try:
            async for update_payload in self._handle_prompt_cb(
                session_id, prompt_text
            ):
                await self._write(
                    _make_notification(
                        "session/update",
                        {"sessionId": session_id, "update": update_payload},
                    )
                )
            # Normal completion.
            await self._write(
                _make_response(msg_id, {"stopReason": StopReason.END_TURN.value})
            )
        except asyncio.CancelledError:
            logger.info("Prompt cancelled for session %s", session_id[:8])
            await self._write(
                _make_response(msg_id, {"stopReason": StopReason.CANCELLED.value})
            )
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Prompt failed for session %s", session_id[:8])
            await self._write(
                _make_error(msg_id, AcpError.INTERNAL_ERROR, str(exc))
            )

    def _handle_session_cancel(self, params: dict[str, Any]) -> None:
        """Cancel an in-flight prompt turn (notification — no response)."""
        session_id = params.get("sessionId", "")
        session = self._sessions.get(session_id)
        if session is None:
            return
        task = session.get("prompt_task")
        if task is not None and not task.done():
            logger.info("Cancelling prompt for session %s", session_id[:8])
            task.cancel()

    def _handle_session_close(self, params: dict[str, Any]) -> dict[str, Any]:
        """Tear down session state."""
        session_id = params.get("sessionId", "")
        if session_id in self._sessions:
            task = self._sessions[session_id].get("prompt_task")
            if task is not None and not task.done():
                task.cancel()
            del self._sessions[session_id]
            logger.info("Session %s closed", session_id[:8])
        return {"sessionId": session_id}

    def _handle_session_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        """Delete a session and its data."""
        return self._handle_session_close(params)

    async def _handle_cancel_request(self, params: dict[str, Any]) -> None:
        """Handle ``$/cancel_request`` (JSON-RPC in-band cancellation).

        Cancels the future associated with the given request ID.
        """
        # In a fuller implementation this would tie request IDs to
        # futures.  For the skeleton, we log and ignore.
        req_id = params.get("id")
        logger.debug("Cancel request received for id=%s", req_id)

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_prompt_text(prompt: Any) -> str:
        """Extract a plain-text prompt from an ACP prompt payload.

        ACP prompts may be a plain string or a list of content blocks
        (text, image, resource, etc.).  We extract the first text block.
        """
        if isinstance(prompt, str):
            return prompt
        if isinstance(prompt, list):
            for block in prompt:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
        return ""

    # ── Shutdown ────────────────────────────────────────────────────

    async def _shutdown(self) -> None:
        """Cancel all in-flight prompts and close the writer."""
        for sid, session in list(self._sessions.items()):
            task = session.get("prompt_task")
            if task is not None and not task.done():
                task.cancel()
        self._sessions.clear()

        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None

        logger.info("ACP server shut down")