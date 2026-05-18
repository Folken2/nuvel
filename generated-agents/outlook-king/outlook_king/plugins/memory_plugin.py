"""Memory plugin for outlook-king.

Mirrors the session's ``user_id`` into ``state['user_id']`` before each
agent invocation so memory tools (and any future user-scoped tools) can
read it ergonomically from the ToolContext.

The backend's FastAPI dependency resolves the email header to a surrogate
user_id via NeonMemoryService.upsert_user and passes it to
Runner.run_async; this plugin just makes it visible to tools.
"""
from __future__ import annotations

import logging
from typing import Optional

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

logger = logging.getLogger(__name__)


class MemoryPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="memory")

    async def before_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> Optional[types.Content]:
        """Copy the session's user_id into state so tools can read it.

        Returns None to let the agent proceed normally — we never want to
        short-circuit, only to populate state.
        """
        del agent  # not needed; the plugin runs for every agent.
        user_id = callback_context.user_id
        if not user_id:
            logger.warning("No user_id on session; memory tools will fail")
            return None

        # Mutating callback_context.state queues an EventActions state_delta
        # under the hood — no need to construct an Event manually.
        if callback_context.state.get("user_id") != user_id:
            callback_context.state["user_id"] = user_id
        return None
