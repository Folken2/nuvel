"""Example tool — replace or delete once you have real tools."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool


@tool(
    "echo",
    "Echo back the provided text. Replace this with a real tool.",
    {"text": str},
)
async def echo(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {"type": "text", "text": f"echo: {args['text']}"},
        ],
    }
