"""
Optional OrgMemoryService retrieval backend.

When an org-scoped memory database is configured (``NUVEL_ORG_MEMORY_DSN`` +
``NUVEL_ORG_GRAPH_PATH``), this builds Nuvel's ``OrgMemoryService`` — an ADK
``BaseMemoryService`` backed by a hierarchical, embedding-ranked store — so it
can be handed to the Runner as ``memory_service=...``. The relevance-preload
surface (``preload.py``) then retrieves through it automatically.

The service is optional by design: generated agents run standalone and must
not hard-depend on the org-memory extra. When the DSN is unset or the
``nuvel`` org-memory package isn't installed, :func:`build_memory_service`
returns ``None`` and the markdown store remains the sole backend. This keeps
existing generated agents working unchanged while making OrgMemoryService the
default the moment a DB is wired up.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENV_DSN = "NUVEL_ORG_MEMORY_DSN"
ENV_GRAPH = "NUVEL_ORG_GRAPH_PATH"


def org_memory_configured() -> bool:
    """True when the env names an org-memory database to use as the backend."""
    return bool(os.environ.get(ENV_DSN))


async def build_memory_service() -> Optional[Any]:
    """Build an ``OrgMemoryService`` from env, or ``None`` when unavailable.

    Never raises: any import/connection failure is logged and downgraded to
    ``None`` so the agent falls back to markdown memory instead of crashing.
    """
    if not org_memory_configured():
        return None
    try:
        # Prefer the installed nuvel org-memory package. Generated agents that
        # opt into a memory DB add the extra to requirements; when it's absent
        # this import fails and we fall back to markdown.
        from nuvel.memory.factory import build_default_service  # type: ignore
    except Exception as exc:
        logger.info(
            "org memory DSN set but nuvel.memory unavailable (%s); "
            "falling back to markdown memory", exc,
        )
        return None
    try:
        return await build_default_service()
    except Exception:
        logger.warning("failed to build OrgMemoryService; using markdown memory", exc_info=True)
        return None


__all__ = ["build_memory_service", "org_memory_configured", "ENV_DSN", "ENV_GRAPH"]
