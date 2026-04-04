# Composio Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `list_composio_toolkits` tool so the meta-agent can discover available Composio integrations, and update the system prompt so it knows when and how to wire Composio into generated agents.

**Architecture:** One new tool file using the Composio SDK (`composio.Composio.toolkits.list/get`), wired into the existing tools registry. Template requirements.txt gets optional Composio deps. System prompt gets Composio-aware Discovery and Code Generation sections.

**Tech Stack:** Composio SDK (`composio`), google-adk FunctionTool

---

## File Structure

### New files:
- `meta_agent/tools/composio_tools.py` — `list_composio_toolkits` tool + FunctionTool instance
- `tests/test_composio_tools.py` — Tests with mocked Composio SDK

### Modified files:
- `meta_agent/tools/__init__.py` — Add `list_composio_toolkits_tool` to imports and `get_tools()`
- `meta_agent/prompt/instructions.py` — Add Composio to Capabilities, Discovery, and Code Generation Rules
- `meta_agent/templates/requirements.txt` — Add commented-out Composio deps
- `requirements.txt` — Add `composio` to meta-agent's own deps

---

### Task 1: list_composio_toolkits Tool

**Files:**
- Create: `meta_agent/tools/composio_tools.py`
- Create: `tests/test_composio_tools.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add composio to meta-agent requirements**

Append to `/Users/albertfolch/Documents/Cursor/meta-agent/requirements.txt`:

```
composio
```

Install:

```bash
source .venv/bin/activate && pip install composio
```

- [ ] **Step 2: Write tests**

Create `tests/test_composio_tools.py`:

```python
"""Tests for meta_agent.tools.composio_tools."""

from unittest.mock import patch, MagicMock

import pytest

from meta_agent.tools.composio_tools import (
    list_composio_toolkits,
    _list_toolkits_impl,
)


class TestListToolkitsImpl:
    @patch("meta_agent.tools.composio_tools.Composio")
    def test_returns_toolkits(self, MockComposio):
        mock_client = MagicMock()
        MockComposio.return_value = mock_client

        # Mock the list response
        item1 = MagicMock()
        item1.slug = "github"
        item1.name = "GitHub"
        item1.description = "GitHub integration"

        item2 = MagicMock()
        item2.slug = "slack"
        item2.name = "Slack"
        item2.description = "Slack integration"

        mock_response = MagicMock()
        mock_response.items = [item1, item2]
        mock_client.toolkits.list.return_value = mock_response

        result = _list_toolkits_impl(api_key="test-key")
        assert result["status"] == "ok"
        assert len(result["toolkits"]) == 2
        assert result["toolkits"][0]["slug"] == "github"
        assert result["toolkits"][1]["slug"] == "slack"

    @patch("meta_agent.tools.composio_tools.Composio")
    def test_filters_by_query(self, MockComposio):
        mock_client = MagicMock()
        MockComposio.return_value = mock_client

        item1 = MagicMock()
        item1.slug = "github"
        item1.name = "GitHub"
        item1.description = "GitHub integration for repos and PRs"

        item2 = MagicMock()
        item2.slug = "slack"
        item2.name = "Slack"
        item2.description = "Slack messaging"

        mock_response = MagicMock()
        mock_response.items = [item1, item2]
        mock_client.toolkits.list.return_value = mock_response

        result = _list_toolkits_impl(api_key="test-key", query="github")
        assert result["status"] == "ok"
        assert len(result["toolkits"]) == 1
        assert result["toolkits"][0]["slug"] == "github"

    @patch("meta_agent.tools.composio_tools.Composio")
    def test_passes_category(self, MockComposio):
        mock_client = MagicMock()
        MockComposio.return_value = mock_client

        mock_response = MagicMock()
        mock_response.items = []
        mock_client.toolkits.list.return_value = mock_response

        _list_toolkits_impl(api_key="test-key", category="communication")
        mock_client.toolkits.list.assert_called_once_with(category="communication")

    @patch("meta_agent.tools.composio_tools.Composio")
    def test_no_results(self, MockComposio):
        mock_client = MagicMock()
        MockComposio.return_value = mock_client

        mock_response = MagicMock()
        mock_response.items = []
        mock_client.toolkits.list.return_value = mock_response

        result = _list_toolkits_impl(api_key="test-key", query="nonexistent")
        assert result["status"] == "ok"
        assert result["toolkits"] == []

    @patch("meta_agent.tools.composio_tools.Composio")
    def test_sdk_error(self, MockComposio):
        MockComposio.side_effect = Exception("Connection failed")
        result = _list_toolkits_impl(api_key="test-key")
        assert result["status"] == "error"
        assert "Connection failed" in result["message"]


class TestListComposioToolkits:
    def test_missing_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            # Ensure COMPOSIO_API_KEY is not set
            import os
            old = os.environ.pop("COMPOSIO_API_KEY", None)
            try:
                result = list_composio_toolkits()
                assert result["status"] == "error"
                assert "COMPOSIO_API_KEY" in result["message"]
            finally:
                if old is not None:
                    os.environ["COMPOSIO_API_KEY"] = old

    @patch("meta_agent.tools.composio_tools._list_toolkits_impl")
    def test_delegates_to_impl(self, mock_impl):
        mock_impl.return_value = {"status": "ok", "toolkits": [], "count": 0}
        with patch.dict("os.environ", {"COMPOSIO_API_KEY": "test-key"}):
            result = list_composio_toolkits(query="github")
            mock_impl.assert_called_once_with(api_key="test-key", query="github", category=None)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
source .venv/bin/activate && python -m pytest tests/test_composio_tools.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'meta_agent.tools.composio_tools'`

- [ ] **Step 4: Implement composio_tools.py**

Create `meta_agent/tools/composio_tools.py`:

```python
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
    """List Composio toolkits. Testable without ToolContext.

    Args:
        api_key: Composio API key.
        query: Optional search filter (matches against slug and name).
        category: Optional category filter (passed to API).

    Returns:
        dict with status, toolkits list, and count.
    """
    if Composio is None:
        return {
            "status": "error",
            "message": "composio package not installed. Run: pip install composio",
        }

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

            toolkits.append({
                "slug": slug,
                "name": name,
                "description": description,
            })

        # Filter by query if provided
        if query:
            q = query.lower()
            toolkits = [
                t for t in toolkits
                if q in t["slug"].lower() or q in t["name"].lower() or q in t["description"].lower()
            ]

        return {
            "status": "ok",
            "toolkits": toolkits,
            "count": len(toolkits),
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Composio SDK error: {e}",
        }


def list_composio_toolkits(
    query: str = None,
    category: str = None,
    tool_context=None,
) -> dict:
    """List available Composio toolkits for external service integrations.

    Composio provides 250+ pre-built integrations (GitHub, Slack, Gmail,
    Sheets, Jira, etc.) exposed via MCP. Use this to verify toolkit names
    exist before wiring them into a generated agent.

    Args:
        query: Search filter — matches against toolkit name and description
               (e.g., "github", "slack", "email")
        category: Filter by category (e.g., "communication", "developer-tools")

    Returns:
        List of matching toolkits with slug, name, and description.
    """
    api_key = os.getenv("COMPOSIO_API_KEY")
    if not api_key:
        return {
            "status": "error",
            "message": (
                "COMPOSIO_API_KEY not set. Ask the user for their Composio API key "
                "and set it in the environment or .env file."
            ),
        }

    return _list_toolkits_impl(api_key=api_key, query=query, category=category)


list_composio_toolkits_tool = FunctionTool(func=list_composio_toolkits)
```

- [ ] **Step 5: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_composio_tools.py -v
```

Expected: All PASS

- [ ] **Step 6: Run all tests**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: All 79 existing + new tests PASS

- [ ] **Step 7: Commit**

```bash
git add meta_agent/tools/composio_tools.py tests/test_composio_tools.py requirements.txt
git commit -m "feat: add list_composio_toolkits tool

Queries Composio SDK for available toolkits. Supports query
and category filtering. Returns slug, name, description."
```

---

### Task 2: Wire Tool + Update System Prompt

**Files:**
- Modify: `meta_agent/tools/__init__.py`
- Modify: `meta_agent/prompt/instructions.py`
- Modify: `meta_agent/templates/requirements.txt`

- [ ] **Step 1: Update `meta_agent/tools/__init__.py`**

Replace full content:

```python
"""Meta-agent tools — file ops, scaffolding, validation, skill discovery, and Composio."""

from .file_tools import write_file_tool, read_file_tool, list_files_tool
from .scaffold_tool import scaffold_agent_tool
from .validate_tool import validate_agent_tool
from .skills_tools import search_skills_tool, install_skill_tool, read_skill_context_tool
from .composio_tools import list_composio_toolkits_tool


def get_tools():
    """Return all meta-agent function tools."""
    return [
        scaffold_agent_tool,
        write_file_tool,
        read_file_tool,
        list_files_tool,
        validate_agent_tool,
        search_skills_tool,
        install_skill_tool,
        read_skill_context_tool,
        list_composio_toolkits_tool,
    ]
```

- [ ] **Step 2: Update `meta_agent/prompt/instructions.py`**

Three edits:

**Edit A — Capabilities list (line 24).** Change:

```
1. **Function Tools** for file operations and skill discovery: scaffold_agent, write_file, read_file, list_files, validate_agent, search_skills, install_skill, read_skill_context
```

To:

```
1. **Function Tools** for file operations, skill discovery, and integrations: scaffold_agent, write_file, read_file, list_files, validate_agent, search_skills, install_skill, read_skill_context, list_composio_toolkits
```

**Edit B — Discovery phase (after line 37, add Composio check).** Change:

```
- **LLM preference**: Model preference (default: OpenRouter via LiteLLM)

Ask only the questions that aren't already answered. If the user gives a comprehensive brief, skip to Design.
```

To:

```
- **LLM preference**: Model preference (default: OpenRouter via LiteLLM)

When the user mentions external integrations (APIs, services, communication tools), check if Composio has a toolkit for it by calling `list_composio_toolkits("service-name")`. Composio provides 250+ pre-built integrations (GitHub, Slack, Gmail, Sheets, etc.) via MCP — much faster than writing custom API tools from scratch.

Ask only the questions that aren't already answered. If the user gives a comprehensive brief, skip to Design.
```

**Edit C — Code Generation Rules (after the Agent Wiring section, before Important Rules).** Insert a new section:

```
## Composio Integration
When an agent needs external service integrations (GitHub, Slack, email, etc.):
1. Call `list_composio_toolkits("service-name")` to verify the toolkit exists and get the exact slug
2. Write `<package>/tools/composio_tools.py` with a `get_composio_toolset()` function using the MCP pattern:
   - Import `Composio` from composio SDK
   - Import `StreamableHTTPConnectionParams` and `McpToolset` from google.adk
   - Create a Composio session with the specific `toolkits=["github", "slack", ...]` list
   - Return a `McpToolset` connected to the session's MCP URL
   - Return `None` if `COMPOSIO_API_KEY` is not set (graceful degradation)
3. Update `<package>/tools/__init__.py` to import and include `get_composio_toolset()`
4. Add `composio` and `composio-google-adk>=0.11.0,<1.0.0` to the agent's `requirements.txt`
5. Add `COMPOSIO_API_KEY` and `COMPOSIO_USER_ID` to `.env.example`
```

- [ ] **Step 3: Update template requirements**

Add to `meta_agent/templates/requirements.txt` (append after uvicorn line):

```
# Optional: Composio Tool Router (uncomment if using external integrations)
# composio
# composio-google-adk>=0.11.0,<1.0.0
```

- [ ] **Step 4: Run all tests**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: All PASS

- [ ] **Step 5: Commit and push**

```bash
git add meta_agent/tools/__init__.py meta_agent/prompt/instructions.py meta_agent/templates/requirements.txt
git commit -m "feat: wire Composio tool + update system prompt and template

Registers list_composio_toolkits in get_tools(). Adds Composio-aware
Discovery, Code Generation Rules, and template deps."
git push
```

---

## Implementation Notes

### Task Dependencies
- Task 1 must complete before Task 2 (need the tool to wire it)
- Both tasks are small — total implementation time is minimal

### Testing Strategy
- All Composio SDK calls are mocked (no real API key needed in tests)
- The `_list_toolkits_impl` function is testable without ToolContext
- The wrapper `list_composio_toolkits` handles env var lookup and delegates

### Composio SDK Response Format
The SDK's `toolkits.list()` returns objects with `.items` attribute. Each item has `.slug`, `.name`, `.description`. The implementation uses `getattr` with fallbacks to handle potential API changes gracefully.
