"""nuvel — command-line interface for the meta-agent.

Subcommands:
    nuvel new <name>            Scaffold a new ADK agent.
    nuvel skills list           List bundled skills.
    nuvel skills search <q>     Search skills by name or description.
    nuvel run                   Launch the meta-agent (delegates to run_adk.py).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _cmd_new(args: argparse.Namespace) -> int:
    from scaffold import scaffold_agent

    result = scaffold_agent(
        name=args.name,
        output_dir=args.output_dir,
        description=args.description,
        system_prompt=args.system_prompt,
    )
    if result["status"] == "ok":
        print(f"Agent scaffolded at: {result['path']}")
        print(f"Files created: {result['files_created']}")
        return 0
    print(f"Error: {result['message']}", file=sys.stderr)
    return 1


def _load_skills() -> list[dict]:
    skills: list[dict] = []
    if not SKILLS_DIR.is_dir():
        return skills
    for entry in sorted(SKILLS_DIR.iterdir()):
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
    _print_skills(_load_skills())
    return 0


def _cmd_skills_search(args: argparse.Namespace) -> int:
    query = args.query.lower()
    matches = [
        s
        for s in _load_skills()
        if query in s["slug"].lower()
        or query in s["name"].lower()
        or query in s["description"].lower()
    ]
    _print_skills(matches)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    entry = REPO_ROOT / "run_adk.py"
    if not entry.is_file():
        print(f"Error: {entry} not found.", file=sys.stderr)
        return 1
    env = os.environ.copy()
    if args.dev:
        env["DEV_MODE"] = "true"
    return subprocess.call([sys.executable, str(entry)], env=env)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nuvel",
        description="nuvel — scaffold, run, and explore meta-agent projects.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new", help="Scaffold a new ADK agent.")
    p_new.add_argument("name", help="Kebab-case agent name (e.g. my-agent).")
    p_new.add_argument("--output-dir", default=None, help="Parent directory for the new agent.")
    p_new.add_argument("--description", default="", help="Short agent description.")
    p_new.add_argument("--system-prompt", default="", help="System prompt for the new agent.")
    p_new.set_defaults(func=_cmd_new)

    p_skills = sub.add_parser("skills", help="Inspect bundled skills.")
    skills_sub = p_skills.add_subparsers(dest="skills_command", required=True)

    p_list = skills_sub.add_parser("list", help="List all bundled skills.")
    p_list.set_defaults(func=_cmd_skills_list)

    p_search = skills_sub.add_parser("search", help="Search skills by keyword.")
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
