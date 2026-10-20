"""Command line for the Skills MCP server: ``nuvel-mcp serve``.

``add_serve_arguments`` / ``run_serve`` are shared with ``nuvel mcp serve`` so
both entry points expose identical flags.
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys

from nuvel_mcp.auth import LOOPBACK_HOSTS

HTTP_EXTRA_HINT = 'pip install "nuvel-mcp[http]"'
# Top-level modules the http extra provides; a missing one means the extra
# isn't installed. Any other ImportError is reported as itself.
# A bare DNS name or IPv4 address: no scheme, port, path, userinfo or spaces.
ALLOWED_HOST_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*")
HTTP_EXTRA_MODULES = frozenset({"mcp", "starlette", "anyio", "uvicorn", "httpx", "sse_starlette"})


def _import_http_transport():
    return importlib.import_module("nuvel_mcp.http")


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
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="'stdio' (default) or 'http' (Streamable HTTP; needs "
             f"{HTTP_EXTRA_HINT}). Over HTTP only record_run_trace and "
             "check_skill_health are exposed unless --tools says otherwise.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP bind address. Default: 127.0.0.1. Any non-loopback "
             "address requires --auth.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="HTTP port. Default: 8765.",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=None,
        metavar="HOST",
        help="HTTP on a loopback bind: also accept this Host (and Origin "
             "http(s)://HOST), e.g. the public name a reverse proxy forwards. "
             "A bare host name, matched case-insensitively on any port. "
             "Repeatable.",
    )
    parser.add_argument(
        "--auth",
        default=None,
        metavar="bearer|MODULE:FACTORY",
        help="HTTP authenticator: 'bearer' (tokens from $NUVEL_MCP_TOKENS as "
             "name:token,name:token; the name becomes the trace tenant) or "
             "'module:factory', a zero-argument callable returning an "
             "Authenticator. Default: none (loopback only).",
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

    http = args.transport == "http"
    authenticator = None
    allowed_hosts: list[str] = []
    if args.allowed_host is not None:
        if not http:
            print("Error: --allowed-host applies to --transport http only.", file=sys.stderr)
            return 1
        for value in args.allowed_host:
            if not ALLOWED_HOST_RE.fullmatch(value.lower()):
                print("Error: invalid --allowed-host value; expected a bare host name "
                      "such as mcp.example.com (no scheme, port, path or spaces).",
                      file=sys.stderr)
                return 1
            allowed_hosts.append(value.lower())
        if args.auth is None:
            # A proxied public name is non-loopback reach, same as --host.
            print("Error: --allowed-host makes the server reachable through a proxy; "
                  "it requires --auth.", file=sys.stderr)
            return 1
    if http:
        # Fail fast, before building anything or binding a socket.
        if args.host not in LOOPBACK_HOSTS and args.auth is None:
            print(
                f"Error: --host {args.host} is not a loopback address; "
                "serving it requires --auth.",
                file=sys.stderr,
            )
            return 1
        try:
            http_transport = _import_http_transport()
        except ImportError as exc:
            missing = (exc.name or "").partition(".")[0]
            if missing in HTTP_EXTRA_MODULES:
                print(f"Error: --transport http needs the http extra: {HTTP_EXTRA_HINT}",
                      file=sys.stderr)
            else:
                print(f"Error: --transport http could not import {exc.name or exc}",
                      file=sys.stderr)
            return 1
        from nuvel_mcp.auth import load_authenticator
        try:
            # The spec only ever comes from the command line.
            authenticator = load_authenticator(args.auth)
        except ValueError as exc:
            print(f"Error: invalid --auth: {exc}", file=sys.stderr)
            return 1

    tools = args.tools
    if http and tools is None:
        from nuvel_mcp.server import HTTP_DEFAULT_TOOLS
        tools = HTTP_DEFAULT_TOOLS

    server = SkillsMCPServer(
        loader,
        theme=args.theme,
        require_filter=require_filter,
        with_org_memory=args.with_org_memory,
        feedback_dir=args.feedback_dir,
        tools=tools,
        trace_store=trace_store,
    )
    if http:
        http_transport.serve_http(server, args.host, args.port, authenticator,
                                  allowed_hosts=allowed_hosts)
    else:
        server.serve()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nuvel-mcp", description="Nuvel Skills MCP server.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_serve = sub.add_parser("serve", help="Start the Nuvel Skills MCP server (stdio or HTTP).")
    add_serve_arguments(p_serve)
    p_serve.set_defaults(func=run_serve)
    args = parser.parse_args(argv)
    return args.func(args)
