"""``nuvel mcp serve`` — start the Nuvel Skills MCP stdio server.

Serves a skills hub (a directory with ``index.json`` and
``<theme>/<name>/SKILL.md`` files) to MCP clients over stdio. Stdlib only, so
it runs without the framework's heavier agent dependencies.
"""

from __future__ import annotations

import argparse
import sys


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the `mcp` subcommand tree."""
    p = subparsers.add_parser(
        "mcp",
        help="Serve Nuvel Skills to MCP clients.",
    )
    sub = p.add_subparsers(dest="mcp_command", required=True)

    p_serve = sub.add_parser(
        "serve",
        help="Start the Nuvel Skills MCP stdio server.",
        description=(
            "Start an MCP stdio server that exposes a skills hub as MCP "
            "resources (skill://{theme}/{name}) and tools (search_skills, "
            "get_skill, propose_improvement). Set GITHUB_TOKEN to let "
            "propose_improvement file issues against github.com/Folken2/skills."
        ),
    )
    p_serve.add_argument(
        "--skills-dir",
        default=".",
        help="Skills hub directory (a dir with index.json, or a repo root "
             "containing skills/index.json). Default: current directory.",
    )
    p_serve.add_argument(
        "--theme",
        default=None,
        help="Scope to a single theme/role (e.g. 'hr', 'sales'). "
             "When set, only skills in that theme are exposed.",
    )
    p_serve.add_argument(
        "--require",
        action="append",
        default=None,
        metavar="CAPABILITY",
        help="Only expose skills whose 'requires' frontmatter declares every "
             "listed capability (Airbyte-style gating). Repeatable, and each "
             "value may be comma-separated (e.g. --require api_key "
             "--require salesforce_connected).",
    )
    p_serve.add_argument(
        "--with-org-memory",
        default=None,
        metavar="DSN_OR_PATH",
        help="Resolve {{ var_name }} placeholders in skills against "
             "OrgMemoryService. Accepts a Postgres connection string (contains "
             "'://') or a path to an org-graph YAML file; otherwise falls back "
             "to NUVEL_ORG_MEMORY_DSN / NUVEL_ORG_GRAPH_PATH.",
    )
    p_serve.set_defaults(func=_cmd_mcp_serve)


def _cmd_mcp_serve(args: argparse.Namespace) -> int:
    # Imported lazily so building the parser stays dependency-light.
    from nuvel.mcp.server import SkillsMCPServer
    from nuvel.mcp.skills_loader import SkillsError, SkillsLoader

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
    )
    server.serve()
    return 0
