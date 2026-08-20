"""Local, terminal-runnable entrypoint for {{agent_name}}.

Two modes:

    python -m {{agent_package}}.cli "summarize today's incidents"   # one-shot
    python -m {{agent_package}}.cli                                  # interactive REPL

It drives the same :class:`~{{agent_package}}.acp.runtime.AgentRuntime` as the
ACP adapter, so tools, skills, and model config behave identically. For editor
integration (Agent Client Protocol) use ``python -m {{agent_package}}.acp``.

Configuration comes from the environment — copy ``.env.example`` to ``.env``
and export it, or set the vars directly.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

from .acp.runtime import APP_NAME, AgentRuntime

# ANSI helpers (tool/thought chatter goes to stderr so piped stdout stays clean).
_DIM = "\033[2m"
_CYAN = "\033[36m"
_RESET = "\033[0m"


def _short(value: object, limit: int = 120) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def _run_once(runtime: AgentRuntime, user_id: str, session_id: str, text: str) -> None:
    await runtime.ensure_session(user_id, session_id)
    async for update in runtime.run_turn(user_id, session_id, text):
        if update.kind == "text":
            sys.stdout.write(update.text)
            sys.stdout.flush()
        elif update.kind == "thought":
            print(f"{_DIM}{update.text}{_RESET}", file=sys.stderr)
        elif update.kind == "tool_call":
            print(
                f"{_CYAN}⚙ {update.tool_name}({_short(update.tool_args)}){_RESET}",
                file=sys.stderr,
            )
    sys.stdout.write("\n")
    sys.stdout.flush()


async def _repl(runtime: AgentRuntime, user_id: str, session_id: str) -> None:
    print(
        f"{APP_NAME} — interactive session. Type your message; "
        f"Ctrl-D or /exit to quit.",
        file=sys.stderr,
    )
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, lambda: input("you> "))
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            break
        line = line.strip()
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        await _run_once(runtime, user_id, session_id, line)


async def _amain(args: argparse.Namespace) -> None:
    runtime = AgentRuntime()
    problems = runtime.agent.config.validate()
    if problems:
        for problem in problems:
            print(f"config: {problem}", file=sys.stderr)
        raise SystemExit(1)

    session_id = args.session_id or uuid.uuid4().hex
    try:
        if args.prompt:
            await _run_once(runtime, args.user_id, session_id, " ".join(args.prompt))
        else:
            await _repl(runtime, args.user_id, session_id)
    finally:
        await runtime.aclose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="{{agent_package}}",
        description=f"Run {APP_NAME} from the terminal (one-shot or interactive REPL).",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt to send. Omit for an interactive REPL.",
    )
    parser.add_argument(
        "--user-id",
        default=os.getenv("ACP_USER_ID", "cli-user"),
        help="User id to attribute the session to (default: cli-user).",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Reuse an existing session id instead of creating a fresh one.",
    )
    args = parser.parse_args(argv)
    try:
        asyncio.run(_amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
