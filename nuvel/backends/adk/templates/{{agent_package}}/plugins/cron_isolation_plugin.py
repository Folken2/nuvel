"""
Cron isolation plugin: headless approval policy for scheduled runs.

During a scheduled cron run there is no user present to approve tool calls, so
this ``before_tool_callback`` enforces ``NUVEL_CRON_HEADLESS_POLICY``:

  * ``allow-shell`` (default) — shell/bin tools run inside the isolated cron
    scope and are auto-allowed; every other tool (HTTP calls, DB writes, ...)
    is auto-denied with a logged reason.
  * ``deny-all``   — every tool is denied.
  * ``allow-all``  — every tool is allowed (opts out of the gate).

Outside a cron run the plugin is inert (``active_cron_run()`` is ``None``), so
ordinary interactive turns are never affected.
"""

import logging
from typing import Any, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from ..cron.isolation import active_cron_run, evaluate_headless_tool

logger = logging.getLogger(__name__)


class CronIsolationPlugin(BasePlugin):
    """Applies the headless approval policy to tools during cron runs."""

    def __init__(self) -> None:
        super().__init__(name="cron_isolation")

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Optional[dict]:
        run = active_cron_run()
        if run is None:
            # Not a scheduled run — leave every other approval path untouched.
            return None

        allowed, reason = evaluate_headless_tool(tool.name)
        if allowed:
            return None

        logger.warning(
            "cron: headless policy denied tool %s (job %s): %s",
            tool.name,
            run.job_id,
            reason,
        )
        return {
            "status": "error",
            "error": "headless_denied",
            "message": reason,
            "headless_denied": True,
        }
