"""
Tools for outlook-king.

The tool surface is composed of:
  - memory + cron     — always on (nuvel base)
  - persona stack     — soul, skill-authoring, awakening (--persona)
  - composio MCP      — ~1000 toolkits incl. Outlook/Graph (--with-composio)
  - outlook domain    — context, draft analysis, style memory, search hints

Outlook itself is accessed through Composio's hosted MCP server (the
``OUTLOOK_*`` toolkits). The domain tools below add the workflow glue:
reading what the user is currently looking at, learning their voice,
grounding draft coaching, and shaping search queries.
"""

from .memory_tools import memory_tool_list
from ..cron.tools import cronjob_tool_list
from .soul_tools import soul_tool_list
from .skill_tools import skill_tool_list
from .awakening_tools import awakening_tool_list
from .composio_mcp import build_composio_mcp_toolset

from .outlook_context import outlook_context_tool_list
from .outlook_actions import outlook_action_tool_list
from .style_tools import style_tool_list
from .coach_tools import coach_tool_list
from .search_hints import search_hint_tool_list


def get_tools() -> list:
    """Return the list of tools available to the agent."""
    tools: list = []
    tools.extend(memory_tool_list)
    tools.extend(cronjob_tool_list)
    tools.extend(soul_tool_list)
    tools.extend(skill_tool_list)
    tools.extend(awakening_tool_list)

    tools.extend(outlook_context_tool_list)
    tools.extend(outlook_action_tool_list)
    tools.extend(style_tool_list)
    tools.extend(coach_tool_list)
    tools.extend(search_hint_tool_list)

    composio_toolset = build_composio_mcp_toolset()
    if composio_toolset is not None:
        tools.append(composio_toolset)
    return tools
