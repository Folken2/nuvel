"""
Composio toolkit discovery tool for the Meta-Agent.

Lists available Composio integrations so the meta-agent can verify
toolkit names and suggest relevant ones when building agents that
need external service connections.
"""

from __future__ import annotations

import logging
import os

from google.adk.tools import FunctionTool

logger = logging.getLogger(__name__)

try:
    from composio import Composio
except ImportError:
    Composio = None


def _list_toolkits_impl(
    api_key: str,
    query: str | None = None,
    category: str | None = None,
) -> dict:
    """List Composio toolkits. Testable without ToolContext."""
    if Composio is None:
        return {"status": "error", "message": "composio package not installed. Run: pip install composio"}

    try:
        client = Composio(api_key=api_key)
        kwargs = {}
        if category:
            kwargs["category"] = category

        response = client.toolkits.list(**kwargs)
        items = response.items if hasattr(response, "items") else response

        toolkits = []
        for item in items:
            slug = getattr(item, "slug", getattr(item, "name", ""))
            name = getattr(item, "name", slug)
            description = getattr(item, "description", "")
            toolkits.append({"slug": slug, "name": name, "description": description})

        if query:
            q = query.lower()
            toolkits = [
                t for t in toolkits
                if q in t["slug"].lower() or q in t["name"].lower() or q in t["description"].lower()
            ]

        return {"status": "ok", "toolkits": toolkits, "count": len(toolkits)}

    except Exception as e:
        return {"status": "error", "message": f"Composio SDK error: {e}"}


def list_composio_toolkits(query: str = None, category: str = None, tool_context=None) -> dict:
    """List available Composio toolkits for external service integrations.

    Composio provides 250+ pre-built integrations (GitHub, Slack, Gmail,
    Sheets, Jira, etc.) exposed via MCP. Use this to verify toolkit names
    exist before wiring them into a generated agent.

    Args:
        query: Search filter — matches against toolkit name and description
        category: Filter by category (e.g., "communication", "developer-tools")

    Returns:
        List of matching toolkits with slug, name, and description.
    """
    api_key = os.getenv("COMPOSIO_API_KEY")
    if not api_key:
        return {
            "status": "error",
            "message": "COMPOSIO_API_KEY not set. Ask the user for their Composio API key and set it in .env.",
        }
    return _list_toolkits_impl(api_key=api_key, query=query, category=category)


list_composio_toolkits_tool = FunctionTool(func=list_composio_toolkits)
