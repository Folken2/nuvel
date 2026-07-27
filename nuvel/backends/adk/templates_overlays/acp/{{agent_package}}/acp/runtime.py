"""Shared ADK runtime helpers for the ACP adapter and the local CLI.

Both entrypoints need the same three things: a Runner wired with the full
plugin chain (via ``AgentHarness``), a session to run against, and a way to
turn the stream of ADK events into simple, transport-agnostic updates.
Keeping that here means the protocol server (``acp/server.py``) and the
terminal CLI (``cli.py``) stay thin and behave identically to the server.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..agent import root_agent
from ..harness import AgentHarness
from .permission import permission_callback_from_env

logger = logging.getLogger(__name__)

APP_NAME = "{{agent_name}}"


@dataclass
class AgentUpdate:
    """A single transport-agnostic event emitted while a turn runs."""

    kind: str  # "text" | "thought" | "tool_call" | "tool_result"
    text: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: Any = None


def jsonable(value: Any) -> Any:
    """Best-effort coercion of a tool result into JSON-serializable data."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _translate_event(event: Any) -> list[AgentUpdate]:
    """Map one ADK ``Event`` into zero or more :class:`AgentUpdate`."""
    updates: list[AgentUpdate] = []
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return updates

    for part in parts:
        text = getattr(part, "text", None)
        if text:
            kind = "thought" if getattr(part, "thought", False) else "text"
            updates.append(AgentUpdate(kind=kind, text=text))

        fc = getattr(part, "function_call", None)
        if fc is not None:
            updates.append(
                AgentUpdate(
                    kind="tool_call",
                    tool_call_id=getattr(fc, "id", "") or "",
                    tool_name=getattr(fc, "name", "") or "",
                    tool_args=dict(getattr(fc, "args", None) or {}),
                )
            )

        fr = getattr(part, "function_response", None)
        if fr is not None:
            updates.append(
                AgentUpdate(
                    kind="tool_result",
                    tool_call_id=getattr(fr, "id", "") or "",
                    tool_name=getattr(fr, "name", "") or "",
                    tool_result=getattr(fr, "response", None),
                )
            )

    return updates


class AgentRuntime:
    """Owns the Runner(s) and exposes session + turn helpers.

    Construct once and reuse across turns/sessions. Runners are built via
    ``AgentHarness`` so they share the process-wide session/artifact services
    and the full plugin chain with the FastAPI server.

    Most sessions run against a single shared ``_default_runner``. A session
    that supplies *extra tools* (editor-injected MCP servers, or the fs
    bridge) gets its own Runner wrapping a copy of ``root_agent`` with those
    tools appended — see :meth:`ensure_session`.
    """

    def __init__(self) -> None:
        self.app_name = APP_NAME
        self._harness = AgentHarness.get(APP_NAME)
        self._default_runner = self._harness.build_runner(agent=root_agent)
        # session_id -> a per-session Runner (only when the session added tools).
        self._runners: dict[str, Any] = {}
        # session_id -> the extra toolsets/tools to close on shutdown.
        self._session_tools: dict[str, list] = {}

    @property
    def runner(self):
        return self._default_runner

    def _runner_for(self, session_id: str):
        return self._runners.get(session_id, self._default_runner)

    def _configure_runner(
        self, session_id: str, extra_tools: list, before_tool_callback
    ) -> None:
        """Build a per-session Runner for this session's extra tools / gate.

        Copies ``root_agent`` with ``extra_tools`` appended and/or a
        ``before_tool_callback`` (the ACP permission gate) applied. On any
        failure the session falls back to the shared default runner rather
        than aborting.
        """
        if session_id in self._runners:
            return
        if not extra_tools and before_tool_callback is None:
            return
        try:
            update: dict = {}
            if extra_tools:
                base_tools = list(getattr(root_agent, "tools", None) or [])
                update["tools"] = base_tools + list(extra_tools)
            if before_tool_callback is not None:
                update["before_tool_callback"] = before_tool_callback
            agent = root_agent.model_copy(update=update)
            self._runners[session_id] = self._harness.build_runner(agent=agent)
            if extra_tools:
                self._session_tools[session_id] = list(extra_tools)
        except Exception as exc:  # noqa: BLE001 — degrade to the default runner
            logger.warning(
                "Could not build a per-session runner for %s (%s); "
                "falling back to the default agent.",
                session_id,
                exc,
            )

    async def ensure_session(
        self,
        user_id: str,
        session_id: str,
        *,
        extra_tools: list | None = None,
        permission_requester=None,
    ) -> None:
        """Create the session if it doesn't exist; wire per-session extras once.

        ``extra_tools`` (MCP toolsets + fs bridge) and, when
        ``permission_requester`` is given, the ACP HITL gate (built from
        ``ACP_PERMISSION_*`` env, chaining any existing ``before_tool_callback``)
        are honored on the first call for a session; later calls (e.g. the
        defensive re-ensure before a prompt) leave the runner in place.
        """
        callback = None
        if permission_requester is not None:
            callback = permission_callback_from_env(
                session_id,
                permission_requester,
                chained=getattr(root_agent, "before_tool_callback", None),
            )
        if extra_tools or callback is not None:
            self._configure_runner(session_id, extra_tools or [], callback)
        svc = self._runner_for(session_id).session_service
        existing = await svc.get_session(
            app_name=self.app_name, user_id=user_id, session_id=session_id
        )
        if existing is None:
            await svc.create_session(
                app_name=self.app_name, user_id=user_id, session_id=session_id
            )

    async def run_turn(
        self, user_id: str, session_id: str, text: str
    ) -> AsyncIterator[AgentUpdate]:
        """Run one prompt turn, yielding updates as the agent produces them."""
        from google.genai import types

        message = types.Content(role="user", parts=[types.Part(text=text)])
        async for event in self._runner_for(session_id).run_async(
            user_id=user_id, session_id=session_id, new_message=message
        ):
            for update in _translate_event(event):
                yield update

    async def aclose(self) -> None:
        """Close any per-session toolsets (e.g. MCP subprocesses)."""
        for tools in self._session_tools.values():
            for tool in tools:
                close = getattr(tool, "close", None)
                if close is None:
                    continue
                try:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
                except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                    logger.warning("Error closing session tool %r: %s", tool, exc)
        self._session_tools.clear()
        self._runners.clear()
