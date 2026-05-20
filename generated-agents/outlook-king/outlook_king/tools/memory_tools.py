"""Memory tools for outlook-king.

Same five tools the agent saw before — same names, same parameters —
but now backed by NeonMemoryService over Postgres instead of markdown
files. ``user_id`` comes from session state, populated by the memory
plugin before each invocation.
"""
from __future__ import annotations

from google.adk.tools import FunctionTool, ToolContext

from ..state.memory_singleton import get_memory_service


def _resolve_user_id(tool_context: ToolContext) -> str:
    user_id = tool_context.state.get("user_id")
    if not user_id:
        raise RuntimeError(
            "tool_context.state['user_id'] is missing — memory_plugin "
            "must run before any memory tool"
        )
    return user_id


async def save_memory(content: str, topic: str = "", *, tool_context: ToolContext) -> dict:
    """Save a piece of information to long-term memory.

    Use this to remember important facts, user preferences, project details,
    or anything that should persist across conversations.

    Args:
        content: The information to remember. Be concise and specific.
        topic: Optional topic category (e.g. "user-preferences"). Empty
               string saves to the default "core" topic.

    Returns:
        Status dict confirming the save.
    """
    user_id = _resolve_user_id(tool_context)
    return await get_memory_service().save(user_id, content, topic or "core")


async def recall_memory(topic: str = "", *, tool_context: ToolContext) -> dict:
    """Recall information from long-term memory.

    Args:
        topic: Optional topic to recall. Empty string returns core memory.
               Use memory_status() to see all available topics.

    Returns:
        Dict with the memory content.
    """
    user_id = _resolve_user_id(tool_context)
    return await get_memory_service().recall(user_id, topic or None)


async def update_memory(content: str, topic: str = "", *, tool_context: ToolContext) -> dict:
    """Replace all rows for a topic with a single consolidated entry.

    Use when you need to reorganize, summarize, or rewrite memory rather
    than just append.

    Args:
        content: The new consolidated content.
        topic: Optional topic. Empty string updates core memory.

    Returns:
        Status dict confirming the update.
    """
    user_id = _resolve_user_id(tool_context)
    return await get_memory_service().update(user_id, content, topic or "core")


async def forget_topic(topic: str, *, tool_context: ToolContext) -> dict:
    """Delete every row in a topic. Use to clean up obsolete categories.

    Args:
        topic: The topic to delete.

    Returns:
        Status dict with rowcount.
    """
    user_id = _resolve_user_id(tool_context)
    return await get_memory_service().forget_topic(user_id, topic)


async def memory_status(*, tool_context: ToolContext) -> dict:
    """Get memory usage statistics: total rows and per-topic counts.

    Returns:
        Dict with row counts.
    """
    user_id = _resolve_user_id(tool_context)
    return await get_memory_service().stats(user_id)


# ── Tool exports ───────────────────────────────────────────────────────

memory_tool_list = [
    FunctionTool(save_memory),
    FunctionTool(recall_memory),
    FunctionTool(update_memory),
    FunctionTool(forget_topic),
    FunctionTool(memory_status),
]
