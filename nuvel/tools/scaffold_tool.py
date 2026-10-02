"""Scaffold tool — wraps nuvel.scaffold as an ADK FunctionTool."""

from __future__ import annotations

import os

from nuvel.backends.adk.scaffold import scaffold_agent as _scaffold_agent

from google.adk.tools import FunctionTool

_OUTPUT_DIR = os.getenv("AGENTS_OUTPUT_DIR", "./generated-agents")


def scaffold_agent(
    name: str,
    description: str = "",
    persona: bool = False,
    with_composio: bool = False,
    with_slack: bool = False,
    with_telegram: bool = False,
    with_teams: bool = False,
    workflow: bool = False,
    with_acp: bool = False,
    with_eval: bool = False,
    with_litellm: bool = False,
    system_prompt: str = "",
    tool_context=None,
) -> dict:
    """Create a new ADK agent project from the production skeleton.

    Stamps out a complete runnable project with FastAPI server, plugin chain,
    LiteLLM config, and stub files.

    Args:
        name: Kebab-case agent name.
        description: Short agent description.
        persona: Add the self-rewriting SOUL.md persona overlay.
        with_composio: Add the Composio Tool Router MCP toolset.
        with_slack: Add the Slack gateway (implies with_composio).
        with_telegram: Add the Telegram Bot API gateway.
        with_teams: Add the MS Teams sidecar.
        workflow: Build an ADK 2.0 Workflow graph instead of an LlmAgent.
        with_acp: Add the Agent Client Protocol stdio adapter.
        with_eval: Add an evalv2 suite starter.
        system_prompt: Optional inline system prompt seed.
    """
    if tool_context is not None:
        output_dir = tool_context.state.get("agent_output_dir", _OUTPUT_DIR)
    else:
        output_dir = _OUTPUT_DIR

    result = _scaffold_agent(
        name,
        output_dir=output_dir,
        description=description,
        system_prompt=system_prompt,
        persona=persona,
        with_composio=with_composio,
        with_slack=with_slack,
        with_telegram=with_telegram,
        with_teams=with_teams,
        workflow=workflow,
        with_acp=with_acp,
        with_eval=with_eval,
        with_litellm=with_litellm,
    )

    if result.get("status") == "ok" and tool_context is not None:
        tool_context.state["current_agent_name"] = result["agent_name"]
        tool_context.state["current_agent_path"] = result["path"]
        tool_context.state["current_agent_package"] = result["package_name"]

    return result


scaffold_agent_tool = FunctionTool(func=scaffold_agent)
