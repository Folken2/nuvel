"""Agent Client Protocol server for {{agent_name}}.

Implements the agent side of ACP (https://agentclientprotocol.com) over the
stdio JSON-RPC transport. Handled client→agent methods:

    initialize        negotiate protocol version + capabilities
    authenticate      no-op (this agent advertises no auth methods)
    session/new       create a session, return its id
    session/prompt    run a turn; stream session/update notifications,
                      then return a stopReason
    session/cancel    (notification) cancel the in-flight turn

While a turn runs the server emits ``session/update`` notifications:
``agent_message_chunk`` for text, ``agent_thought_chunk`` for reasoning,
and ``tool_call`` / ``tool_call_update`` for tool activity.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

from . import PROTOCOL_VERSION
from .jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    StdioTransport,
    make_error,
    make_notification,
    make_response,
)
from .runtime import AgentRuntime, AgentUpdate, jsonable

logger = logging.getLogger(__name__)

# One ACP process serves one principal; sessions namespace the conversations.
DEFAULT_USER_ID = os.getenv("ACP_USER_ID", "acp-user")


def _blocks_to_text(blocks: Any) -> str:
    """Flatten an ACP prompt (list of content blocks) into plain text."""
    if not isinstance(blocks, list):
        return ""
    out: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            out.append(block.get("text", ""))
        elif btype in ("resource", "resource_link"):
            resource = block.get("resource")
            if isinstance(resource, dict) and isinstance(resource.get("text"), str):
                out.append(resource["text"])
    return "\n".join(t for t in out if t)


class ACPAgent:
    """Drives the ACP request/response loop over a :class:`StdioTransport`."""

    def __init__(self, transport: StdioTransport) -> None:
        self._t = transport
        self._runtime = AgentRuntime()
        # sessionId -> the asyncio.Task running its current prompt turn.
        self._active: dict[str, asyncio.Task] = {}

    # ── main loop ────────────────────────────────────────────────────

    async def serve(self) -> None:
        while True:
            msg = await self._t.read()
            if msg is None:
                break  # client closed the pipe
            await self._dispatch(msg)

    async def _dispatch(self, msg: dict) -> None:
        method = msg.get("method")
        msg_id = msg.get("id")

        # A message with no method is a response to something we sent. We
        # never send agent→client requests, so there is nothing to correlate.
        if method is None:
            return

        try:
            if method == "initialize":
                await self._t.write(
                    make_response(msg_id, self._handle_initialize(msg.get("params") or {}))
                )
            elif method == "authenticate":
                await self._t.write(make_response(msg_id, {}))
            elif method == "session/new":
                result = await self._handle_new_session(msg.get("params") or {})
                await self._t.write(make_response(msg_id, result))
            elif method == "session/load":
                # loadSession capability is not advertised.
                await self._t.write(
                    make_error(msg_id, METHOD_NOT_FOUND, "session/load is not supported")
                )
            elif method == "session/prompt":
                self._start_prompt(msg_id, msg.get("params") or {})
            elif method == "session/cancel":
                self._handle_cancel(msg.get("params") or {})
            else:
                if msg_id is not None:
                    await self._t.write(
                        make_error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
                    )
        except Exception as exc:  # noqa: BLE001 — surface as JSON-RPC error
            logger.exception("Error handling %s", method)
            if msg_id is not None:
                await self._t.write(make_error(msg_id, INTERNAL_ERROR, str(exc)))

    # ── handlers ─────────────────────────────────────────────────────

    def _handle_initialize(self, params: dict) -> dict:
        client_version = params.get("protocolVersion", PROTOCOL_VERSION)
        version = (
            min(client_version, PROTOCOL_VERSION)
            if isinstance(client_version, int)
            else PROTOCOL_VERSION
        )
        return {
            "protocolVersion": version,
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {
                    "image": False,
                    "audio": False,
                    "embeddedContext": True,
                },
            },
            "authMethods": [],
        }

    async def _handle_new_session(self, params: dict) -> dict:
        session_id = uuid.uuid4().hex
        await self._runtime.ensure_session(DEFAULT_USER_ID, session_id)
        return {"sessionId": session_id}

    def _start_prompt(self, msg_id: Any, params: dict) -> None:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str):
            asyncio.ensure_future(
                self._t.write(make_error(msg_id, INVALID_PARAMS, "Missing sessionId"))
            )
            return

        task = asyncio.ensure_future(self._run_prompt(msg_id, session_id, params))
        self._active[session_id] = task
        task.add_done_callback(lambda _t, sid=session_id: self._active.pop(sid, None))

    async def _run_prompt(self, msg_id: Any, session_id: str, params: dict) -> None:
        text = _blocks_to_text(params.get("prompt"))
        try:
            await self._runtime.ensure_session(DEFAULT_USER_ID, session_id)
            async for update in self._runtime.run_turn(
                DEFAULT_USER_ID, session_id, text
            ):
                await self._emit_update(session_id, update)
        except asyncio.CancelledError:
            await self._t.write(make_response(msg_id, {"stopReason": "cancelled"}))
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Prompt turn failed")
            await self._t.write(make_error(msg_id, INTERNAL_ERROR, str(exc)))
            return
        await self._t.write(make_response(msg_id, {"stopReason": "end_turn"}))

    def _handle_cancel(self, params: dict) -> None:
        session_id = params.get("sessionId")
        task = self._active.get(session_id) if isinstance(session_id, str) else None
        if task is not None and not task.done():
            task.cancel()

    # ── session/update emitters ──────────────────────────────────────

    async def _emit_update(self, session_id: str, update: AgentUpdate) -> None:
        payload = self._update_payload(update)
        if payload is None:
            return
        await self._t.write(
            make_notification(
                "session/update", {"sessionId": session_id, "update": payload}
            )
        )

    @staticmethod
    def _update_payload(update: AgentUpdate) -> dict | None:
        if update.kind == "text":
            return {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": update.text},
            }
        if update.kind == "thought":
            return {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": update.text},
            }
        if update.kind == "tool_call":
            return {
                "sessionUpdate": "tool_call",
                "toolCallId": update.tool_call_id or update.tool_name,
                "title": update.tool_name,
                "kind": "other",
                "status": "in_progress",
                "rawInput": update.tool_args,
            }
        if update.kind == "tool_result":
            return {
                "sessionUpdate": "tool_call_update",
                "toolCallId": update.tool_call_id or update.tool_name,
                "status": "completed",
                "rawOutput": jsonable(update.tool_result),
            }
        return None
