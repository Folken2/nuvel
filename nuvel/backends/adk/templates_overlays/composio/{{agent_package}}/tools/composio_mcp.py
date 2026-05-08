"""
Composio Tool Router — MCP integration.

Creates a per-user Composio session and exposes its hosted MCP server
to the agent via ADK's McpToolset. Composio handles auth, tool discovery,
and execution behind a single MCP endpoint.

Env:
    COMPOSIO_API_KEY  required to enable the toolset
    COMPOSIO_USER_ID  user identity scoped to the session (default: "default")
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def build_composio_mcp_toolset() -> Optional[object]:
    """Return a McpToolset wired to Composio's hosted MCP server, or None if disabled."""
    api_key = os.getenv("COMPOSIO_API_KEY")
    if not api_key:
        logger.info("COMPOSIO_API_KEY not set — Composio Tool Router disabled.")
        return None

    try:
        from composio import Composio
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            StreamableHTTPConnectionParams,
        )
    except ImportError as e:
        logger.warning("Composio MCP deps missing: %s", e)
        return None

    user_id = os.getenv("COMPOSIO_USER_ID", "default")

    try:
        composio = Composio()
        session = composio.create(user_id=user_id)
    except Exception as e:
        logger.warning("Failed to create Composio session for user %r: %s", user_id, e)
        return None

    url = getattr(session.mcp, "url", None)
    headers = getattr(session.mcp, "headers", None) or {}
    if not url:
        logger.warning("Composio session returned no MCP url; skipping toolset.")
        return None

    logger.info("Composio Tool Router MCP wired for user_id=%s", user_id)
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=url, headers=headers),
    )
