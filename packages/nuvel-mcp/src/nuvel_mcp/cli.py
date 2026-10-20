"""Command line for the Skills MCP server: ``nuvel-mcp serve``.

``add_serve_arguments`` / ``run_serve`` are shared with ``nuvel mcp serve`` so
both entry points expose identical flags.
"""

from __future__ import annotations

import argparse
import sys


def add_serve_arguments(parser: argparse.ArgumentParser) -> None:
    """Add every ``serve`` flag to ``parser``."""
    parser.add_argument(
        "--skills-dir",
        default=".",
        help="Skills hub directory (a dir with index.json, or a repo root "
             "containing skills/index.json). Default: current directory.",
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


def run_serve(args: argparse.Namespace) -> int:
    from nuvel_mcp.server import SkillsMCPServer
    from nuvel_mcp.skills_loader import SkillsError, SkillsLoader

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

    server = SkillsMCPServer(
        loader,
        theme=args.theme,
        require_filter=require_filter,
        with_org_memory=args.with_org_memory,
        feedback_dir=args.feedback_dir,
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
