"""
Awakening tools — self-deleting bootstrap.

`complete_awakening` removes AWAKENING.md so it stops being injected into
the system prompt. Call it after the agent has greeted the principal,
learned a seed of who they are, and updated SOUL.md to match.
"""

from __future__ import annotations

import logging

from google.adk.tools import FunctionTool

from ..config.paths import awakening_file

logger = logging.getLogger(__name__)


def complete_awakening() -> dict:
    """Mark the awakening as complete and delete AWAKENING.md.

    After this is called, AWAKENING.md is gone and the agent is no longer
    a newborn on subsequent turns. Idempotent — calling it twice is fine.

    Returns:
        Status dict.
    """
    path = awakening_file()
    if not path.is_file():
        return {"status": "ok", "message": "Awakening already complete."}
    try:
        path.unlink()
        logger.info("Awakening complete: deleted %s", path)
        return {"status": "ok", "deleted": str(path)}
    except Exception as e:
        logger.error("Failed to delete %s: %s", path, e)
        return {"status": "error", "message": str(e)}


awakening_tool_list = [FunctionTool(complete_awakening)]
