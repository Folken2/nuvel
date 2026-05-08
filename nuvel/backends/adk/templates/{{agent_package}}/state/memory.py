"""
Markdown file-based long-term memory for the agent.

Provides persistent, human-readable memory that survives across sessions.
Inspired by Claude Code's CLAUDE.md and similar approaches.

Memory is stored as markdown files in a configurable directory (default: ./memory/).
The agent can save learnings, user preferences, and facts that persist
beyond a single conversation session.

Structure:
    memory/
        AGENT_MEMORY.md          # Core memory (always loaded into prompt)
        topics/
            <topic-slug>.md      # Topic-specific memory files
"""

import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────

CORE_MEMORY_FILE = "AGENT_MEMORY.md"
TOPICS_DIR = "topics"


def _memory_dir() -> Path:
    """Return configured memory directory (reads env var on each call)."""
    # Prefer config/paths.memory_dir() so deployment volumes work uniformly
    # with SOUL/SKILLS overrides; fall back to the legacy default if the
    # paths module is missing for any reason.
    try:
        from ..config.paths import memory_dir as _resolved
        return _resolved()
    except Exception:
        return Path(os.getenv("MEMORY_DIR", "./memory"))


def _max_core_memory_size() -> int:
    return int(os.getenv("MEMORY_MAX_CORE_SIZE", "10000"))


def _max_topic_size() -> int:
    return int(os.getenv("MEMORY_MAX_TOPIC_SIZE", "5000"))


# ── Helpers ────────────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")[:60]


def _ensure_memory_dir() -> Path:
    """Create memory directory structure if needed."""
    d = _memory_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / TOPICS_DIR).mkdir(exist_ok=True)
    return d


def _timestamp() -> str:
    """ISO timestamp for memory entries."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── Core Memory (AGENT_MEMORY.md) ─────────────────────────────────────


def load_core_memory() -> str:
    """Load the core memory file content.

    Returns empty string if file doesn't exist yet.
    """
    core_file = _memory_dir() / CORE_MEMORY_FILE
    if not core_file.is_file():
        return ""
    try:
        content = core_file.read_text(encoding="utf-8").strip()
        logger.debug("Loaded core memory (%d chars)", len(content))
        return content
    except Exception as e:
        logger.warning("Failed to read core memory: %s", e)
        return ""


def save_core_memory(content: str) -> dict:
    """Write content to the core memory file.

    Args:
        content: Full markdown content to write.

    Returns:
        Status dict with result.
    """
    max_size = _max_core_memory_size()
    if len(content) > max_size:
        return {
            "status": "error",
            "message": f"Content exceeds max size ({len(content)}/{max_size} chars). "
                       "Summarize or split into topics.",
        }

    base = _ensure_memory_dir()
    core_file = base / CORE_MEMORY_FILE

    try:
        core_file.write_text(content, encoding="utf-8")
        logger.info("Saved core memory (%d chars)", len(content))
        return {"status": "ok", "file": str(core_file), "size": len(content)}
    except Exception as e:
        logger.error("Failed to save core memory: %s", e)
        return {"status": "error", "message": str(e)}


def append_core_memory(entry: str) -> dict:
    """Append an entry to the core memory file.

    Adds a timestamped entry at the end. Creates the file if needed.

    Args:
        entry: Text to append (will be added with timestamp).

    Returns:
        Status dict with result.
    """
    existing = load_core_memory()
    timestamped = f"\n\n- [{_timestamp()}] {entry.strip()}"
    new_content = (existing + timestamped).strip()

    max_size = _max_core_memory_size()
    if len(new_content) > max_size:
        return {
            "status": "error",
            "message": f"Core memory would exceed max size ({len(new_content)}/{max_size} chars). "
                       "Consider summarizing old entries or moving details to topic files.",
        }

    return save_core_memory(new_content)


# ── Topic Memory ──────────────────────────────────────────────────────


def list_topics() -> list[str]:
    """List all topic memory files (without .md extension)."""
    topics_dir = _memory_dir() / TOPICS_DIR
    if not topics_dir.is_dir():
        return []
    return sorted(
        f.stem for f in topics_dir.glob("*.md") if f.is_file()
    )


def load_topic(topic: str) -> str:
    """Load a topic memory file.

    Args:
        topic: Topic name (will be slugified).

    Returns:
        Content string, or empty string if not found.
    """
    slug = _slugify(topic)
    if not slug:
        return ""
    topic_file = _memory_dir() / TOPICS_DIR / f"{slug}.md"
    if not topic_file.is_file():
        return ""
    try:
        content = topic_file.read_text(encoding="utf-8").strip()
        logger.debug("Loaded topic '%s' (%d chars)", slug, len(content))
        return content
    except Exception as e:
        logger.warning("Failed to read topic '%s': %s", slug, e)
        return ""


def save_topic(topic: str, content: str) -> dict:
    """Write content to a topic memory file.

    Args:
        topic: Topic name (will be slugified).
        content: Full markdown content.

    Returns:
        Status dict with result.
    """
    slug = _slugify(topic)
    if not slug:
        return {"status": "error", "message": "Invalid topic name."}

    max_size = _max_topic_size()
    if len(content) > max_size:
        return {
            "status": "error",
            "message": f"Content exceeds max topic size ({len(content)}/{max_size} chars).",
        }

    base = _ensure_memory_dir()
    topic_file = base / TOPICS_DIR / f"{slug}.md"

    try:
        topic_file.write_text(content, encoding="utf-8")
        logger.info("Saved topic '%s' (%d chars)", slug, len(content))
        return {"status": "ok", "topic": slug, "file": str(topic_file), "size": len(content)}
    except Exception as e:
        logger.error("Failed to save topic '%s': %s", slug, e)
        return {"status": "error", "message": str(e)}


def append_topic(topic: str, entry: str) -> dict:
    """Append an entry to a topic memory file.

    Args:
        topic: Topic name (will be slugified).
        entry: Text to append (will be added with timestamp).

    Returns:
        Status dict with result.
    """
    existing = load_topic(topic)
    slug = _slugify(topic)

    if not existing:
        # Create new topic with header
        new_content = f"# {topic.strip().title()}\n\n- [{_timestamp()}] {entry.strip()}"
    else:
        new_content = f"{existing}\n\n- [{_timestamp()}] {entry.strip()}"

    max_size = _max_topic_size()
    if len(new_content) > max_size:
        return {
            "status": "error",
            "message": f"Topic '{slug}' would exceed max size ({len(new_content)}/{max_size} chars). "
                       "Consider summarizing old entries.",
        }

    return save_topic(slug, new_content)


def delete_topic(topic: str) -> dict:
    """Delete a topic memory file.

    Args:
        topic: Topic name (will be slugified).

    Returns:
        Status dict.
    """
    slug = _slugify(topic)
    if not slug:
        return {"status": "error", "message": "Invalid topic name."}
    topic_file = _memory_dir() / TOPICS_DIR / f"{slug}.md"
    if not topic_file.is_file():
        return {"status": "error", "message": f"Topic '{slug}' not found."}

    try:
        topic_file.unlink()
        logger.info("Deleted topic '%s'", slug)
        return {"status": "ok", "topic": slug, "deleted": True}
    except Exception as e:
        logger.error("Failed to delete topic '%s': %s", slug, e)
        return {"status": "error", "message": str(e)}


# ── Aggregate Loading (for prompt injection) ──────────────────────────


def load_all_memory() -> str:
    """Load all memory for injection into the system prompt.

    Returns a formatted markdown string with core memory and all topics.
    """
    parts = []

    # Core memory
    core = load_core_memory()
    if core:
        parts.append(core)

    # Topic memories
    topics = list_topics()
    for topic_name in topics:
        content = load_topic(topic_name)
        if content:
            parts.append(content)

    if not parts:
        return ""

    return "\n\n---\n\n".join(parts)


def memory_stats() -> dict:
    """Get memory usage statistics."""
    core = load_core_memory()
    topics = list_topics()

    topic_sizes = {}
    for t in topics:
        content = load_topic(t)
        topic_sizes[t] = len(content)

    return {
        "core_memory_size": len(core),
        "core_memory_max": _max_core_memory_size(),
        "topic_count": len(topics),
        "topics": topic_sizes,
        "topic_max_size": _max_topic_size(),
        "memory_dir": str(_memory_dir()),
    }
