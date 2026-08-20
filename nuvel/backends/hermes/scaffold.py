"""
scaffold.py — The Hermes Stamping Script

Copies the template skeleton from nuvel/backends/hermes/templates/ into a
target directory, renames the {{agent_package}} directory, and substitutes
placeholders — the same Copy-Boilerplate-Adapt shape as the ADK and Buzz
backends.

What lands is a **Hermes profile**, not a Python program: SOUL.md (identity),
config.yaml (model, turn budget, platforms, skills), .env.example (secrets),
and a skills/ directory. Hermes owns the runtime, so there is no server, no
model loop, and no requirements.txt to generate. Drop the stamped package
directory into ``<hermes_home>/profiles/<name>/`` and Hermes picks it up —
the same layout :mod:`nuvel.bots` drives through the ``hermes`` CLI.

One overlay is applied from nuvel/backends/hermes/templates_overlays/<bundle>/:

    hermes-gateway  --with-telegram. Replaces config.yaml with one that has
                    platforms.telegram enabled and its bot_token / dm_policy /
                    allowed_users / mention_only knobs spelled out.

Everything else (`--persona`, `--with-composio`, `--with-slack`,
`--with-teams`, `--workflow`, `--with-eval`, `--with-acp`) is ADK-only or
meaningless for Hermes, and is rejected rather than silently ignored.

Usage:
    python -m nuvel.backends.hermes.scaffold <agent-name> [--output-dir DIR]
                                             [--description DESC]
                                             [--with-telegram] [--model ID]
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

# Hermes resolves models through its own `model.provider`, so config.yaml wants
# a bare id — not the `openrouter/…`-prefixed ids nuvel._defaults carries for
# LiteLLM routing. Kept as its own constant rather than derived from
# DEFAULT_FAST_MODEL: the two are free to diverge, and `--model` overrides it.
DEFAULT_HERMES_MODEL = "deepseek/deepseek-v4-flash"

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


# ── Placeholder replacement ─────────────────────────────────────────


# Lands under "# Purpose" in SOUL.md when no system prompt is supplied. Points
# at the skills rather than listing capabilities — a hand-maintained
# capability list drifts within a week, the skills directory doesn't.
_DEFAULT_PURPOSE = (
    "I help with whatever the person in front of me is actually trying to do.\n"
    "\n"
    "My skills are focused guides for specific kinds of work — I check them "
    "when a task looks unfamiliar and read one before following it. "
    "Otherwise: act."
)


def _build_replacements(
    name: str,
    package: str,
    description: str,
    system_prompt: str,
    model: str,
    telegram_bot_token: str = "",
) -> dict[str, str]:
    return {
        "{{agent_package}}": package,
        "{{agent_name}}": name,
        "{{agent_name_snake}}": package,
        "{{agent_description}}": description,
        # `{{description}}` is an accepted alias so a template written with the
        # shorter spelling stamps correctly too.
        "{{description}}": description,
        "{{agent_system_prompt}}": system_prompt,
        # SOUL.md — prose, so an empty prompt falls back rather than leaving
        # the "# Purpose" section blank.
        "{{agent_purpose}}": system_prompt or _DEFAULT_PURPOSE,
        # config.yaml — bare model id; Hermes' `model.provider` picks the endpoint.
        "{{default_model}}": model,
        # .env.example — blank by default: a bot token is a credential, and
        # scaffolding one into a committed file is how they leak.
        "{{telegram_bot_token}}": telegram_bot_token,
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
    workflow: bool = False,
    with_acp: bool = False,
    with_eval: bool = False,
    model: str = DEFAULT_HERMES_MODEL,
    telegram_bot_token: str = "",
) -> dict:
    """Scaffold a Hermes profile from the template skeleton.

    Args:
        name: Kebab-case agent name.
        output_dir: Parent directory for the new agent.
        description: Short agent description. Lands in SOUL.md's identity line.
        system_prompt: Optional inline system prompt seed. Becomes the
                       "# Purpose" section of SOUL.md; omit it and a default
                       pointing at the skills directory is used instead.
        with_telegram: Apply the hermes-gateway overlay — a config.yaml with
                       platforms.telegram enabled plus its dm_policy /
                       allowed_users / mention_only knobs.
        model: Value for `model.default` in config.yaml. Bare id, no provider
               prefix (Hermes resolves the endpoint from `model.provider`).
        telegram_bot_token: Seed for `TELEGRAM_BOT_TOKEN` in .env.example.
                            Blank by default — the token is a credential.

    The persona / composio / slack / teams / workflow / eval / acp bundles are
    ADK-only; passing them here returns an error rather than silently ignoring.

    Returns:
        A dict with status and metadata.
    """
    if persona:
        return {
            "status": "error",
            "message": (
                "--persona is an ADK-only bundle; a Hermes profile already has a "
                "SOUL.md. Use --framework adk if you want the self-rewriting version."
            ),
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
    if with_eval:
        return {
            "status": "error",
            "message": "--with-eval is not yet supported for the hermes backend. Use --framework adk.",
        }
    if with_acp:
        return {
            "status": "error",
            "message": (
                "--with-acp is not supported for the hermes backend — Hermes owns the "
                "runtime, so there is no process for nuvel to hand an ACP adapter to. "
                "Use --framework buzz for an ACP-native agent, or --framework adk."
            ),
        }
    for flag_name, flag_set in (
        ("with-slack", with_slack),
        ("with-teams", with_teams),
    ):
        if flag_set:
            return {
                "status": "error",
                "message": (
                    f"--{flag_name} is not supported for the hermes backend — Telegram "
                    "(--with-telegram) is the platform this backend wires. "
                    "Use --framework adk for the other gateways."
                ),
            }

    try:
        name = validate_agent_name(name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    package = name.replace("-", "_")
    description = description or f"Hermes agent: {name}"

    if output_dir is None:
        output_dir = os.environ.get("AGENTS_OUTPUT_DIR", "./generated-agents")
    target = Path(output_dir) / name

    if target.exists():
        return {
            "status": "error",
            "message": f"Directory already exists: {target}",
        }

    replacements = _build_replacements(
        name, package, description, system_prompt, model, telegram_bot_token,
    )
    files_created: list[str] = []

    try:
        # 1. Base profile — SOUL.md, config.yaml, .env.example, skills/.
        _stamp_tree(TEMPLATES_DIR, target, replacements, files_created)

        # 2. Overlay — replaces config.yaml with the Telegram-enabled one.
        if with_telegram:
            _stamp_tree(
                OVERLAYS_DIR / "hermes-gateway", target, replacements, files_created
            )

        return {
            "status": "ok",
            "path": str(target),
            "agent_name": name,
            "package_name": package,
            "files_created": len(files_created),
            "files": files_created,
            "persona": False,
            "with_composio": False,
            "with_acp": False,
            "with_telegram": with_telegram,
            "model": model,
        }

    except Exception as exc:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        return {"status": "error", "message": str(exc)}


# ── CLI ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scaffold a new Hermes profile from the template skeleton."
    )
    parser.add_argument("agent_name", help="Kebab-case agent name (e.g. my-agent)")
    parser.add_argument("--output-dir", default=None, help="Parent directory for the new agent")
    parser.add_argument("--description", default="", help="Short agent description")
    parser.add_argument("--system-prompt", default="", help="System prompt for the new agent")
    parser.add_argument(
        "--with-telegram", action="store_true",
        help="Apply the hermes-gateway overlay: config.yaml with "
             "platforms.telegram enabled (bot_token, dm_policy, allowed_users).",
    )
    parser.add_argument(
        "--model", default=DEFAULT_HERMES_MODEL,
        help=f"Value for model.default in config.yaml (default: {DEFAULT_HERMES_MODEL}).",
    )
    args = parser.parse_args()

    result = scaffold_agent(
        name=args.agent_name,
        output_dir=args.output_dir,
        description=args.description,
        system_prompt=args.system_prompt,
        with_telegram=args.with_telegram,
        model=args.model,
    )

    if result["status"] == "ok":
        print(f"Agent scaffolded at: {result['path']}")
        print(f"Files created: {result['files_created']}")
        if result.get("with_telegram"):
            print("Channels: telegram")
    else:
        print(f"Error: {result['message']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
