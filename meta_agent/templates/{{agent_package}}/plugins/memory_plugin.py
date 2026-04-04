"""
Memory plugin for the agent.

Logs memory loading events and provides lifecycle hooks for the
markdown file-based long-term memory system.

Memory is loaded into the system prompt via the InstructionProvider
callback in prompt/instructions.py. This plugin complements that by:
- Logging memory load events for observability
- Tracking memory usage in session state for tools to reference
"""

import logging
import os
from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.models import LlmRequest
from google.adk.plugins.base_plugin import BasePlugin

from ..state.memory import load_all_memory, memory_stats

logger = logging.getLogger(__name__)

_MEMORY_ENABLED = os.getenv("MEMORY_ENABLED", "true").lower() in ("true", "1", "yes")


class MemoryPlugin(BasePlugin):
    """Plugin that tracks memory loading for observability."""

    def __init__(self) -> None:
        super().__init__(name="memory")

    async def before_agent_callback(
        self,
        *,
        invocation_context,
        **kwargs,
    ) -> Optional[dict]:
        """Log memory stats at the start of each agent invocation."""
        if not _MEMORY_ENABLED:
            return None

        try:
            stats = memory_stats()
            core_size = stats["core_memory_size"]
            topic_count = stats["topic_count"]

            if core_size > 0 or topic_count > 0:
                logger.info(
                    "Memory loaded: core=%d chars, topics=%d",
                    core_size,
                    topic_count,
                )
            else:
                logger.debug("No long-term memory found")

        except Exception as e:
            logger.warning("Memory plugin error: %s", e)

        return None
