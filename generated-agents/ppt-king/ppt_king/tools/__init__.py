"""
Tools for ppt-king.

The tool surface is composed of:
  - memory + cron     — always on (nuvel base)
  - persona stack     — soul, skill-authoring, awakening (--persona)
  - composio MCP      — ~1000 toolkits (--with-composio)
  - ppt domain        — context, outline planning, structure, style memory

PowerPoint itself is read/written through the Office.js add-in, which
pushes the active slide and deck outline into ADK session state before
each turn. The domain tools below add the workflow glue: reading what
the user is currently looking at, planning a deck from a brief,
analysing deck flow, and learning the user's slide style over time.
"""

from .memory_tools import memory_tool_list
from ..cron.tools import cronjob_tool_list
from .soul_tools import soul_tool_list
from .skill_tools import skill_tool_list
from .awakening_tools import awakening_tool_list
from .composio_mcp import build_composio_mcp_toolset

from .ppt_context import ppt_context_tool_list
from .action_tools import action_tool_list
from .style_tools import style_tool_list
from .outline_tools import outline_tool_list
from .structure_tools import structure_tool_list


def get_tools() -> list:
    """Return the list of tools available to the agent."""
    tools: list = []
    tools.extend(memory_tool_list)
    tools.extend(cronjob_tool_list)
    tools.extend(soul_tool_list)
    tools.extend(skill_tool_list)
    tools.extend(awakening_tool_list)

    tools.extend(ppt_context_tool_list)
    tools.extend(action_tool_list)
    tools.extend(style_tool_list)
    tools.extend(outline_tool_list)
    tools.extend(structure_tool_list)

    composio_toolset = build_composio_mcp_toolset()
    if composio_toolset is not None:
        tools.append(composio_toolset)
    return tools
