"""nuvel — command-line interface.

Subcommands:
    nuvel new <name> --framework <fw>
        Scaffold a new agent. Default framework: adk.
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
    nuvel mcp serve [--skills-dir <dir>]
        Serve a skills hub to MCP clients over stdio (resources + tools).
    nuvel bots list|create|delete|chat|info|logs|send
        Manage and talk to Hermes-backed bots.
    nuvel fleet deploy|list|status|destroy
        Deploy and manage fleets of bots from a YAML manifest.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_FRAMEWORK = "adk"
SUPPORTED_FRAMEWORKS = (
    "adk", "claude-agent-sdk", "anthropic-managed-agents", "buzz", "hermes",
)
_BACKENDS_DIR = Path(__file__).resolve().parent / "backends"


def _backend_module(framework: str) -> str:
    return framework.replace("-", "_")


def _skills_dir(framework: str) -> Path:
    return _BACKENDS_DIR / _backend_module(framework) / "skills"


def _scaffold_agent_for(framework: str):
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


def _cmd_new(args: argparse.Namespace) -> int:
    scaffold_agent = _scaffold_agent_for(args.framework)

    result = scaffold_agent(
        name=args.name,
        output_dir=args.output_dir,
        description=args.description,
        system_prompt=args.system_prompt,
        persona=args.persona,
        with_composio=args.with_composio,
        with_slack=args.with_slack,
        with_telegram=args.with_telegram,
        with_teams=args.with_teams,
        workflow=args.workflow,
        with_acp=args.with_acp,
        with_eval=args.with_eval,
    )
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
    print(f"Error: {result['message']}", file=sys.stderr)
    return 1


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
    _print_skills(_load_skills(args.framework))
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


def _cmd_plugins_list(args: argparse.Namespace) -> int:
    from nuvel.agent_plugins import PluginRegistry
    from nuvel.config import get_plugin_dirs

    registry = PluginRegistry(get_plugin_dirs())
    registry.discover_plugins()
    _print_plugins(registry)
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
    _print_plugins(registry)
    return 0


def _cmd_plugins_config(args: argparse.Namespace) -> int:
    from nuvel.config import get_plugin_dirs

    dirs = get_plugin_dirs()
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

    cwd = Path(args.path).resolve() if args.path else None
    return run_doctor(cwd=cwd)


def _add_framework_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--framework", "-f",
        choices=SUPPORTED_FRAMEWORKS,
        default=DEFAULT_FRAMEWORK,
        help=f"Agent framework (default: {DEFAULT_FRAMEWORK}).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nuvel",
        description="nuvel — scaffold, run, and explore agents across frameworks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new", help="Scaffold a new agent.")
    p_new.add_argument("name", help="Kebab-case agent name (e.g. my-agent).")
    _add_framework_flag(p_new)
    p_new.add_argument("--output-dir", default=None, help="Parent directory for the new agent.")
    p_new.add_argument("--description", default="", help="Short agent description.")
    p_new.add_argument("--system-prompt", default="", help="System prompt for the new agent.")
    p_new.add_argument(
        "--persona", action="store_true",
        help="(adk only) Activate the persona overlay: self-rewriting SOUL.md, "
             "AWAKENING.md, author_skill, complete_awakening. For agents meant "
             "to live and grow over time. Inappropriate for stateless task bots.",
    )
    p_new.add_argument(
        "--with-composio", action="store_true",
        help="(adk only) Wire the Composio Tool Router MCP "
             "(~1000 toolkits via one hosted endpoint).",
    )
    p_new.add_argument(
        "--with-slack", action="store_true",
        help="(adk only) Add a Slack gateway via Composio Slackbot. Implies --with-composio.",
    )
    p_new.add_argument(
        "--with-telegram", action="store_true",
        help="(adk, hermes) Add a Telegram gateway — a webhook + Bot API "
             "sidecar for adk, the platforms.telegram config block for hermes.",
    )
    p_new.add_argument(
        "--with-teams", action="store_true",
        help="(adk only) Add an MS Teams gateway (aiohttp sidecar via Microsoft 365 Agents SDK).",
    )
    p_new.add_argument(
        "--workflow", action="store_true",
        help="(adk only) Generate a workflow-native agent: the root agent is an "
             "ADK 2.0 Workflow graph (agent_workflow.py) with task-mode nodes, "
             "typed contracts, and conditional routing, instead of a single LlmAgent.",
    )
    p_new.add_argument(
        "--with-acp", action="store_true",
        help="(adk only) Make the agent ACP-compatible and CLI-runnable: add an "
             "Agent Client Protocol adapter (stdio JSON-RPC, python -m <pkg>.acp) "
             "plus a local terminal CLI (python -m <pkg>.cli).",
    )
    p_new.add_argument(
        "--with-eval", action="store_true",
        help="(adk only) Stamp a starter evalv2 suite into the agent "
             "(skills/default/eval/suite.yaml + a sample example). Run it with "
             "`nuvel evalv2 run`.",
    )
    p_new.set_defaults(func=_cmd_new)

    p_skills = sub.add_parser("skills", help="Inspect bundled skills.")
    skills_sub = p_skills.add_subparsers(dest="skills_command", required=True)

    p_list = skills_sub.add_parser("list", help="List all bundled skills.")
    _add_framework_flag(p_list)
    p_list.set_defaults(func=_cmd_skills_list)

    p_search = skills_sub.add_parser("search", help="Search skills by keyword.")
    _add_framework_flag(p_search)
    p_search.add_argument("query", help="Substring to match against skill name/description.")
    p_search.set_defaults(func=_cmd_skills_search)

    p_plugins = sub.add_parser("plugins", help="Discover and inspect Agent Plugins.")
    plugins_sub = p_plugins.add_subparsers(dest="plugins_command", required=True)

    p_pl_list = plugins_sub.add_parser(
        "list", help="List all plugins found in the configured plugin_dirs."
    )
    p_pl_list.set_defaults(func=_cmd_plugins_list)

    p_pl_load = plugins_sub.add_parser(
        "load", help="Load and inspect a single plugin directory."
    )
    p_pl_load.add_argument("dir", help="Path to a plugin directory (with plugin.json).")
    p_pl_load.set_defaults(func=_cmd_plugins_load)

    p_pl_config = plugins_sub.add_parser(
        "config", help="Show the current plugin directory configuration."
    )
    p_pl_config.set_defaults(func=_cmd_plugins_config)

    p_run = sub.add_parser("run", help="Launch the meta-agent server.")
    p_run.add_argument("--dev", action="store_true", help="Run with DEV_MODE=true.")
    p_run.set_defaults(func=_cmd_run)

    p_doctor = sub.add_parser(
        "doctor",
        help="Diagnose the nuvel install and the agent in the current directory.",
    )
    p_doctor.add_argument(
        "--path", default=None,
        help="Directory to inspect (defaults to current working directory).",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    from nuvel import traces_cli
    traces_cli.register(sub)

    from nuvel import pricing
    pricing.register(sub)

    from nuvel.commands import mcp_serve
    mcp_serve.register(sub)

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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
