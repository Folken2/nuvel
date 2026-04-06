"""
Memory tools for the agent.

Provides tools for the agent to persist and recall information across sessions
using markdown file-based long-term memory.
"""

from google.adk.tools import FunctionTool

from ..state.memory import (
    append_core_memory,
    append_topic,
    delete_topic,
    list_topics,
    load_core_memory,
    load_topic,
    memory_stats,
    save_core_memory,
    save_topic,
)


def save_memory(
    content: str,
    topic: str = "",

) -> dict:
    """Save a piece of information to long-term memory.

    Use this to remember important facts, user preferences, project details,
    or anything that should persist across conversations. Information saved
    here will be available in future sessions.

    Args:
        content: The information to remember. Be concise and specific.
        topic: Optional topic category (e.g. "user-preferences", "project-setup").
               If empty, saves to core memory. If provided, saves to a
               topic-specific file.

    Returns:
        Status dict confirming the save.
    """
    if topic:
        return append_topic(topic, content)
    return append_core_memory(content)


def recall_memory(
    topic: str = "",

) -> dict:
    """Recall information from long-term memory.

    Use this to retrieve previously saved information. Call without a topic
    to get core memory, or specify a topic to get topic-specific memory.

    Args:
        topic: Optional topic to recall. If empty, returns core memory.
               Use memory_status() to see all available topics.

    Returns:
        Dict with the memory content.
    """
    if topic:
        content = load_topic(topic)
        if not content:
            return {
                "status": "ok",
                "content": "",
                "message": f"No memory found for topic '{topic}'.",
                "available_topics": list_topics(),
            }
        return {"status": "ok", "topic": topic, "content": content}

    content = load_core_memory()
    if not content:
        return {
            "status": "ok",
            "content": "",
            "message": "No core memory saved yet. Use save_memory() to store information.",
        }
    return {"status": "ok", "content": content}


def update_memory(
    content: str,
    topic: str = "",

) -> dict:
    """Replace the full content of a memory file.

    Use this when you need to reorganize, summarize, or rewrite memory
    rather than just appending. This overwrites the entire file.

    Args:
        content: The new full content for the memory file.
        topic: Optional topic. If empty, updates core memory.

    Returns:
        Status dict confirming the update.
    """
    if topic:
        return save_topic(topic, content)
    return save_core_memory(content)


def forget_topic(
    topic: str,

) -> dict:
    """Delete a topic memory file entirely.

    Use this to clean up topics that are no longer relevant.

    Args:
        topic: The topic to delete.

    Returns:
        Status dict confirming deletion.
    """
    return delete_topic(topic)


def memory_status(

) -> dict:
    """Get memory usage statistics.

    Shows how much memory is used, available topics, and size limits.

    Returns:
        Dict with memory statistics.
    """
    return memory_stats()


# ── Tool exports ───────────────────────────────────────────────────────

memory_tool_list = [
    FunctionTool(save_memory),
    FunctionTool(recall_memory),
    FunctionTool(update_memory),
    FunctionTool(forget_topic),
    FunctionTool(memory_status),
]
