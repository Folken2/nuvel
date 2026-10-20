"""
Cache plugin for the Meta Agent.

Caches successful tool responses with TTL expiration.
Uses tool_context.state for session-scoped persistence.
"""

import logging
from typing import Any, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from ..state.query_cache import CACHE_STATE_KEY, cache_get, cache_set

logger = logging.getLogger(__name__)

CACHEABLE_TOOLS = {"read_file", "list_files", "validate_agent"}

# Tools that change files on disk. Once one runs, a cached read_file /
# list_files / validate_agent result may describe files that no longer exist
# in that form, so the session's cache is dropped.
INVALIDATING_TOOLS = {"write_file", "scaffold_agent", "install_skill"}


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
        if tool.name in INVALIDATING_TOOLS:
            if tool_context.state.get(CACHE_STATE_KEY):
                tool_context.state[CACHE_STATE_KEY] = {}
                logger.debug("Cache cleared after %s", tool.name)
            return None

        if tool.name not in CACHEABLE_TOOLS:
            return None

        if isinstance(result, dict) and result.get("status") != "error" and not result.get("_cached"):
            try:
                cache_set(tool_context.state, tool.name, tool_args, result)
                logger.debug("Cached: %s", tool.name)
            except Exception as e:
                logger.warning("Failed to cache: %s", e)

        return None
