"""Local CLI for testing the agent without spinning up the server."""

from __future__ import annotations

import asyncio
import sys
from importlib import import_module

from dotenv import load_dotenv

load_dotenv()

_agent_mod = import_module("{{agent_package}}")
get_client = _agent_mod.get_client


async def _run(prompt: str) -> None:
    async with get_client() as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            cls = msg.__class__.__name__
            if cls == "AssistantMessage":
                for block in msg.content:
                    if block.__class__.__name__ == "TextBlock":
                        print(block.text)
                    elif block.__class__.__name__ == "ToolUseBlock":
                        print(f"[tool] {block.name}({block.input})")
            elif cls == "ResultMessage":
                cost = getattr(msg, "total_cost_usd", 0.0) or 0.0
                turns = getattr(msg, "num_turns", 0)
                print(f"\n[done] {turns} turns · ${cost:.4f}")


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python run_dev.py \"<prompt>\"", file=sys.stderr)
        return 1
    asyncio.run(_run(" ".join(sys.argv[1:])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
