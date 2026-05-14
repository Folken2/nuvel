"""
SOUL tools — let the agent read and rewrite its own character file.

The SOUL.md path resolves via config/paths.soul_file() so a deployment
volume mount works without code changes. SOUL.md is loaded into the
system prompt every turn — calling update_soul takes effect on the next
invocation.
"""

from __future__ import annotations

import logging

from google.adk.tools import FunctionTool

from ..config.paths import soul_file

logger = logging.getLogger(__name__)

_MAX_SOUL_SIZE = 8000  # chars — keep the soul compact; details belong in memory


def read_soul() -> dict:
    """Read the agent's current SOUL.md (its character, voice, values)."""
    path = soul_file()
    if not path.is_file():
        return {"status": "ok", "content": "", "message": "No SOUL.md yet."}
    content = path.read_text(encoding="utf-8")
    return {"status": "ok", "content": content, "size": len(content)}


def update_soul(content: str) -> dict:
    """Rewrite the agent's SOUL.md.

    Use this when your character genuinely shifts — a new value crystallizes,
    a voice trait sharpens, a boundary tightens. The next turn loads the new
    soul. Keep it tight; details about the world belong in memory, not soul.

    Args:
        content: Full new markdown content for SOUL.md. Replaces the file.

    Returns:
        Status dict.
    """
    content = content.strip()
    if not content:
        return {"status": "error", "message": "Refusing to write an empty soul."}
    if len(content) > _MAX_SOUL_SIZE:
        return {
            "status": "error",
            "message": f"Soul too large ({len(content)}/{_MAX_SOUL_SIZE}). "
                       "Tighten it — details belong in memory.",
        }

    path = soul_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info("SOUL.md updated (%d chars) at %s", len(content), path)
    return {"status": "ok", "size": len(content), "file": str(path)}


soul_tool_list = [
    FunctionTool(read_soul),
    FunctionTool(update_soul),
]
