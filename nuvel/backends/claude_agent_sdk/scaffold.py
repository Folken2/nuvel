"""
scaffold.py — The Claude Agent SDK Stamping Script

Copies the template skeleton from nuvel/backends/claude_agent_sdk/templates/
into a target directory, renames the {{agent_package}} directory, and
substitutes placeholders.

Usage:
    python -m nuvel.backends.claude_agent_sdk.scaffold <agent-name>
                                                       [--output-dir DIR]
                                                       [--description DESC]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "templates"

PLACEHOLDER_TAG = "{{agent_package}}"

TEXT_EXTENSIONS = frozenset({
    ".py", ".md", ".txt", ".yaml", ".yml", ".toml",
    ".cfg", ".ini", ".env", ".example", ".html", ".json",
})


_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def validate_agent_name(name: str) -> str:
    """Validate a kebab-case agent name."""
    if not name:
        raise ValueError("Agent name must not be empty.")
    if len(name) > 40:
        raise ValueError(f"Agent name too long ({len(name)} chars, max 40).")
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid agent name '{name}'. "
            "Use lowercase letters, digits, and single hyphens; "
            "must start with a letter and cannot end with a hyphen."
        )
    return name


def _build_replacements(
    name: str,
    package: str,
    description: str,
    system_prompt: str,
) -> dict[str, str]:
    return {
        "{{agent_package}}": package,
        "{{agent_name}}": name,
        "{{agent_name_snake}}": package,
        "{{agent_description}}": description,
        "{{agent_system_prompt}}": system_prompt,
    }


def _substitute(text: str, replacements: dict[str, str]) -> str:
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


def _stamp_tree(
    src_root: Path,
    target: Path,
    replacements: dict[str, str],
    files_created: list[str],
) -> None:
    if not src_root.is_dir():
        return
    for dirpath, _dirnames, filenames in os.walk(src_root):
        rel_dir = Path(dirpath).relative_to(src_root)
        dest_dir = target / Path(
            *[_substitute(part, replacements) for part in rel_dir.parts]
        ) if rel_dir.parts else target
        dest_dir.mkdir(parents=True, exist_ok=True)

        for fname in filenames:
            if fname == ".gitkeep":
                continue

            src_file = Path(dirpath) / fname
            dest_fname = _substitute(fname, replacements)
            is_tmpl = dest_fname.endswith(".tmpl")
            if is_tmpl:
                dest_fname = dest_fname[: -len(".tmpl")]
            dest_file = dest_dir / dest_fname

            suffix = Path(dest_fname).suffix
            if is_tmpl or suffix in TEXT_EXTENSIONS:
                content = src_file.read_text(encoding="utf-8")
                content = _substitute(content, replacements)
                dest_file.write_text(content, encoding="utf-8")
            else:
                shutil.copy2(str(src_file), str(dest_file))

            rel = str(dest_file.relative_to(target))
            if rel not in files_created:
                files_created.append(rel)


def scaffold_agent(
    name: str,
    output_dir: str | None = None,
    description: str = "",
    system_prompt: str = "",
    persona: bool = False,
    with_composio: bool = False,
    with_slack: bool = False,
    with_telegram: bool = False,
    with_teams: bool = False,
    workflow: bool = False,
    with_acp: bool = False,
    with_eval: bool = False,
) -> dict:
    """Scaffold a Claude Agent SDK project from the template skeleton.

    The persona and with_composio flags are ADK-only; passing them here
    returns an error rather than silently ignoring.
    """
    if with_acp:
        return {
            "status": "error",
            "message": "--with-acp is an ADK-only bundle; use --framework adk if you want it.",
        }
    if with_eval:
        return {
            "status": "error",
            "message": "--with-eval is not yet supported for the claude-agent-sdk backend. Use --framework adk.",
        }
    if persona:
        return {
            "status": "error",
            "message": "--persona is an ADK-only bundle; use --framework adk if you want it.",
        }
    if with_composio:
        return {
            "status": "error",
            "message": "--with-composio is an ADK-only bundle; use --framework adk if you want it.",
        }
    if workflow:
        return {
            "status": "error",
            "message": "--workflow is an ADK-only bundle (ADK 2.0 Workflow graphs); use --framework adk if you want it.",
        }
    for flag_name, flag_set in (("with-slack", with_slack), ("with-telegram", with_telegram), ("with-teams", with_teams)):
        if flag_set:
            return {
                "status": "error",
                "message": f"--{flag_name} is not yet supported for the claude-agent-sdk backend. Use --framework adk.",
            }

    try:
        name = validate_agent_name(name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    package = name.replace("-", "_")
    description = description or f"Claude Agent SDK agent: {name}"
    system_prompt = system_prompt or (
        "You are a helpful AI assistant. Use your tools to take action."
    )

    if output_dir is None:
        output_dir = os.environ.get("AGENTS_OUTPUT_DIR", "./generated-agents")
    target = Path(output_dir) / name

    if target.exists():
        return {
            "status": "error",
            "message": f"Directory already exists: {target}",
        }

    replacements = _build_replacements(name, package, description, system_prompt)
    files_created: list[str] = []

    try:
        _stamp_tree(TEMPLATES_DIR, target, replacements, files_created)

        return {
            "status": "ok",
            "path": str(target),
            "agent_name": name,
            "package_name": package,
            "files_created": len(files_created),
            "files": files_created,
            "persona": False,
            "with_composio": False,
        }

    except Exception as exc:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        return {"status": "error", "message": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scaffold a new Claude Agent SDK project from the template skeleton."
    )
    parser.add_argument("agent_name", help="Kebab-case agent name (e.g. my-agent)")
    parser.add_argument("--output-dir", default=None, help="Parent directory for the new agent")
    parser.add_argument("--description", default="", help="Short agent description")
    args = parser.parse_args()

    result = scaffold_agent(
        name=args.agent_name,
        output_dir=args.output_dir,
        description=args.description,
    )

    if result["status"] == "ok":
        print(f"Agent scaffolded at: {result['path']}")
        print(f"Files created: {result['files_created']}")
    else:
        print(f"Error: {result['message']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
