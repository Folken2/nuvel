"""
Cache plugin for the agent.

Caches successful tool responses with TTL expiration.
Uses tool_context.state for session-scoped persistence.

Configure CACHEABLE_TOOLS to specify which tools should be cached.
"""

import logging
from typing import Any, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from ..state.query_cache import cache_get, cache_set

logger = logging.getLogger(__name__)

CACHEABLE_TOOLS: set[str] = set()


class CachePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="cache")

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Optional[dict]:
        """Return cached result if available."""
        if tool.name not in CACHEABLE_TOOLS:
            return None

        cached = cache_get(tool_context.state, tool.name, tool_args)
        if cached:
            logger.info("Cache HIT: %s", tool.name)
            return cached

        logger.debug("Cache MISS: %s", tool.name)
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict,
    ) -> Optional[dict]:
        """Store successful responses in cache."""
        if tool.name not in CACHEABLE_TOOLS:
            return None

        if isinstance(result, dict) and result.get("status") != "error" and not result.get("_cached"):
            try:
                cache_set(tool_context.state, tool.name, tool_args, result)
                logger.debug("Cached: %s", tool.name)
            except Exception as e:
                logger.warning("Failed to cache: %s", e)

        return None
