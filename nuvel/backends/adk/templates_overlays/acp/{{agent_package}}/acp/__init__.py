"""Agent Client Protocol (ACP) adapter for {{agent_name}}.

This package makes the agent runnable as an ACP subprocess — the protocol
code editors like Zed use to talk to coding agents over stdio
(https://agentclientprotocol.com). It speaks JSON-RPC 2.0 with
newline-delimited JSON messages on stdin/stdout.

Run it directly:

    python -m {{agent_package}}.acp

An ACP client launches that command and drives it over the pipe:
``initialize`` → ``session/new`` → ``session/prompt`` …

The adapter reuses the same ``AgentHarness``-wired Runner as the FastAPI
server, so tools, plugins, memory, and cost tracking behave identically.
For a plain terminal REPL instead of the protocol, see
``{{agent_package}}.cli``.
"""

from __future__ import annotations

__all__ = ["PROTOCOL_VERSION"]

# ACP protocol version this adapter implements.
PROTOCOL_VERSION = 2
