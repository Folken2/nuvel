"""
Audit hook — logs every tool call before it runs.

Returning {} (or no decision) lets the call proceed. To block, return:
    {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                             "permissionDecision": "deny",
                             "permissionDecisionReason": "..."}}
See the claude-agent-sdk hook docs for the full schema.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("audit")


async def audit_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    tool_name = input_data.get("tool_name", "<unknown>")
    tool_input = input_data.get("tool_input", {})
    logger.info("tool_call", extra={
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": tool_use_id,
    })
    return {}
