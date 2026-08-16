"""
Minimal tools for the meta-agent's review fork.

The judge fork is allowed to write one durable fact (``remember_fact``, backed
by the active :class:`OrgMemoryService`) and to *read* the current skill
catalog (``list_skills`` / ``read_skill``) — never to author skills. The write
tool reaches the invocation's ``memory_service`` through the injected
``ToolContext``; when no memory DB is wired the call is a logged no-op so the
fork degrades gracefully instead of raising.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Any

from google.adk.tools import FunctionTool

from nuvel.config import get_skills_dir

logger = logging.getLogger(__name__)

_DEFAULT_SKILLS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent / "backends" / "adk" / "skills"
)


def _skills_dir() -> pathlib.Path:
    return get_skills_dir(_DEFAULT_SKILLS_DIR)


def _memory_service(tool_context: Any) -> Any:
    ictx = getattr(tool_context, "_invocation_context", None)
    return getattr(ictx, "memory_service", None) if ictx is not None else None


async def remember_fact(content: str, tool_context: Any = None) -> dict:
    """Save one durable fact to long-term memory for future sessions.

    Args:
        content: The fact to remember. Concise, specific, and stable.

    Returns:
        Status dict.
    """
    content = (content or "").strip()
    if not content:
        return {"status": "error", "message": "empty content"}

    service = _memory_service(tool_context)
    if service is None or not hasattr(service, "add_memory"):
        logger.info("remember_fact: no memory service wired; dropping fact")
        return {"status": "skipped", "reason": "no_memory_service"}

    ictx = getattr(tool_context, "_invocation_context", None)
    app_name = getattr(ictx, "app_name", "") or ""
    user_id = getattr(ictx, "user_id", "") or ""
    try:
        await service.add_memory(
            app_name=app_name,
            user_id=user_id,
            memories=[{"content": content}],
        )
    except Exception:
        logger.warning("remember_fact: add_memory failed", exc_info=True)
        return {"status": "error", "message": "write failed"}
    return {"status": "ok", "content": content}


def list_skills() -> dict:
    """List the names of skills the meta-agent currently has."""
    base = _skills_dir()
    if not base.is_dir():
        return {"status": "ok", "skills": []}
    names = [
        d.name for d in sorted(base.iterdir())
        if d.is_dir() and (d / "SKILL.md").is_file()
    ]
    return {"status": "ok", "skills": names, "count": len(names)}


def read_skill(name: str) -> dict:
    """Read a skill's SKILL.md body by name (read-only).

    Args:
        name: Skill directory name.
    """
    path = _skills_dir() / (name or "") / "SKILL.md"
    if not path.is_file():
        return {"status": "error", "message": f"Skill '{name}' not found."}
    return {"status": "ok", "name": name, "content": path.read_text(encoding="utf-8")}


review_tool_list = [
    FunctionTool(remember_fact),
    FunctionTool(list_skills),
    FunctionTool(read_skill),
]

REVIEW_TOOL_NAMES = frozenset({"remember_fact", "list_skills", "read_skill"})


__all__ = ["review_tool_list", "REVIEW_TOOL_NAMES", "remember_fact", "list_skills", "read_skill"]
