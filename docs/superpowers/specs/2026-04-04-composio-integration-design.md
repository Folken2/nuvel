# Composio Integration Design Spec

> Enable the meta-agent to discover Composio toolkits and wire them into generated agents, giving agents access to 250+ external service integrations via MCP.

## Core Principle

Composio provides pre-built integrations (GitHub, Slack, Gmail, Sheets, Jira, etc.) exposed via MCP. Instead of writing custom API tools from scratch, the meta-agent can wire Composio's Tool Router into generated agents — the agent gets instant access to external services.

## Scope

1. **One new tool:** `list_composio_toolkits(query, category)` — searches available Composio toolkits via SDK
2. **Template update:** Add `composio` and `composio-google-adk` to the template's `requirements.txt`
3. **System prompt update:** Tell the meta-agent about Composio and when to offer it
4. **No new templates** — the meta-agent writes Composio wiring code via `write_file` using the data-analysis-agent pattern as reference

## Tool: list_composio_toolkits

### Signature
```python
def list_composio_toolkits(query: str = None, category: str = None, tool_context=None) -> dict
```

### Behavior
- Uses the Composio SDK: `Composio(api_key=key).toolkits.list()` and `.toolkits.get(slug)`
- If `query` provided: filters/searches toolkits by name
- If `category` provided: filters by category via `list(category=category)`
- Returns list of toolkits with: name (slug), description, category
- Requires `COMPOSIO_API_KEY` env var — returns helpful error if not set

### Return format
```python
# Success
{"status": "ok", "toolkits": [{"name": "github", "description": "...", ...}], "count": N}

# API key missing
{"status": "error", "message": "COMPOSIO_API_KEY not set. Ask the user for their Composio API key."}

# No matches
{"status": "ok", "toolkits": [], "message": "No toolkits found matching 'xyz'"}
```

## How Composio Gets Wired Into Generated Agents

The meta-agent already writes custom files via `write_file`. When the user needs external integrations, the meta-agent:

1. Calls `list_composio_toolkits("service-name")` to verify the toolkit exists and get the exact slug
2. Writes `<package>/tools/composio_tools.py` following the MCP pattern:

```python
"""Composio Tool Router — external service integrations via MCP."""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

TOOLKITS = ["github", "slack"]  # Set by meta-agent based on user requirements


def get_composio_toolset() -> Optional[object]:
    """Create McpToolset for Composio Tool Router."""
    api_key = os.getenv("COMPOSIO_API_KEY")
    user_id = os.getenv("COMPOSIO_USER_ID", "default-user")
    if not api_key:
        return None
    try:
        from composio import Composio
        from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

        composio_client = Composio(api_key=api_key)
        composio_session = composio_client.create(
            user_id=user_id,
            toolkits=TOOLKITS,
        )
        mcp_url = composio_session.mcp.url
        logger.info("Composio Tool Router MCP session created: %s", mcp_url)

        return McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=mcp_url,
                headers={"x-api-key": api_key},
            ),
        )
    except Exception as e:
        logger.warning("Composio Tool Router not available: %s", e)
        return None
```

3. Updates `<package>/tools/__init__.py` to import `get_composio_toolset` and add it to `get_tools()`
4. Adds `composio` and `composio-google-adk>=0.11.0,<1.0.0` to the agent's `requirements.txt`
5. Updates `.env.example` with `COMPOSIO_API_KEY` and `COMPOSIO_USER_ID`

## Template Update

Add Composio dependencies to `meta_agent/templates/requirements.txt` as commented-out optional deps:

```
# Optional: Composio Tool Router (uncomment if using external integrations)
# composio
# composio-google-adk>=0.11.0,<1.0.0
```

This way the meta-agent can uncomment them when writing the requirements, or write fresh lines.

## System Prompt Updates

### Discovery phase addition
When the user mentions integrations (APIs, services, communication tools), check if Composio has a toolkit for it by calling `list_composio_toolkits("service-name")`. Composio provides 250+ pre-built integrations (GitHub, Slack, Gmail, Sheets, etc.) via MCP — much faster than writing custom API tools from scratch.

### Capabilities list update
Add `list_composio_toolkits` to the function tools list.

### New code generation rule — Composio Integration
When an agent needs external service integrations:
1. Call `list_composio_toolkits` to verify toolkit availability
2. Add `composio` and `composio-google-adk` to the agent's requirements.txt
3. Write a `tools/composio_tools.py` following the MCP pattern
4. Add `COMPOSIO_API_KEY` and `COMPOSIO_USER_ID` to .env.example
5. The generated `get_composio_toolset()` should use the specific toolkits list

## Integration

### New file
- `meta_agent/tools/composio_tools.py` — The `list_composio_toolkits` tool

### Modified files
- `meta_agent/tools/__init__.py` — Add `list_composio_toolkits_tool` to `get_tools()`
- `meta_agent/prompt/instructions.py` — Add Composio sections to Discovery, Capabilities, and Code Generation Rules
- `meta_agent/templates/requirements.txt` — Add commented-out Composio deps

### Dependencies
- `composio` SDK must be installed in the meta-agent's environment (already in data-analysis-agent's deps)
- Generated agents need `composio` and `composio-google-adk` only if they use Composio

## Success Criteria

1. `list_composio_toolkits("github")` returns the GitHub toolkit with slug and description
2. `list_composio_toolkits(category="communication")` returns Slack, Discord, etc.
3. Missing API key returns a helpful error (not a crash)
4. The meta-agent offers Composio when users mention external integrations
5. Generated agents with Composio have a working `get_composio_toolset()` pattern
6. Generated agents without Composio are unaffected (no extra deps)
