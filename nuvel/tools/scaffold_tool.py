"""Scaffold tool — wraps nuvel.scaffold as an ADK FunctionTool."""

from __future__ import annotations

import os

from nuvel.scaffold import scaffold_agent as _scaffold_agent

from google.adk.tools import FunctionTool

_OUTPUT_DIR = os.getenv("AGENTS_OUTPUT_DIR", "./generated-agents")


def scaffold_agent(name: str, description: str = "", tool_context=None) -> dict:
    """Create a new ADK agent project from the production skeleton.

    Stamps out a complete runnable project with FastAPI server, plugin chain,
    LiteLLM config, and stub files.
    """
    if tool_context is not None:
        output_dir = tool_context.state.get("agent_output_dir", _OUTPUT_DIR)
    else:
        output_dir = _OUTPUT_DIR

    result = _scaffold_agent(name, output_dir=output_dir, description=description)

    if result.get("status") == "ok" and tool_context is not None:
        tool_context.state["current_agent_name"] = result["agent_name"]
        tool_context.state["current_agent_path"] = result["path"]
        tool_context.state["current_agent_package"] = result["package_name"]

    return result


scaffold_agent_tool = FunctionTool(func=scaffold_agent)
