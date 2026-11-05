"""nuvel — command-line interface.

Subcommands:
    nuvel agent create <name> --framework <fw>
        Scaffold a new agent. Default framework: adk.
    nuvel agent list [--output-dir <dir>]
        List the agents in the output directory.
    nuvel skills list [--framework <fw>]
        List bundled knowledge skills for a framework.
    nuvel skills search <q> [--framework <fw>]
        Search skills by name or description.
    nuvel plugins list
        List Agent Plugins discovered in the configured plugin_dirs.
    nuvel plugins load <dir>
        Load and inspect a single plugin directory.
    nuvel plugins config
        Show the current plugin directory configuration.
    nuvel run [--dev]
        Launch the meta-agent (ADK-based) for autonomous scaffolding.
    nuvel traces list|show|stats
        Inspect JSONL trace logs across all local agents.
    nuvel pricing list|sync|add <model>
        Inspect and sync model pricing.json against OpenRouter.
    nuvel dashboard
        Open the local web dashboard over your trace logs.
    nuvel mcp serve [--skills-dir <dir>] [--transport stdio|http] [--host H] [--port P] [--allow-remote]
        Serve a skills hub to MCP clients over stdio or HTTP (resources + tools).
        HTTP is gated by OAuth 2.1 (--no-auth for loopback dev); stdio by --require-auth.
    nuvel mcp client create|list|delete, nuvel mcp token revoke
        Manage the MCP server's OAuth clients and tokens.
    nuvel bots list|create|delete|chat|info|logs|send
        Manage and talk to Hermes-backed bots.
    nuvel fleet deploy|list|status|destroy
        Deploy and manage fleets of bots from a YAML manifest.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_FRAMEWORK = "adk"
SUPPORTED_FRAMEWORKS = ("adk", "hermes", "claude-agent-sdk")
DEPRECATED_FRAMEWORKS = ("anthropic-managed-agents",)
# Frozen: still dispatchable for anyone who knows the name, but left out of the
# usage line, the help listing and the "invalid choice" error.
FROZEN_COMMANDS = ("acp",)
_BACKENDS_DIR = Path(__file__).resolve().parent / "backends"


class _VisibleChoices(dict):
    """Subcommand map that accepts frozen names but never lists them."""

    def __iter__(self):
        return (name for name in super().__iter__() if name not in FROZEN_COMMANDS)


def _backend_module(framework: str) -> str:
    return framework.replace("-", "_")


def _skills_dir(framework: str) -> Path:
    return _BACKENDS_DIR / _backend_module(framework) / "skills"


def _scaffold_agent_for(framework: str):
    if framework in DEPRECATED_FRAMEWORKS:
        print(
            f"\u26a0  Warning: '{framework}' is deprecated and will be removed in a "
            f"future version. Use one of: {', '.join(SUPPORTED_FRAMEWORKS)}.",
            file=sys.stderr,
        )
    if framework == "adk":
        from nuvel.backends.adk.scaffold import scaffold_agent
        return scaffold_agent
    if framework == "claude-agent-sdk":
        from nuvel.backends.claude_agent_sdk.scaffold import scaffold_agent
        return scaffold_agent
    if framework == "anthropic-managed-agents":
        from nuvel.backends.anthropic_managed_agents.scaffold import scaffold_agent
        return scaffold_agent
    if framework == "buzz":
        from nuvel.backends.buzz.scaffold import scaffold_agent
        return scaffold_agent
    if framework == "hermes":
        from nuvel.backends.hermes.scaffold import scaffold_agent
        return scaffold_agent
    raise ValueError(f"Unknown framework: {framework}")


def _unsupported_message(flags: list[str], framework: str) -> str:
    verb = "is" if len(flags) == 1 else "are"
    return (
        f"{', '.join(flags)} {verb} not supported for the "
        f"{framework} backend. Use --framework {DEFAULT_FRAMEWORK}."
    )


def _scaffold_options() -> list[str]:
    """Everything `nuvel agent create` can hand a backend besides the agent name.

    Read from the parser, so a flag added to _add_new_agent_args is dispatched
    with no further wiring. Each is passed only if the backend declares it.
    """
    parser = argparse.ArgumentParser()
    _add_new_agent_args(parser)
    # The agent name is the parser's only required argument.
    return [
        option
        for option in vars(parser.parse_args(["placeholder"]))
        if option not in ("name", "framework")
    ]


def _scaffold_kwargs(scaffold_agent, args: argparse.Namespace) -> dict:
    """Build the kwargs a backend's scaffold_agent actually declares.

    Raises ValueError naming any flag the user set that the backend lacks.
    """
    accepted = inspect.signature(scaffold_agent).parameters
    options = _scaffold_options()
    unsupported = [
        "--" + option.replace("_", "-")
        for option in options
        if option not in accepted and getattr(args, option)
    ]
    if unsupported:
        raise ValueError(_unsupported_message(unsupported, args.framework))
    return {
        option: getattr(args, option)
        for option in options
        if option in accepted
    }


def _cmd_new(args: argparse.Namespace) -> int:
    # Answered here because the frozen flag's only supported home is ADK, and
    # a backend's own refusal may not name the backend.
    if args.with_acp and args.framework != DEFAULT_FRAMEWORK:
        print(f"Error: {_unsupported_message(['--with-acp'], args.framework)}", file=sys.stderr)
        return 1
    if args.with_acp:
        print("\u26a0  Warning: --with-acp is experimental and unsupported.", file=sys.stderr)

    scaffold_agent = _scaffold_agent_for(args.framework)

    try:
        kwargs = _scaffold_kwargs(scaffold_agent, args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    result = scaffold_agent(name=args.name, **kwargs)
    if result["status"] == "ok":
        print(f"Agent scaffolded at: {result['path']}")
        print(f"Files created: {result['files_created']}")
        print(f"Framework: {args.framework}")
        flags = []
        if result.get("persona"):
            flags.append("persona")
        if result.get("with_composio"):
            flags.append("composio")
        if result.get("workflow"):
            flags.append("workflow")
        if result.get("with_acp"):
            flags.append("acp")
        if result.get("with_eval"):
            flags.append("eval")
        if result.get("with_litellm"):
            flags.append("litellm")
        if result.get("with_buzz"):
            flags.append("buzz")
        if flags:
            print(f"Bundles: {', '.join(flags)}")
        channels = [
            ch for ch, key in (("slack", "with_slack"), ("telegram", "with_telegram"), ("teams", "with_teams"))
            if result.get(key)
        ]
        if channels:
            print(f"Channels: {', '.join(channels)}")
        return 0
    # Not every backend's refusal says which backend is refusing.
    print(f"Error: {args.framework} backend: {result['message']}", file=sys.stderr)
    return 1


def _cmd_agent_list(args: argparse.Namespace) -> int:
    from nuvel.doctor import detect_agent

    output_dir = Path(
        args.output_dir or os.environ.get("AGENTS_OUTPUT_DIR", "./generated-agents")
    )
    agents = []
    if output_dir.is_dir():
        for entry in sorted(output_dir.iterdir()):
            if not entry.is_dir():
                continue
            info = detect_agent(entry)
            if info.is_agent:
                agents.append(
                    {"name": entry.name, "framework": info.framework, "path": str(entry)}
                )
    if args.json:
        _print_json({"output_dir": str(output_dir), "agents": agents})
        return 0
    if not agents:
        print(f"No agents found in {output_dir}.")
        return 0
    width = max(len(a["name"]) for a in agents)
    for a in agents:
        print(f"  {a['name']:<{width}}  {a['framework'] or '-'}")
    return 0


def _load_skills(framework: str) -> list[dict]:
    import yaml  # local import: keeps the dependency-light commands import-free

    skills: list[dict] = []
    skills_dir = _skills_dir(framework)
    if not skills_dir.is_dir():
        return skills
    for entry in sorted(skills_dir.iterdir()):
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            continue
        text = skill_md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        _, _, rest = text.partition("---")
        front, _, _ = rest.partition("---")
        try:
            meta = yaml.safe_load(front) or {}
        except yaml.YAMLError:
            meta = {}
        skills.append(
            {
                "slug": entry.name,
                "name": meta.get("name", entry.name),
                "description": " ".join(
                    str(meta.get("description", "")).split()
                ),
                "path": str(skill_md),
            }
        )
    return skills


def _print_json(payload) -> None:
    """The whole `--json` view: one document on stdout."""
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _print_skills(skills: list[dict]) -> None:
    if not skills:
        print("No skills found.")
        return
    width = max(len(s["slug"]) for s in skills)
    for s in skills:
        desc = s["description"]
        if len(desc) > 80:
            desc = desc[:77] + "..."
        print(f"  {s['slug']:<{width}}  {desc}")


def _cmd_skills_list(args: argparse.Namespace) -> int:
    skills = _load_skills(args.framework)
    if args.json:
        _print_json({"framework": args.framework, "skills": skills})
    else:
        _print_skills(skills)
    return 0


def _cmd_skills_search(args: argparse.Namespace) -> int:
    query = args.query.lower()
    matches = [
        s
        for s in _load_skills(args.framework)
        if query in s["slug"].lower()
        or query in s["name"].lower()
        or query in s["description"].lower()
    ]
    if args.json:
        _print_json({"framework": args.framework, "query": args.query, "skills": matches})
    else:
        _print_skills(matches)
    return 0


def _print_plugins(registry) -> None:
    """Print a formatted view of loaded plugins, skills, and MCP servers."""
    plugins = registry.get_all_plugins()
    if not plugins:
        print("No plugins found.")
    for info in plugins:
        m = info.manifest
        header = m.name
        if m.version:
            header += f" v{m.version}"
        print(f"● {header}")
        if m.description:
            print(f"    {m.description}")
        if info.skills:
            print(f"    skills:  {', '.join(s.name for s in info.skills)}")
        for name, entry in info.mcp_servers.items():
            print(f"    mcp:     {name} ({entry.transport})")
        for err in info.errors:
            print(f"    ! {err.component}: {err.message}")
    for err in registry.errors:
        print(
            f"✗ {err.plugin_name}: {err.component}: {err.message}",
            file=sys.stderr,
        )


def _plugins_payload(registry) -> dict:
    """The `--json` view of what `_print_plugins` shows."""
    return {
        "plugins": [
            {
                "name": info.manifest.name,
                "version": info.manifest.version,
                "description": info.manifest.description,
                "root": str(info.root),
                "skills": [s.name for s in info.skills],
                "mcp_servers": [
                    {"name": name, "transport": entry.transport}
                    for name, entry in info.mcp_servers.items()
                ],
                "errors": [
                    {"component": err.component, "message": err.message}
                    for err in info.errors
                ],
            }
            for info in registry.get_all_plugins()
        ],
        "errors": [
            {"plugin": err.plugin_name, "component": err.component, "message": err.message}
            for err in registry.errors
        ],
    }


def _show_plugins(registry, args: argparse.Namespace) -> None:
    if args.json:
        _print_json(_plugins_payload(registry))
    else:
        _print_plugins(registry)


def _cmd_plugins_list(args: argparse.Namespace) -> int:
    from nuvel.agent_plugins import PluginRegistry
    from nuvel.config import get_plugin_dirs

    registry = PluginRegistry(get_plugin_dirs())
    registry.discover_plugins()
    _show_plugins(registry, args)
    return 0


def _cmd_plugins_load(args: argparse.Namespace) -> int:
    from nuvel.agent_plugins import PluginRegistry
    from nuvel.agent_plugins.exceptions import ManifestError

    path = Path(args.dir).expanduser()
    if not path.is_dir():
        print(f"Error: not a directory: {path}", file=sys.stderr)
        return 1

    registry = PluginRegistry([])
    try:
        registry.load_plugin(path)
    except ManifestError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _show_plugins(registry, args)
    return 0


def _cmd_plugins_config(args: argparse.Namespace) -> int:
    from nuvel.config import get_plugin_dirs

    dirs = get_plugin_dirs()
    if args.json:
        _print_json({
            "plugin_dirs": [{"path": str(d), "exists": d.is_dir()} for d in dirs],
        })
        return 0
    print("Plugin directories (set NUVEL_PLUGIN_DIRS to override):")
    for d in dirs:
        state = "exists" if d.is_dir() else "missing"
        print(f"  {d}  [{state}]")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    env = os.environ.copy()
    if args.dev:
        env["DEV_MODE"] = "true"
    return subprocess.call([sys.executable, "-m", "nuvel.run_adk"], env=env)


def _cmd_doctor(args: argparse.Namespace) -> int:
    from nuvel.doctor import run_doctor

    cwd = Path(args.path) if args.path else None
    return run_doctor(cwd=cwd, as_json=args.json)


def _add_doctor_parser(sub) -> None:
    p_doctor = sub.add_parser(
        "doctor",
        help="Diagnose the nuvel install and the agent in the current directory.",
    )
    p_doctor.add_argument(
        "--path", default=None,
        help="Directory to inspect (defaults to current working directory).",
    )
    p_doctor.add_argument(
        "--json", action="store_true",
        help="Emit the findings as JSON instead of a checklist.",
    )
    p_doctor.set_defaults(func=_cmd_doctor)


def _add_framework_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--framework", "-f",
        choices=SUPPORTED_FRAMEWORKS,
        default=DEFAULT_FRAMEWORK,
        help=(
            f"Agent framework (default: {DEFAULT_FRAMEWORK}). "
            f"Supported: {', '.join(SUPPORTED_FRAMEWORKS)}."
        ),
    )


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true",
        help="Print one JSON document on stdout instead of the text view.",
    )


def _add_new_agent_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments for `nuvel agent create` / `nuvel new`."""
    parser.add_argument("name", help="Kebab-case agent name (e.g. my-agent).")
    _add_framework_flag(parser)
    parser.add_argument("--output-dir", default=None, help="Parent directory for the new agent.")
    parser.add_argument("--description", default="", help="Short agent description.")
    parser.add_argument("--system-prompt", default="", help="System prompt for the new agent.")
    parser.add_argument(
        "--persona", action="store_true",
        help="(adk only) Activate the persona overlay: self-rewriting SOUL.md, "
             "AWAKENING.md, author_skill, complete_awakening. For agents meant "
             "to live and grow over time. Inappropriate for stateless task bots.",
    )
    parser.add_argument(
        "--with-composio", action="store_true",
        help="(adk only) Wire the Composio Tool Router MCP "
             "(~1000 toolkits via one hosted endpoint).",
    )
    parser.add_argument(
        "--with-slack", action="store_true",
        help="(adk only) Add a Slack gateway via Composio Slackbot. Implies --with-composio.",
    )
    parser.add_argument(
        "--with-telegram", action="store_true",
        help="(adk, hermes) Add a Telegram gateway: webhook + Bot API outbound on adk, "
             "platforms.telegram enabled in config.yaml on hermes.",
    )
    parser.add_argument(
        "--with-teams", action="store_true",
        help="(adk only) Add an MS Teams gateway (aiohttp sidecar via Microsoft 365 Agents SDK).",
    )
    parser.add_argument(
        "--workflow", action="store_true",
        help="(adk only) Generate a workflow-native agent: the root agent is an "
             "ADK 2.0 Workflow graph (agent_workflow.py) with task-mode nodes, "
             "typed contracts, and conditional routing, instead of a single LlmAgent.",
    )
    parser.add_argument(
        "--with-acp", action="store_true",
        help=argparse.SUPPRESS,  # frozen; _cmd_new warns it is unsupported
    )
    parser.add_argument(
        "--with-eval", action="store_true",
        help="(adk only) Stamp a starter evalv2 suite into the agent "
             "(skills/default/eval/suite.yaml + a sample example). Run it with "
             "`nuvel evalv2 run`.",
    )
    parser.add_argument(
        "--with-litellm", action="store_true",
        help="(adk only) Generate a ready-to-run LiteLLM proxy alongside the agent. "
             "LiteLLM acts as a model gateway for supervision: virtual keys, budgets, "
             "rate limits, fallbacks, spend tracking, and OpenTelemetry tracing. "
             "The agent routes LLM calls through the proxy instead of calling providers "
             "directly.",
    )


def _version() -> str:
    """The installed nuvel-cli version, from package metadata."""
    import importlib.metadata

    try:
        return importlib.metadata.version("nuvel-cli")
    except importlib.metadata.PackageNotFoundError:
        return "unknown (no installed package metadata)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nuvel",
        description="nuvel — scaffold, run, and explore agents across frameworks.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

# ── agent create (primary) ──────────────────────────────────────────
    p_agent = sub.add_parser("agent", help="Manage agents.")
    agent_sub = p_agent.add_subparsers(dest="agent_command", required=True)

    p_create = agent_sub.add_parser("create", help="Scaffold a new agent.")
    _add_new_agent_args(p_create)
    p_create.set_defaults(func=_cmd_new)

    p_agent_list = agent_sub.add_parser("list", help="List the agents in the output directory.")
    p_agent_list.add_argument(
        "--output-dir", default=None,
        help="Directory holding the agents (default: $AGENTS_OUTPUT_DIR or ./generated-agents).",
    )
    _add_json_flag(p_agent_list)
    p_agent_list.set_defaults(func=_cmd_agent_list)

    # ── new (deprecated alias for `agent create`) ───────────────────────
    p_new = sub.add_parser("new", help="[Deprecated] Use `nuvel agent create` instead.")
    _add_new_agent_args(p_new)
    p_new.set_defaults(func=_cmd_new)

    p_skills = sub.add_parser("skills", help="Inspect bundled skills.")
    skills_sub = p_skills.add_subparsers(dest="skills_command", required=True)

    p_list = skills_sub.add_parser("list", help="List all bundled skills.")
    _add_framework_flag(p_list)
    _add_json_flag(p_list)
    p_list.set_defaults(func=_cmd_skills_list)

    p_search = skills_sub.add_parser("search", help="Search skills by keyword.")
    _add_framework_flag(p_search)
    p_search.add_argument("query", help="Substring to match against skill name/description.")
    _add_json_flag(p_search)
    p_search.set_defaults(func=_cmd_skills_search)

    p_plugins = sub.add_parser("plugins", help="Discover and inspect Agent Plugins.")
    plugins_sub = p_plugins.add_subparsers(dest="plugins_command", required=True)

    p_pl_list = plugins_sub.add_parser(
        "list", help="List all plugins found in the configured plugin_dirs."
    )
    _add_json_flag(p_pl_list)
    p_pl_list.set_defaults(func=_cmd_plugins_list)

    p_pl_load = plugins_sub.add_parser(
        "load", help="Load and inspect a single plugin directory."
    )
    p_pl_load.add_argument("dir", help="Path to a plugin directory (with plugin.json).")
    _add_json_flag(p_pl_load)
    p_pl_load.set_defaults(func=_cmd_plugins_load)

    p_pl_config = plugins_sub.add_parser(
        "config", help="Show the current plugin directory configuration."
    )
    _add_json_flag(p_pl_config)
    p_pl_config.set_defaults(func=_cmd_plugins_config)

    p_run = sub.add_parser("run", help="Launch the meta-agent server.")
    p_run.add_argument("--dev", action="store_true", help="Run with DEV_MODE=true.")
    p_run.set_defaults(func=_cmd_run)

    _add_doctor_parser(sub)

    from nuvel import traces_cli
    traces_cli.register(sub)

    from nuvel import pricing
    pricing.register(sub)

    from nuvel.commands import mcp_serve
    mcp_serve.register(sub)

    from nuvel.commands import acp_serve
    acp_serve.register(sub)

    # These subcommands pull optional third-party deps (fastapi/uvicorn, the
    # eval stack). Register them when importable; skip cleanly when they're not,
    # so the stdlib-only commands above still work in a bare environment.
    try:
        from nuvel import dashboard
        dashboard.register(sub)
    except ImportError:
        pass

    try:
        from nuvel.eval import cli as eval_cli
        eval_cli.register(sub)
    except ImportError:
        pass

    from nuvel.bots import cli as bots_cli
    bots_cli.register(sub)
    bots_cli.register_fleet(sub)

    from nuvel.evalv2 import cli as evalv2_cli
    evalv2_cli.register(sub)

    sub.choices = _VisibleChoices(sub.choices)
    return parser


def _build_doctor_parser() -> argparse.ArgumentParser:
    """Just the doctor subcommand: it must run on the broken installs it diagnoses,
    which the imports behind the full parser would crash on."""
    parser = argparse.ArgumentParser(prog="nuvel")
    _add_doctor_parser(parser.add_subparsers(dest="command", required=True))
    return parser


def main(argv: list[str] | None = None) -> int:
    first = (sys.argv[1:] if argv is None else argv)[:1]
    parser = _build_doctor_parser() if first == ["doctor"] else build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
