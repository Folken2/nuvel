"""Local CLI for testing the agent without spinning up the server."""

from __future__ import annotations

import json
import os
import sys
from importlib import import_module

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

run_session = import_module("{{agent_package}}").run_session


def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: python run_dev.py "<prompt>"', file=sys.stderr)
        return 1

    agent_id = os.environ.get("MANAGED_AGENT_ID")
    env_id = os.environ.get("MANAGED_AGENT_ENV_ID")
    if not agent_id or not env_id:
        print("MANAGED_AGENT_ID / MANAGED_AGENT_ENV_ID not set. Run setup.py first.", file=sys.stderr)
        return 1

    client = Anthropic()
    prompt = " ".join(sys.argv[1:])

    for payload in run_session(client, agent_id, env_id, prompt):
        kind = payload.get("type", "")
        if kind == "agent.message":
            for block in payload.get("content") or []:
                btype = getattr(block, "type", None) or block.get("type")
                if btype == "text":
                    text = getattr(block, "text", None) or block.get("text", "")
                    print(text, end="", flush=True)
        elif kind in ("agent.tool_use", "agent.mcp_tool_use", "agent.custom_tool_use"):
            name = payload.get("name", "?")
            print(f"\n[tool] {name}", flush=True)
        elif kind == "session.status_idle":
            print("\n[done]")
        elif kind == "session.status_terminated":
            print("\n[terminated]")
        elif kind == "session.error":
            print(f"\n[error] {json.dumps(payload, default=str)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
