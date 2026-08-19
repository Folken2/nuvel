"""Entrypoint: run {{agent_name}} as an ACP subprocess over stdio.

    python -m {{agent_package}}.acp

An ACP client (e.g. Zed) launches this command and speaks JSON-RPC 2.0 over
the pipe. stdout is the protocol channel, so before anything else we capture
the real stdout for the transport and point ``sys.stdout`` at stderr — that
way any stray ``print`` from the agent or its dependencies can't corrupt the
JSON-RPC stream. All logging goes to stderr too.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys


def main() -> None:
    # Reserve the real stdout for JSON-RPC; send everything else to stderr.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.getLevelName(os.getenv("LOG_LEVEL", "WARNING")),
    )

    # Imported here so the stdout swap above is already in effect when the
    # agent module (and its dependencies) initialize at import time.
    from .jsonrpc import StdioTransport
    from .server import ACPAgent

    transport = StdioTransport(out=real_stdout, inp=sys.stdin)
    agent = ACPAgent(transport)

    try:
        asyncio.run(agent.serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
