"""Agent Client Protocol (ACP) adapter for {{agent_name}}.

This package makes the agent runnable as an ACP subprocess — the protocol
code editors like Zed use to talk to agents over stdio
(https://agentclientprotocol.com). It speaks JSON-RPC 2.0 with
newline-delimited JSON messages on stdin/stdout.

Run it directly:

    python -m {{agent_package}}.acp

An ACP client launches that command and drives it over the pipe:
``initialize`` → ``session/new`` → ``session/prompt`` …

ACP is the agent's canonical runtime: the terminal CLI
(``{{agent_package}}.cli``) and the Buzz relay worker
(``{{agent_package}}.buzz_relay``) both drive the same
:class:`~{{agent_package}}.acp.runtime.AgentRuntime` this server wraps, so
behavior is identical whichever way a turn arrives.
"""

from __future__ import annotations

__all__ = ["PROTOCOL_VERSION"]

# ACP protocol version this adapter implements.
PROTOCOL_VERSION = 1
