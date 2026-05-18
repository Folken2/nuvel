"""Memory plugin for outlook-king.

Mirrors ``session.user_id`` into ``state['user_id']`` before each agent
invocation so memory tools (and any future user-scoped tools) can read
it ergonomically from the ToolContext.

The backend's FastAPI dependency resolves the email header to a
surrogate user_id via NeonMemoryService.upsert_user and passes it to
Runner.run_async; this plugin just makes it visible to tools.
"""
from __future__ import annotations

import logging
from typing import Optional

from google.adk.events import Event, EventActions
from google.adk.plugins.base_plugin import BasePlugin

logger = logging.getLogger(__name__)


class MemoryPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="memory")

    async def before_agent_callback(
        self,
        *,
        invocation_context,
        **kwargs,
    ) -> Optional[Event]:
        """Copy the session's user_id into state so tools can read it."""
        session = invocation_context.session
        if not session or not session.user_id:
            logger.warning("No user_id on session; memory tools will fail")
            return None

        # Already mirrored? Skip the redundant state write.
        if session.state.get("user_id") == session.user_id:
            return None

        return Event(
            invocation_id=invocation_context.invocation_id,
            author="memory_plugin",
            actions=EventActions(state_delta={"user_id": session.user_id}),
        )
