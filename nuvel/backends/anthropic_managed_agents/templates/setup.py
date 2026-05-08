"""
One-time setup: applies agent.yaml + environment.yaml and writes IDs to .env.

Run after editing either YAML file. Idempotent — if AGENT_ID and ENV_ID
are already in .env, this updates the agent in place (creating a new
version) and leaves the environment alone unless you delete its ID.

For team workflows, prefer the `ant` CLI which handles the same flow
with version pinning and CI integration:
    ant beta:agents create < agent.yaml --transform id -r
    ant beta:agents update --agent-id $ID --version N < agent.yaml
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

load_dotenv(ENV_FILE)


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        print(f"Error: {path} not found.", file=sys.stderr)
        sys.exit(1)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _set_env(key: str, value: str) -> None:
    if not ENV_FILE.exists():
        ENV_FILE.write_text("", encoding="utf-8")
    set_key(str(ENV_FILE), key, value, quote_mode="never")
    os.environ[key] = value


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Add it to .env first.", file=sys.stderr)
        return 1

    client = Anthropic()

    # ── Environment ────────────────────────────────────────────────
    env_spec = _load_yaml(ROOT / "environment.yaml")
    env_id = os.environ.get("MANAGED_AGENT_ENV_ID")

    if env_id:
        print(f"Environment already provisioned: {env_id}")
    else:
        env = client.beta.environments.create(**env_spec)
        env_id = env.id
        _set_env("MANAGED_AGENT_ENV_ID", env_id)
        print(f"Created environment: {env_id}")

    # ── Agent ──────────────────────────────────────────────────────
    agent_spec = _load_yaml(ROOT / "agent.yaml")
    agent_id = os.environ.get("MANAGED_AGENT_ID")

    if agent_id:
        agent = client.beta.agents.update(agent_id, **agent_spec)
        print(f"Updated agent {agent.id} -> version {agent.version}")
    else:
        agent = client.beta.agents.create(**agent_spec)
        agent_id = agent.id
        _set_env("MANAGED_AGENT_ID", agent_id)
        print(f"Created agent: {agent_id} (version {agent.version})")

    _set_env("MANAGED_AGENT_VERSION", str(agent.version))
    print("\nDone. IDs written to .env:")
    print(f"  MANAGED_AGENT_ID={agent_id}")
    print(f"  MANAGED_AGENT_ENV_ID={env_id}")
    print(f"  MANAGED_AGENT_VERSION={agent.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
