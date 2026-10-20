"""Command line for the Skills MCP server: ``nuvel-mcp serve``.

``add_serve_arguments`` / ``run_serve`` are shared with ``nuvel mcp serve`` so
both entry points expose identical flags.
"""

from __future__ import annotations

import argparse
import sys


def _parse_tools(value: str) -> frozenset[str]:
    return frozenset(v.strip() for v in value.split(",") if v.strip())


def add_serve_arguments(parser: argparse.ArgumentParser) -> None:
    """Add every ``serve`` flag to ``parser``."""
    parser.add_argument(
        "--skills-dir",
        default=None,
        help="Skills hub directory (a dir with index.json, or a repo root "
             "containing skills/index.json). Omit to serve the feedback "
             "tools only.",
    )
    parser.add_argument(
        "--tools",
        default=None,
        type=_parse_tools,
        metavar="A,B",
        help="Comma-separated allow-list of tool names to expose.",
    )
    parser.add_argument(
        "--theme",
        default=None,
        help="Scope to a single theme/role (e.g. 'hr', 'sales'). "
             "When set, only skills in that theme are exposed.",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=None,
        metavar="CAPABILITY",
        help="Only expose skills whose 'requires' frontmatter declares every "
             "listed capability (Airbyte-style gating). Repeatable, and each "
             "value may be comma-separated (e.g. --require api_key "
             "--require salesforce_connected).",
    )
    parser.add_argument(
        "--with-org-memory",
        default=None,
        metavar="DSN_OR_PATH",
        help="Resolve {{ var_name }} placeholders in skills against "
             "OrgMemoryService. Accepts a Postgres connection string (contains "
             "'://') or a path to an org-graph YAML file; otherwise falls back "
             "to NUVEL_ORG_MEMORY_DSN / NUVEL_ORG_GRAPH_PATH.",
    )
    parser.add_argument(
        "--feedback-dir",
        default=None,
        metavar="DIR",
        help="The local skill feedback store: record_feedback outcomes, "
             "propose_improvement's queued proposals, and what "
             "check_skill_health reads. Kept out of the skills hub so a "
             "read-only git clone still works. Default: $NUVEL_FEEDBACK_DIR, "
             "then ~/.nuvel/feedback.",
    )
    parser.add_argument(
        "--trace-store",
        default="local",
        metavar="local|MODULE:FACTORY",
        help="Where record_run_trace stores traces: 'local' (JSONL under "
             "<feedback-dir>/_runs/) or 'module:factory', a zero-argument "
             "callable returning a TraceStore. Default: local.",
    )


def run_serve(args: argparse.Namespace) -> int:
    from nuvel_mcp.feedback import resolve_feedback_dir
    from nuvel_mcp.server import SkillsMCPServer
    from nuvel_mcp.skills_loader import SkillsError, SkillsLoader
    from nuvel_mcp.traces import load_trace_store

    loader = None
    if args.skills_dir is not None:
        try:
            loader = SkillsLoader.from_base(args.skills_dir)
        except SkillsError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    require_filter = None
    if args.require:
        require_filter = []
        for value in args.require:
            require_filter.extend(v.strip() for v in value.split(",") if v.strip())

    trace_store = None
    if args.trace_store != "local":
        try:
            trace_store = load_trace_store(
                args.trace_store, resolve_feedback_dir(args.feedback_dir)
            )
        except ValueError as exc:
            print(f"Error: invalid --trace-store: {exc}", file=sys.stderr)
            return 1

    server = SkillsMCPServer(
        loader,
        theme=args.theme,
        require_filter=require_filter,
        with_org_memory=args.with_org_memory,
        feedback_dir=args.feedback_dir,
        tools=args.tools,
        trace_store=trace_store,
    )
    server.serve()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nuvel-mcp", description="Nuvel Skills MCP server.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_serve = sub.add_parser("serve", help="Start the Nuvel Skills MCP stdio server.")
    add_serve_arguments(p_serve)
    p_serve.set_defaults(func=run_serve)
    args = parser.parse_args(argv)
    return args.func(args)
