"""
scaffold.py — The Stamping Script

Copies the template skeleton from meta_agent/templates/ into a target
directory, renaming the {{agent_package}} directory and replacing all
placeholders in files.

Usage:
    python scaffold.py <agent-name> [--output-dir DIR] [--description DESC]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "meta_agent" / "templates"

PLACEHOLDER_TAG = "{{agent_package}}"

# Extensions considered text (placeholder substitution applied)
TEXT_EXTENSIONS = frozenset({
    ".py", ".md", ".txt", ".yaml", ".yml", ".toml",
    ".cfg", ".ini", ".env", ".example",
})


# ── Validation ──────────────────────────────────────────────────────


_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def validate_agent_name(name: str) -> str:
    """Validate a kebab-case agent name.

    Rules:
    - Lowercase letters, digits, and hyphens only
    - Must start with a letter
    - Cannot end with a hyphen
    - No consecutive hyphens
    - Max 40 characters

    Returns the validated name on success; raises ValueError otherwise.
    """
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


# ── Placeholder replacement ─────────────────────────────────────────


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


# ── Scaffolding ─────────────────────────────────────────────────────


def scaffold_agent(
    name: str,
    output_dir: str | None = None,
    description: str = "",
    system_prompt: str = "",
) -> dict:
    """Scaffold a new agent from the template skeleton.

    Returns a dict with status and metadata.
    """
    # 1. Validate
    try:
        name = validate_agent_name(name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    package = name.replace("-", "_")
    description = description or f"ADK agent: {name}"
    system_prompt = system_prompt or "You are a helpful AI assistant."

    # 2. Resolve output directory
    if output_dir is None:
        output_dir = os.environ.get("AGENTS_OUTPUT_DIR", "./generated-agents")
    target = Path(output_dir) / name

    # 3. Check target doesn't already exist
    if target.exists():
        return {
            "status": "error",
            "message": f"Directory already exists: {target}",
        }

    replacements = _build_replacements(name, package, description, system_prompt)
    files_created: list[str] = []

    try:
        # 4. Walk template tree
        for dirpath, dirnames, filenames in os.walk(TEMPLATES_DIR):
            rel_dir = Path(dirpath).relative_to(TEMPLATES_DIR)
            # Replace {{agent_package}} in directory path segments
            dest_dir = target / Path(
                *[_substitute(part, replacements) for part in rel_dir.parts]
            ) if rel_dir.parts else target
            dest_dir.mkdir(parents=True, exist_ok=True)

            for fname in filenames:
                # Skip .gitkeep
                if fname == ".gitkeep":
                    continue

                src_file = Path(dirpath) / fname

                # Determine destination filename
                dest_fname = _substitute(fname, replacements)
                is_tmpl = dest_fname.endswith(".tmpl")
                if is_tmpl:
                    dest_fname = dest_fname[: -len(".tmpl")]

                dest_file = dest_dir / dest_fname

                # Decide whether to substitute content
                suffix = Path(dest_fname).suffix
                if is_tmpl or suffix in TEXT_EXTENSIONS:
                    content = src_file.read_text(encoding="utf-8")
                    content = _substitute(content, replacements)
                    dest_file.write_text(content, encoding="utf-8")
                else:
                    shutil.copy2(str(src_file), str(dest_file))

                files_created.append(str(dest_file.relative_to(target)))

        return {
            "status": "ok",
            "path": str(target),
            "agent_name": name,
            "package_name": package,
            "files_created": len(files_created),
            "files": files_created,
        }

    except Exception as exc:
        # Clean up partial output
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        return {"status": "error", "message": str(exc)}


# ── CLI ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scaffold a new ADK agent from the template skeleton."
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
