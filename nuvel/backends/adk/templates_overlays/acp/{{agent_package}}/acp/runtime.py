"""Shared ADK runtime helpers for the ACP adapter and the local CLI.

Both entrypoints need the same three things: a Runner wired with the full
plugin chain (via ``AgentHarness``), a session to run against, and a way to
turn the stream of ADK events into simple, transport-agnostic updates.
Keeping that here means the protocol server (``acp/server.py``) and the
terminal CLI (``cli.py``) stay thin and behave identically to the server.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..agent import root_agent
from ..harness import AgentHarness

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
    """Owns the Runner and exposes session + turn helpers.

    Construct once and reuse across turns/sessions. The Runner is built via
    ``AgentHarness`` so it shares the process-wide session/artifact services
    and the full plugin chain with the FastAPI server.
    """

    def __init__(self) -> None:
        self.app_name = APP_NAME
        self._harness = AgentHarness.get(APP_NAME)
        self._runner = self._harness.build_runner(agent=root_agent)

    @property
    def runner(self):
        return self._runner

    async def ensure_session(self, user_id: str, session_id: str) -> None:
        """Create the session if it doesn't already exist."""
        svc = self._runner.session_service
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
        async for event in self._runner.run_async(
            user_id=user_id, session_id=session_id, new_message=message
        ):
            for update in _translate_event(event):
                yield update
