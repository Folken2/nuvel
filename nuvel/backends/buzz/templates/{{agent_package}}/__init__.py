"""{{agent_name}} — {{agent_description}}

A Buzz agent. Everything is configured from the environment; see
``.env.example`` for the full surface.

Entrypoints:

    python -m {{agent_package}}.acp          ACP subprocess (stdio JSON-RPC)
    python -m {{agent_package}}.cli          terminal one-shot / REPL
    python -m {{agent_package}}.buzz_relay   join a Buzz relay group
"""

from __future__ import annotations

__all__ = ["AGENT_NAME"]

AGENT_NAME = "{{agent_name}}"
