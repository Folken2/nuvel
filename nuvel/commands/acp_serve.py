"""``nuvel acp serve`` — run the Nuvel meta-agent as an ACP v2 stdio server.

Serves the meta-agent (the ADK ``LlmAgent`` in :mod:`nuvel.agent`) to ACP
(Agent Client Protocol) clients over stdio JSON-RPC 2.0. stdout is reserved
for protocol traffic, so — like the generated-agent ACP adapter — we swap
``sys.stdout`` to stderr *before* importing the heavy agent stack.

The heavy ADK import is deferred until :func:`_cmd_acp_serve` runs (after the
stdout swap), keeping ``nuvel --help`` and the stdlib-only commands fast.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, AsyncGenerator


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the `acp` subcommand tree."""
    p = subparsers.add_parser(
        "acp",
        help="Serve the Nuvel meta-agent over ACP (Agent Client Protocol).",
    )
    sub = p.add_subparsers(dest="acp_command", required=True)

    p_serve = sub.add_parser(
        "serve",
        help="Start the Nuvel ACP stdio server.",
        description=(
            "Run the Nuvel meta-agent as an ACP v2 subprocess over stdio "
            "(JSON-RPC 2.0 with newline-delimited JSON). An ACP client "
            "(e.g. Zed) launches this command and drives it over the pipe: "
            "initialize -> session/new -> session/prompt."
        ),
    )
    p_serve.add_argument(
        "--dev",
        action="store_true",
        help="Run with DEV_MODE=true (in-memory sessions; no Postgres needed).",
    )
    p_serve.set_defaults(func=_cmd_acp_serve)


# ── ACP payload helpers ──────────────────────────────────────────────


def _jsonable(value: Any) -> Any:
    """Best-effort coercion of a tool result into JSON-serializable data."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _translate_event(event: Any) -> list[dict[str, Any]]:
    """Map one ADK ``Event`` into zero or more ACP ``session/update`` payloads.

    Mirrors the generated-agent ACP adapter's translation: text/thought parts
    become ``agent_message_chunk`` / ``agent_thought_chunk`` updates, and
    tool activity becomes ``tool_call`` / ``tool_call_update`` updates.
    """
    payloads: list[dict[str, Any]] = []
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return payloads

    for part in parts:
        text = getattr(part, "text", None)
        if text:
            session_update = (
                "agent_thought_chunk"
                if getattr(part, "thought", False)
                else "agent_message_chunk"
            )
            payloads.append(
                {
                    "sessionUpdate": session_update,
                    "content": {"type": "text", "text": text},
                }
            )

        fc = getattr(part, "function_call", None)
        if fc is not None:
            payloads.append(
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": getattr(fc, "id", "") or "",
                    "title": getattr(fc, "name", "") or "",
                    "kind": "other",
                    "status": "in_progress",
                    "rawInput": dict(getattr(fc, "args", None) or {}),
                }
            )

        fr = getattr(part, "function_response", None)
        if fr is not None:
            payloads.append(
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": getattr(fr, "id", "") or "",
                    "status": "completed",
                    "rawOutput": _jsonable(getattr(fr, "response", None)),
                }
            )

    return payloads


# ── Prompt handler factory ───────────────────────────────────────────


def _build_prompt_handler(root_agent: Any) -> Any:
    """Build the :class:`~nuvel.acp.server.AcpServer` prompt handler.

    Wraps the meta-agent's ADK runner: one ``Runner`` over an in-memory
    session service (a stdio ACP server is inherently a single local process),
    mirroring how :mod:`nuvel.memory.sibling_runner` builds throwaway runners.
    Returns an async generator ``(session_id, prompt_text) -> payload``.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    app_name = "nuvel"
    user_id = os.getenv("ACP_USER_ID", "nuvel-user")
    session_service = InMemorySessionService()
    runner = Runner(
        app_name=app_name,
        agent=root_agent,
        session_service=session_service,
        memory_service=None,
    )

    async def handle_prompt(
        session_id: str, prompt_text: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        from google.genai import types

        existing = await session_service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        if existing is None:
            await session_service.create_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )

        message = types.Content(role="user", parts=[types.Part(text=prompt_text)])
        async for event in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=message
        ):
            for payload in _translate_event(event):
                yield payload

    return handle_prompt


# ── Command handler ──────────────────────────────────────────────────


def _cmd_acp_serve(args: argparse.Namespace) -> int:
    if args.dev:
        os.environ["DEV_MODE"] = "true"

    # Reserve the real stdout for JSON-RPC; send every stray print/log to
    # stderr. Must happen *before* importing the heavy agent stack, which
    # initializes (and may print) at import time.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    from nuvel.acp.server import AcpServer
    from nuvel.agent import root_agent

    server = AcpServer(_build_prompt_handler(root_agent))
    asyncio.run(server.serve(stdout_writer=real_stdout))
    return 0
