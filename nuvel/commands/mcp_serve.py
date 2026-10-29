"""``nuvel mcp serve`` — start the Nuvel Skills MCP server.

Serves a skills hub (a directory with ``index.json`` and
``<theme>/<name>/SKILL.md`` files) to MCP clients over stdio (default) or HTTP
(``--transport http``: Streamable HTTP on ``/mcp`` plus legacy SSE on ``/sse``,
gated by OAuth 2.1). ``nuvel mcp client`` and ``nuvel mcp token`` manage the
OAuth store; ``nuvel mcp db`` manages the Postgres database. Every flag and command comes from :mod:`nuvel_mcp.cli`, so
``nuvel mcp …`` and ``nuvel-mcp …`` stay identical. Stdlib only, so it runs
without the framework's heavier agent dependencies.
"""

from __future__ import annotations

import argparse

from nuvel_mcp.cli import add_db_commands, add_oauth_commands, add_serve_arguments, run_serve


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the `mcp` subcommand tree."""
    p = subparsers.add_parser(
        "mcp",
        help="Serve Nuvel Skills to MCP clients.",
    )
    sub = p.add_subparsers(dest="mcp_command", required=True)

    p_serve = sub.add_parser(
        "serve",
        help="Start the Nuvel Skills MCP server (stdio or HTTP).",
        description=(
            "Start an MCP server (stdio by default, Streamable HTTP with "
            "--transport http, gated by OAuth 2.1) that exposes a skills hub as MCP "
            "resources (skill://{theme}/{name}) and tools (search_skills, "
            "list_skills, list_themes, get_skill, install_skill, fence_value, "
            "propose_improvement, record_feedback, check_skill_health, "
            "record_run_trace). "
            "propose_improvement files issues against github.com/Folken2/skills "
            "when GITHUB_TOKEN is set, otherwise POSTs to NUVEL_FEEDBACK_URL, "
            "otherwise queues into the local feedback store "
            "(<feedback-dir>/feedback-queue.jsonl), where check_skill_health "
            "sees it."
        ),
    )
    add_serve_arguments(p_serve)
    p_serve.set_defaults(func=_cmd_mcp_serve)
    add_oauth_commands(sub)
    add_db_commands(sub)


def _cmd_mcp_serve(args: argparse.Namespace) -> int:
    return run_serve(args)
