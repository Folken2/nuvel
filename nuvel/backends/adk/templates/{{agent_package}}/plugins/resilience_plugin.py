"""
Resilience plugin: circuit breaker + rate limiting for tool calls.

Runs early in the plugin chain (before cache) to fail fast
when the system is overloaded or a backend service is down.

Configure PROTECTED_TOOLS env var (comma-separated) to specify which
tools are guarded by the circuit breaker.
"""

import logging
import os
from typing import Any, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from ..utils.resilience import db_circuit, tool_rate_limiter

logger = logging.getLogger(__name__)

# Tools protected by the circuit breaker (configure via env var)
_PROTECTED_TOOLS: set[str] = set(
    t.strip() for t in os.getenv("PROTECTED_TOOLS", "").split(",") if t.strip()
)


class ResiliencePlugin(BasePlugin):
    """Applies circuit breaker and rate limiting to tool calls."""

    def __init__(self) -> None:
        super().__init__(name="resilience")

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Optional[dict]:
        # Rate limit all tool calls
        if not tool_rate_limiter.allow():
            logger.warning("Rate limit exceeded for tool: %s", tool.name)
            return {
                "status": "error",
                "error": "Rate limit exceeded",
                "message": (
                    "Too many requests. Please wait a moment before trying again."
                ),
            }

        # Circuit breaker for protected tools
        if tool.name in _PROTECTED_TOOLS:
            if not db_circuit.allow_request():
                return {
                    "status": "error",
                    "error": "Service temporarily unavailable",
                    "message": (
                        "The service is temporarily unavailable due to repeated errors. "
                        "It will be retried automatically in a few seconds."
                    ),
                }

        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict,
    ) -> Optional[dict]:
        # Track protected tool outcomes for circuit breaker
        if tool.name in _PROTECTED_TOOLS:
            if isinstance(result, dict) and result.get("status") == "error":
                error = result.get("error", "")
                # Only trip on connection/infrastructure errors, not user query errors
                if any(kw in str(error).lower() for kw in [
                    "connection", "timeout", "pool", "refused", "unavailable",
                ]):
                    db_circuit.record_failure()
            else:
                db_circuit.record_success()

        return None
