"""
Tools for word-king.

The tool surface is composed of:
  - memory + cron     — always on (nuvel base)
  - persona stack     — soul, skill-authoring, awakening (--persona)
  - composio MCP      — ~1000 toolkits, generic outbound (--with-composio)
  - word domain       — selection/document context, style memory,
                        drafting and rewrite heuristics

Word itself is accessed through the Office.js add-in. The taskpane
pushes the user's current selection and full document into ADK session
state, and the domain tools below surface that to the agent. Drafts
and rewrites are returned as plain text the add-in inserts back into
the document.
"""

from .memory_tools import memory_tool_list
from ..cron.tools import cronjob_tool_list
from .soul_tools import soul_tool_list
from .skill_tools import skill_tool_list
from .awakening_tools import awakening_tool_list
from .composio_mcp import build_composio_mcp_toolset

from .word_context import word_context_tool_list
from .word_actions import word_action_tool_list
from .style_tools import style_tool_list
from .draft_tools import draft_tool_list


def get_tools() -> list:
    """Return the list of tools available to the agent."""
    tools: list = []
    tools.extend(memory_tool_list)
    tools.extend(cronjob_tool_list)
    tools.extend(soul_tool_list)
    tools.extend(skill_tool_list)
    tools.extend(awakening_tool_list)

    tools.extend(word_context_tool_list)
    tools.extend(word_action_tool_list)
    tools.extend(style_tool_list)
    tools.extend(draft_tool_list)

    composio_toolset = build_composio_mcp_toolset()
    if composio_toolset is not None:
        tools.append(composio_toolset)
    return tools
