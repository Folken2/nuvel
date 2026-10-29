"""Command line for the Skills MCP server: ``nuvel-mcp serve|client|token``.

``add_serve_arguments`` / ``run_serve`` and ``add_oauth_commands`` are shared
with ``nuvel mcp …`` so both entry points expose identical flags and commands.
``--transport http`` runs the stdlib transport in :mod:`nuvel_mcp.http_transport`
(OAuth 2.1 via :mod:`nuvel_mcp.oauth`); stdio stays the default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# --host / --port defaults; a different value with --transport stdio is ignored.
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8080


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
        help="Comma-separated allow-list of tool names to expose. Default: every "
             "tool on stdio; over HTTP the hub read tools plus record_run_trace "
             "and check_skill_health (record_feedback, propose_improvement and "
             "install_skill only when named here).",
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
        "--recipe-store",
        default="local",
        metavar="local|MODULE:FACTORY",
        help="Where recipe signals and recipes are stored: 'local' (files under "
             "<feedback-dir>/_recipes/) or 'module:factory', a zero-argument "
             "callable returning a RecipeStore. Default: local.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio (default) or http: Streamable HTTP on /mcp, legacy "
             "HTTP+SSE on /sse + /messages, and GET /healthz.",
    )
    parser.add_argument(
        "--host",
        default=_DEFAULT_HOST,
        help="HTTP bind address (default 127.0.0.1). A non-loopback address "
             "requires --allow-remote.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help="HTTP port (default 8080; 0 picks a free port).",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow binding a non-loopback --host (e.g. 0.0.0.0). Requires "
             "NUVEL_MCP_PUBLIC_URL (the OAuth issuer) and either GitHub OAuth "
             "credentials or NUVEL_OAUTH_LOGIN=none (URL keys and "
             "client_credentials only).",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="HTTP only: disable OAuth for local development. Refused unless "
             "--host is loopback.",
    )
    parser.add_argument(
        "--require-auth",
        action="store_true",
        help="stdio only: gate every request on the access token in "
             "NUVEL_MCP_ACCESS_TOKEN (HTTP is always gated).",
    )


def add_oauth_commands(subparsers: argparse._SubParsersAction) -> None:
    """Add the ``client`` and ``token`` commands that manage the OAuth store."""
    p_client = subparsers.add_parser("client", help="Manage OAuth clients of the HTTP server.")
    client_sub = p_client.add_subparsers(dest="client_command", required=True)
    p_create = client_sub.add_parser(
        "create",
        help="Issue a confidential client (client_credentials; plus the "
             "authorization code flow when --redirect-uri is given), or with "
             "--url-key a client reached through a connector URL.",
    )
    p_create.add_argument("--name", required=True, help="Human-readable client name.")
    p_create.add_argument("--redirect-uri", action="append", default=None, metavar="URI",
                          help="Allowed redirect URI (https://, or http:// on loopback). "
                               "Repeatable.")
    p_create.add_argument("--url-key", action="store_true",
                          help="Issue a URL key instead of a secret and print the "
                               "connector URL ($NUVEL_MCP_PUBLIC_URL/mcp/k/<key>) once, "
                               "for MCP clients that connect with no sign-in.")
    p_create.set_defaults(func=_cmd_client_create)
    p_list = client_sub.add_parser("list", help="List registered clients (no secrets).")
    p_list.add_argument("--json", action="store_true", help="Print JSON.")
    p_list.set_defaults(func=_cmd_client_list)
    p_delete = client_sub.add_parser("delete", help="Delete a client and revoke its tokens.")
    p_delete.add_argument("client_id")
    p_delete.set_defaults(func=_cmd_client_delete)

    p_token = subparsers.add_parser("token", help="Manage OAuth tokens of the HTTP server.")
    token_sub = p_token.add_subparsers(dest="token_command", required=True)
    p_revoke = token_sub.add_parser(
        "revoke",
        help="Revoke an access/refresh token, a URL key (or connector URL), or "
             "every token and URL key of a client id.",
    )
    p_revoke.add_argument("value", metavar="TOKEN_KEY_OR_CLIENT_ID")
    p_revoke.set_defaults(func=_cmd_token_revoke)


def _check_tools(tools: frozenset[str] | None) -> bool:
    """Warn about unknown ``--tools`` names; False when none is a real tool."""
    from nuvel_mcp.server import FEEDBACK_TOOLS, HUB_TOOLS

    if tools is None:
        return True
    known = HUB_TOOLS | FEEDBACK_TOOLS
    unknown = sorted(tools - known)
    if unknown:
        print(f"Warning: --tools: unknown tool name(s) ignored: {', '.join(unknown)} "
              f"(known tools: {', '.join(sorted(known))}).", file=sys.stderr)
    if not tools & known:
        print("Error: --tools names no known tool, so nothing would be served.",
              file=sys.stderr)
        return False
    return True


def _warn_http_only_flags(args: argparse.Namespace) -> None:
    """With --transport stdio, say so when HTTP-only flags were given."""
    given = [flag for flag, set_ in (
        ("--host", args.host != _DEFAULT_HOST),
        ("--port", args.port != _DEFAULT_PORT),
        ("--allow-remote", args.allow_remote),
    ) if set_]
    if given:
        print(f"Warning: {', '.join(given)} only apply to --transport http; "
              "ignored with --transport stdio.", file=sys.stderr)


def run_serve(args: argparse.Namespace) -> int:
    from nuvel_mcp.feedback import resolve_feedback_dir
    from nuvel_mcp.server import SkillsMCPServer
    from nuvel_mcp.skills_loader import SkillsError, SkillsLoader
    from nuvel_mcp.recipes import load_recipe_store
    from nuvel_mcp.traces import load_trace_store

    if not _check_tools(args.tools):
        return 2

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

    recipe_store = None
    if args.recipe_store != "local":
        try:
            recipe_store = load_recipe_store(
                args.recipe_store, resolve_feedback_dir(args.feedback_dir)
            )
        except ValueError as exc:
            print(f"Error: invalid --recipe-store: {exc}", file=sys.stderr)
            return 1

    server = SkillsMCPServer(
        loader,
        theme=args.theme,
        require_filter=require_filter,
        with_org_memory=args.with_org_memory,
        feedback_dir=args.feedback_dir,
        tools=args.tools,
        trace_store=trace_store,
        recipe_store=recipe_store,
    )
    if args.require_auth and args.no_auth:
        print("Error: --require-auth and --no-auth are mutually exclusive.", file=sys.stderr)
        return 2
    if args.transport == "stdio":
        if args.no_auth:
            print("Error: --no-auth applies to --transport http; stdio is ungated "
                  "unless --require-auth is set.", file=sys.stderr)
            return 2
        _warn_http_only_flags(args)
        if args.require_auth:
            error = _gate_stdio(server)
            if error:
                print(f"Error: {error}", file=sys.stderr)
                return 2
        server.serve()
        return 0

    from nuvel_mcp.http_transport import HttpConfig, is_loopback_host, serve_http

    loopback = is_loopback_host(args.host)
    if not loopback and not args.allow_remote:
        print(
            f"Error: refusing to bind non-loopback address {args.host!r} without "
            "--allow-remote. The HTTP transport is meant to stay on 127.0.0.1 "
            "unless you deliberately expose it.",
            file=sys.stderr,
        )
        return 2
    try:
        config = HttpConfig.from_env()
    except ValueError as exc:
        print(f"Error: invalid NUVEL_MCP_* setting: {exc}", file=sys.stderr)
        return 2

    if args.no_auth:
        if not loopback:
            print(f"Error: --no-auth is refused on non-loopback address {args.host!r}; "
                  "it is for local development only.", file=sys.stderr)
            return 2
        print("WARNING: --no-auth: OAuth is OFF, every MCP method and tool (writes "
              f"included) is open to anything that can reach {args.host}:{args.port}.",
              file=sys.stderr)
        provider = None
    else:
        provider, error = _oauth_provider_for_http(loopback, config.public_url)
        if error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
    try:
        serve_http(server, args.host, args.port, config, provider, no_auth=args.no_auth)
    except OSError as exc:
        print(f"Error: cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1
    return 0


def _oauth_provider_for_http(loopback: bool, public_url: str | None):
    """Build the provider for gated HTTP, or return (None, error)."""
    from nuvel_mcp.oauth import LOGIN_SETTINGS, OAuthConfig, OAuthProvider, OAuthStoreError

    config = OAuthConfig.from_env()
    if config.login not in LOGIN_SETTINGS:
        return None, (f"NUVEL_OAUTH_LOGIN={config.login!r} is not supported: set it to "
                      "'none' (URL keys and client_credentials only) or leave it unset.")
    mode = config.login_mode
    if mode is None:
        return None, ("the HTTP transport is gated by OAuth and needs a login provider: set "
                      "NUVEL_OAUTH_GITHUB_CLIENT_ID and NUVEL_OAUTH_GITHUB_CLIENT_SECRET "
                      "(a GitHub OAuth app whose callback is <origin>/oauth/callback), or "
                      "NUVEL_OAUTH_LOGIN=none to accept only URL keys and client_credentials, "
                      "or NUVEL_OAUTH_DEV_LOGIN=1 for local tests, or pass --no-auth on loopback.")
    if mode == "dev" and not loopback:
        return None, "NUVEL_OAUTH_DEV_LOGIN=1 is for local tests and is refused on a non-loopback bind."
    if not loopback and not public_url:
        return None, ("a non-loopback bind needs NUVEL_MCP_PUBLIC_URL (e.g. "
                      "https://skills.example.com): it is the OAuth issuer and token audience.")
    provider = OAuthProvider(config)
    try:
        with provider.store.reading():
            pass  # fail fast on an unreadable store
    except OAuthStoreError as exc:
        return None, str(exc)
    if mode == "none":
        ignored = [what for what, set_ in (
            ("GitHub credentials", config.github_client_id or config.github_client_secret),
            ("NUVEL_OAUTH_DEV_LOGIN", config.dev_login),
        ) if set_]
        if ignored:
            print(f"Warning: NUVEL_OAUTH_LOGIN=none: {' and '.join(ignored)} are ignored; "
                  "there is no interactive login.", file=sys.stderr)
        print("NUVEL_OAUTH_LOGIN=none: no interactive login; clients connect with a URL key "
              "(nuvel-mcp client create --url-key) or client_credentials.", file=sys.stderr)
        return provider, None
    if config.dev_login and mode == "github":
        print("Warning: NUVEL_OAUTH_DEV_LOGIN is ignored because GitHub credentials are set.",
              file=sys.stderr)
    if mode == "dev":
        print("WARNING: NUVEL_OAUTH_DEV_LOGIN=1: the login page accepts any username without "
              "a password. For local tests only.", file=sys.stderr)
    if config.allowed_users is None:
        print("Warning: NUVEL_OAUTH_ALLOWED_USERS is not set, so ANY user who completes "
              "the login can get a token. Set it to a comma-separated list of GitHub logins.",
              file=sys.stderr)
    return provider, None


def _gate_stdio(server) -> str | None:
    from nuvel_mcp.oauth import OAuthConfig, OAuthProvider, public_url_from_env, stdio_gate

    try:
        resource = public_url_from_env()
    except ValueError as exc:
        return str(exc)
    provider = OAuthProvider(OAuthConfig.from_env())
    token = os.environ.get("NUVEL_MCP_ACCESS_TOKEN", "").strip() or None
    if token is None:
        print("Warning: --require-auth without NUVEL_MCP_ACCESS_TOKEN: every request "
              "will be refused.", file=sys.stderr)
    server.gate = stdio_gate(provider, token, resource)
    return None


def _provider():
    from nuvel_mcp.oauth import OAuthConfig, OAuthProvider

    return OAuthProvider(OAuthConfig.from_env())


def _store_errors(func):
    """Report an unreadable OAuth store as an error instead of a traceback."""

    def wrapper(args: argparse.Namespace) -> int:
        from nuvel_mcp.oauth import OAuthStoreError

        try:
            return func(args)
        except OAuthStoreError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    return wrapper


@_store_errors
def _cmd_client_create(args: argparse.Namespace) -> int:
    if args.url_key:
        return _create_url_key_client(args)
    provider = _provider()
    try:
        record, secret = provider.create_client(args.name, args.redirect_uri)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "client_id": record["client_id"],
        "client_secret": secret,
        "client_name": record["client_name"],
        "grant_types": record["grant_types"],
        "redirect_uris": record["redirect_uris"],
        "token_endpoint_auth_method": record["token_endpoint_auth_method"],
    }, indent=2))
    print(f"Store the client_secret now: only its hash is kept in {provider.store.path}.",
          file=sys.stderr)
    return 0


def _create_url_key_client(args: argparse.Namespace) -> int:
    from nuvel_mcp.oauth import URL_KEY_PATH, public_url_from_env

    if args.redirect_uri:
        print("Error: --url-key and --redirect-uri are mutually exclusive: a URL-key "
              "client never uses the authorization code flow.", file=sys.stderr)
        return 2
    try:
        public_url = public_url_from_env()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    provider = _provider()
    record, key = provider.create_client(args.name, url_key=True)
    print(json.dumps({
        "client_id": record["client_id"],
        "client_name": record["client_name"],
        "url_key": key,
        "connector_url": f"{public_url or ''}{URL_KEY_PATH}{key}",
    }, indent=2))
    if public_url is None:
        print("NUVEL_MCP_PUBLIC_URL is not set: prefix connector_url with the server's "
              "public origin.", file=sys.stderr)
    print(f"Give connector_url to this tester now: only the key's hash is kept in "
          f"{provider.store.path}. Revoke it with: nuvel-mcp token revoke {record['client_id']}",
          file=sys.stderr)
    return 0


@_store_errors
def _cmd_client_list(args: argparse.Namespace) -> int:
    clients = _provider().list_clients()
    if args.json:
        print(json.dumps(clients, indent=2))
        return 0
    if not clients:
        print("No clients registered.")
        return 0
    print(f"{'CLIENT_ID':<28} {'NAME':<24} {'TYPE':<12} {'SOURCE':<6} {'TOKENS':>6}  CREATED")
    for c in clients:
        created = time.strftime("%Y-%m-%d %H:%M", time.gmtime(c.get("client_id_issued_at", 0)))
        kind = ("url-key" if c.get("url_key")
                else "confidential" if c["confidential"] else "public")
        print(f"{c['client_id']:<28} {(c.get('client_name') or '-')[:24]:<24} {kind:<12} "
              f"{c.get('source', '?'):<6} {c['active_access_tokens']:>6}  {created}")
    return 0


@_store_errors
def _cmd_client_delete(args: argparse.Namespace) -> int:
    if not _provider().delete_client(args.client_id):
        print(f"Error: no client {args.client_id!r}.", file=sys.stderr)
        return 1
    print(f"Deleted client {args.client_id} and revoked its tokens.")
    return 0


@_store_errors
def _cmd_token_revoke(args: argparse.Namespace) -> int:
    result = _provider().revoke(args.value)
    if result is None:
        print("Error: no matching token or client id.", file=sys.stderr)
        return 1
    print(f"Revoked {result['access_tokens']} access token(s), "
          f"{result['refresh_tokens']} refresh token(s) and "
          f"{result['url_keys']} URL key(s) "
          f"({result['kind']}, client {result['client_id']}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nuvel-mcp", description="Nuvel Skills MCP server.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_serve = sub.add_parser("serve", help="Start the Nuvel Skills MCP server (stdio or HTTP).")
    add_serve_arguments(p_serve)
    p_serve.set_defaults(func=run_serve)
    add_oauth_commands(sub)
    args = parser.parse_args(argv)
    return args.func(args)
