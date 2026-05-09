"""
scaffold.py — The ADK Stamping Script

Copies the template skeleton from nuvel/backends/adk/templates/ into a target
directory, renames the {{agent_package}} directory, and substitutes placeholders.

Two optional feature bundles are activated via flags and applied as
overlays from nuvel/backends/adk/templates_overlays/<bundle>/:

    --persona         self-rewriting SOUL.md, AWAKENING.md, author_skill,
                      complete_awakening — for agents meant to live for
                      months and grow over time. NOT appropriate for
                      stateless task bots.

    --with-composio   Composio Tool Router MCP wiring (~1000 toolkits via
                      a single hosted MCP endpoint). Independent of
                      --persona.

Usage:
    python scaffold.py <agent-name> [--output-dir DIR] [--description DESC]
                                    [--persona] [--with-composio]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "templates"
OVERLAYS_DIR = Path(__file__).parent / "templates_overlays"

PLACEHOLDER_TAG = "{{agent_package}}"

# Extensions considered text (placeholder substitution applied)
TEXT_EXTENSIONS = frozenset({
    ".py", ".md", ".txt", ".yaml", ".yml", ".toml",
    ".cfg", ".ini", ".env", ".example", ".html", ".json",
})


# ── Validation ──────────────────────────────────────────────────────


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


# ── Feature-flag substitutions ──────────────────────────────────────


_DEFAULT_FRAME = (
    "You are a helpful AI assistant.\\n\\n"
    "Use your tools to take action. When something matters across sessions, "
    "save it to memory. Otherwise: act."
)

_PERSONA_FRAME = (
    "You connect the principal to their toolkits and act on their behalf. "
    "Your memory persists across sessions. Your skills grow over time.\\n\\n"
    "When something matters about the principal, the world, or yourself — `save_memory`. "
    "When your character genuinely shifts — `update_soul`. "
    "When you discover a reusable, durable pattern — `author_skill`. "
    "Otherwise: act."
)

_PERSONA_TOOL_IMPORTS = (
    "from .soul_tools import soul_tool_list\n"
    "from .skill_tools import skill_tool_list\n"
    "from .awakening_tools import awakening_tool_list\n"
)

_PERSONA_TOOL_EXTENDS = (
    "    tools.extend(soul_tool_list)\n"
    "    tools.extend(skill_tool_list)\n"
    "    tools.extend(awakening_tool_list)\n"
)

_PERSONA_AWAKENING_LOAD = "    awakening = _read(awakening_file())\n"
_PERSONA_AWAKENING_INJECT = (
    "    if awakening:\n"
    "        parts.append(awakening)\n"
)

_COMPOSIO_TOOL_IMPORTS = "from .composio_mcp import build_composio_mcp_toolset\n"
_COMPOSIO_TOOL_EXTENDS = (
    "    composio_toolset = build_composio_mcp_toolset()\n"
    "    if composio_toolset is not None:\n"
    "        tools.append(composio_toolset)\n"
)

_COMPOSIO_ENV_BLOCK = (
    "# ── Composio Tool Router (MCP) ───────────────────────────────\n"
    "# Required to enable the Tool Router toolset (~1000 toolkits via a\n"
    "# single hosted MCP endpoint). Without it, the agent starts with\n"
    "# only the local tools.\n"
    "COMPOSIO_API_KEY=your_composio_api_key_here\n"
    "\n"
    "# Optional: user identity scoped to the Composio session (default: \"default\")\n"
    "# COMPOSIO_USER_ID=default\n"
    "\n"
)

_COMPOSIO_REQUIREMENT = "composio>=1.0.0rc10\n"


# ── Placeholder replacement ─────────────────────────────────────────


def _build_replacements(
    name: str,
    package: str,
    description: str,
    system_prompt: str,
    persona: bool,
    with_composio: bool,
    with_slack: bool = False,
    with_telegram: bool = False,
    with_teams: bool = False,
) -> dict[str, str]:
    # Frame priority: user's --system-prompt wins, else persona-aware default.
    if system_prompt:
        frame = system_prompt
    elif persona:
        frame = _PERSONA_FRAME
    else:
        frame = _DEFAULT_FRAME

    gateway_imports = ""
    gateway_mounts = ""
    gateway_requirements = ""
    gateway_env_block = ""
    gateway_readme_section = ""

    # Channel-specific contributions are stitched in the next tasks (3, 4, 5).
    # For now: any channel flag set means at least the base overlay applies,
    # which contributes nothing to imports/mounts directly — that's per-channel.

    return {
        "{{agent_package}}": package,
        "{{agent_name}}": name,
        "{{agent_name_snake}}": package,
        "{{agent_description}}": description,
        "{{agent_system_prompt}}": system_prompt,
        # Frame
        "{{instruction_frame}}": frame,
        # tools/__init__.py.tmpl
        "{{persona_imports}}": _PERSONA_TOOL_IMPORTS if persona else "",
        "{{persona_extends}}": _PERSONA_TOOL_EXTENDS if persona else "",
        "{{composio_imports}}": _COMPOSIO_TOOL_IMPORTS if with_composio else "",
        "{{composio_extends}}": _COMPOSIO_TOOL_EXTENDS if with_composio else "",
        # prompt/instructions.py.tmpl
        "{{awakening_load}}": _PERSONA_AWAKENING_LOAD if persona else "",
        "{{awakening_inject}}": _PERSONA_AWAKENING_INJECT if persona else "",
        # .env.example
        "{{composio_env_block}}": _COMPOSIO_ENV_BLOCK if with_composio else "",
        # requirements.txt
        "{{composio_requirement}}": _COMPOSIO_REQUIREMENT if with_composio else "",
        # Gateway placeholders (populated by per-channel tasks 3–5)
        "{{gateway_imports}}": gateway_imports,
        "{{gateway_mounts}}": gateway_mounts,
        "{{gateway_requirements}}": gateway_requirements,
        "{{gateway_env_block}}": gateway_env_block,
        "{{gateway_readme_section}}": gateway_readme_section,
    }


def _substitute(text: str, replacements: dict[str, str]) -> str:
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


# ── Tree walking ────────────────────────────────────────────────────


def _stamp_tree(
    src_root: Path,
    target: Path,
    replacements: dict[str, str],
    files_created: list[str],
) -> None:
    """Walk a template tree and stamp it into target, with placeholder substitution.

    Files in src_root with the same relative path as files already in target
    will be **overwritten** — overlays use this to replace base files.
    """
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


# ── Scaffolding ─────────────────────────────────────────────────────


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
) -> dict:
    """Scaffold a new agent from the template skeleton.

    Args:
        name: Kebab-case agent name.
        output_dir: Parent directory for the new agent.
        description: Short agent description.
        system_prompt: Optional inline system prompt seed.
        persona: Activate the persona overlay (self-rewriting soul, awakening,
                 author_skill, etc.). Inappropriate for stateless task bots.
        with_composio: Activate the Composio Tool Router MCP overlay.
        with_slack: Activate the Slack messaging-gateway overlay.
                    Implies with_composio (Slack uses Composio Slackbot).
        with_telegram: Activate the Telegram messaging-gateway overlay.
        with_teams: Activate the MS Teams messaging-gateway overlay
                    (sidecar process; runs separately from the agent server).

    Returns:
        A dict with status and metadata.
    """
    try:
        name = validate_agent_name(name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    # --with-slack uses Composio Slackbot end-to-end; auto-enable composio.
    if with_slack and not with_composio:
        print("[nuvel] --with-slack uses Composio Slackbot — enabling --with-composio.")
        with_composio = True

    package = name.replace("-", "_")
    description = description or f"ADK agent: {name}"
    system_prompt = system_prompt or "You are a helpful AI assistant."

    if output_dir is None:
        output_dir = os.environ.get("AGENTS_OUTPUT_DIR", "./generated-agents")
    target = Path(output_dir) / name

    if target.exists():
        return {
            "status": "error",
            "message": f"Directory already exists: {target}",
        }

    replacements = _build_replacements(
        name, package, description, system_prompt, persona, with_composio,
        with_slack, with_telegram, with_teams,
    )
    files_created: list[str] = []

    try:
        # 1. Base template
        _stamp_tree(TEMPLATES_DIR, target, replacements, files_created)

        # 2. Overlays — order matters: later overlays override earlier ones
        if persona:
            _stamp_tree(OVERLAYS_DIR / "persona", target, replacements, files_created)
        if with_composio:
            _stamp_tree(OVERLAYS_DIR / "composio", target, replacements, files_created)
        if with_slack or with_telegram or with_teams:
            _stamp_tree(OVERLAYS_DIR / "gateway-base", target, replacements, files_created)
        # Per-channel overlays added in subsequent tasks.

        return {
            "status": "ok",
            "path": str(target),
            "agent_name": name,
            "package_name": package,
            "files_created": len(files_created),
            "files": files_created,
            "persona": persona,
            "with_composio": with_composio,
            "with_slack": with_slack,
            "with_telegram": with_telegram,
            "with_teams": with_teams,
        }

    except Exception as exc:
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
    parser.add_argument(
        "--persona", action="store_true",
        help="Activate the persona overlay: self-rewriting SOUL.md, AWAKENING.md, "
             "author_skill, complete_awakening. For agents meant to live and grow "
             "over time. Inappropriate for stateless task bots.",
    )
    parser.add_argument(
        "--with-composio", action="store_true",
        help="Wire the Composio Tool Router MCP (~1000 toolkits via one hosted endpoint).",
    )
    args = parser.parse_args()

    result = scaffold_agent(
        name=args.agent_name,
        output_dir=args.output_dir,
        description=args.description,
        persona=args.persona,
        with_composio=args.with_composio,
    )

    if result["status"] == "ok":
        print(f"Agent scaffolded at: {result['path']}")
        print(f"Files created: {result['files_created']}")
        flags = []
        if result.get("persona"):
            flags.append("persona")
        if result.get("with_composio"):
            flags.append("composio")
        if flags:
            print(f"Bundles: {', '.join(flags)}")
    else:
        print(f"Error: {result['message']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
