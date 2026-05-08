"""nuvel — command-line interface.

Subcommands:
    nuvel new <name> --framework <fw>
        Scaffold a new agent. Default framework: adk.
    nuvel skills list [--framework <fw>]
        List bundled knowledge skills for a framework.
    nuvel skills search <q> [--framework <fw>]
        Search skills by name or description.
    nuvel run [--dev]
        Launch the meta-agent (ADK-based) for autonomous scaffolding.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml

DEFAULT_FRAMEWORK = "adk"
SUPPORTED_FRAMEWORKS = ("adk", "claude-agent-sdk", "anthropic-managed-agents")
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
        if flags:
            print(f"Bundles: {', '.join(flags)}")
        return 0
    print(f"Error: {result['message']}", file=sys.stderr)
    return 1


def _load_skills(framework: str) -> list[dict]:
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


def _cmd_run(args: argparse.Namespace) -> int:
    env = os.environ.copy()
    if args.dev:
        env["DEV_MODE"] = "true"
    return subprocess.call([sys.executable, "-m", "nuvel.run_adk"], env=env)


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

    p_run = sub.add_parser("run", help="Launch the meta-agent server.")
    p_run.add_argument("--dev", action="store_true", help="Run with DEV_MODE=true.")
    p_run.set_defaults(func=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
