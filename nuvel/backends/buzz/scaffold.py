"""
scaffold.py — The Buzz Stamping Script

Copies the template skeleton from nuvel/backends/buzz/templates/ into a target
directory, renames the {{agent_package}} directory, and substitutes
placeholders — the same Copy-Boilerplate-Adapt shape as the ADK backend.

A Buzz agent is ACP-native: the stdio JSON-RPC adapter and the terminal CLI
live in the base template rather than behind a flag, because they *are* the
runtime (there is no FastAPI server to be the "real" entrypoint). The ADK
`--with-acp` overlay is therefore always-on here, and passing the flag is a
no-op rather than an error.

One overlay is applied from nuvel/backends/buzz/templates_overlays/<bundle>/:

    buzz    Nostr identity (secp256k1 keygen, BIP-340 signing, bech32),
            the NIP-29 relay worker, and the skills-derived persona.
            On by default — it is what makes the agent a *Buzz* agent.

Everything else (`--persona`, `--with-composio`, the messaging gateways,
`--workflow`, `--with-eval`) is ADK-only and rejected rather than silently
ignored.

Usage:
    python -m nuvel.backends.buzz.scaffold <agent-name> [--output-dir DIR]
                                           [--description DESC] [--no-buzz]
                                           [--relay-url URL]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

from nuvel._defaults import DEFAULT_FAST_MODEL

TEMPLATES_DIR = Path(__file__).parent / "templates"
OVERLAYS_DIR = Path(__file__).parent / "templates_overlays"

PLACEHOLDER_TAG = "{{agent_package}}"

# Provider prefixes carried by the nuvel-wide model ids for LiteLLM routing.
# The Buzz agent talks to the raw HTTP API, which doesn't want them — mirrors
# PROVIDERS in templates/{{agent_package}}/agent.py.
PROVIDER_PREFIXES = frozenset({
    "openrouter", "openai", "anthropic", "groq", "together", "ollama",
})

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


# No memory tools in a Buzz agent — the frame points at the skills instead.
_DEFAULT_FRAME = (
    "You are a helpful AI assistant.\n"
    "\n"
    "Your skills are focused guides for specific kinds of work: call "
    "list_skills when a task looks unfamiliar, and read_skill before "
    "following one. Otherwise: act."
)


def _bare_model_id(model: str) -> str:
    """``openrouter/moonshotai/kimi-k2.5`` → ``moonshotai/kimi-k2.5``.

    The generated agent strips a prefix that names its configured provider at
    runtime too, but the value stamped into ``.env.example`` is documented as
    the bare id ("openrouter/<id>" and "<id>" both work), so strip it here.
    """
    prefix, sep, rest = model.partition("/")
    return rest if sep and prefix in PROVIDER_PREFIXES else model


def _as_python_literal_body(text: str) -> str:
    """Make `text` safe to drop inside a triple-quoted string in generated code.

    `{{instruction_frame}}` lands in `DEFAULT_INSTRUCTION = \"\"\"…\"\"\"` in
    agent.py, so a user-supplied prompt containing a backslash or a triple
    quote would otherwise produce a file that doesn't parse.
    """
    return text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')


def _build_replacements(
    name: str,
    package: str,
    description: str,
    system_prompt: str,
    buzz_relay_url: str = "",
    nostr_private_key: str = "",
) -> dict[str, str]:
    frame = system_prompt or _DEFAULT_FRAME

    return {
        "{{agent_package}}": package,
        "{{agent_name}}": name,
        "{{agent_name_snake}}": package,
        "{{agent_description}}": description,
        "{{agent_system_prompt}}": system_prompt,
        # agent.py — goes inside a triple-quoted string.
        "{{instruction_frame}}": _as_python_literal_body(frame),
        # .env.example — the bare model id; the provider prefix is optional.
        "{{default_model}}": _bare_model_id(DEFAULT_FAST_MODEL),
        # .env.example — both blank by default: the relay is chosen at deploy
        # time, and an unset key makes the agent generate (and persist) one on
        # first run rather than shipping a secret in a scaffolded file.
        "{{buzz_relay_url}}": buzz_relay_url,
        "{{nostr_private_key}}": nostr_private_key,
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
    with_buzz: bool = True,
    buzz_relay_url: str = "",
    nostr_private_key: str = "",
) -> dict:
    """Scaffold a Buzz agent from the template skeleton.

    Args:
        name: Kebab-case agent name.
        output_dir: Parent directory for the new agent.
        description: Short agent description.
        system_prompt: Optional inline system prompt seed. Becomes
                       `DEFAULT_INSTRUCTION` in the generated agent.py, which
                       `BUZZ_AGENT_INSTRUCTION` can override at runtime.
        with_acp: Accepted and ignored — a Buzz agent is ACP-native, so the
                  stdio adapter and terminal CLI are always stamped.
        with_buzz: Apply the buzz overlay (Nostr identity, NIP-29 relay worker,
                   skills-derived persona). On by default; turning it off
                   leaves a plain ACP/CLI agent with no relay entrypoint.
        buzz_relay_url: Seed for `BUZZ_RELAY_URL` in .env.example. Blank by
                        default — the relay is picked at deploy time.
        nostr_private_key: Seed for `NOSTR_PRIVATE_KEY` in .env.example. Blank
                           by default, which makes the agent generate and
                           persist its own key on first run.

    The persona / composio / gateway / workflow / eval bundles are ADK-only;
    passing them here returns an error rather than silently ignoring.

    Returns:
        A dict with status and metadata.
    """
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
    if with_eval:
        return {
            "status": "error",
            "message": "--with-eval is not yet supported for the buzz backend. Use --framework adk.",
        }
    for flag_name, flag_set in (
        ("with-slack", with_slack),
        ("with-telegram", with_telegram),
        ("with-teams", with_teams),
    ):
        if flag_set:
            return {
                "status": "error",
                "message": (
                    f"--{flag_name} is not supported for the buzz backend — a Buzz agent "
                    "talks over a Nostr relay, not an HTTP gateway. Use --framework adk."
                ),
            }

    try:
        name = validate_agent_name(name)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    # ACP isn't a bundle here — it's the runtime. Say so instead of erroring.
    if with_acp:
        print("[nuvel] buzz agents are ACP-native — --with-acp is already included.")

    package = name.replace("-", "_")
    description = description or f"Buzz agent: {name}"

    if output_dir is None:
        output_dir = os.environ.get("AGENTS_OUTPUT_DIR", "./generated-agents")
    target = Path(output_dir) / name

    if target.exists():
        return {
            "status": "error",
            "message": f"Directory already exists: {target}",
        }

    replacements = _build_replacements(
        name, package, description, system_prompt,
        buzz_relay_url, nostr_private_key,
    )
    files_created: list[str] = []

    try:
        # 1. Base template — package, ACP adapter, terminal CLI, skills.
        _stamp_tree(TEMPLATES_DIR, target, replacements, files_created)

        # 2. Overlay — Nostr identity, relay worker, persona.
        if with_buzz:
            _stamp_tree(OVERLAYS_DIR / "buzz", target, replacements, files_created)

        return {
            "status": "ok",
            "path": str(target),
            "agent_name": name,
            "package_name": package,
            "files_created": len(files_created),
            "files": files_created,
            "persona": False,
            "with_composio": False,
            "with_acp": True,
            "with_buzz": with_buzz,
        }

    except Exception as exc:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        return {"status": "error", "message": str(exc)}


# ── CLI ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scaffold a new Buzz agent from the template skeleton."
    )
    parser.add_argument("agent_name", help="Kebab-case agent name (e.g. my-agent)")
    parser.add_argument("--output-dir", default=None, help="Parent directory for the new agent")
    parser.add_argument("--description", default="", help="Short agent description")
    parser.add_argument("--system-prompt", default="", help="System prompt for the new agent")
    parser.add_argument(
        "--no-buzz", action="store_true",
        help="Skip the buzz overlay (Nostr identity, relay worker, persona), "
             "leaving a plain ACP/CLI agent.",
    )
    parser.add_argument(
        "--relay-url", default="",
        help="Seed BUZZ_RELAY_URL in .env.example (default: blank).",
    )
    args = parser.parse_args()

    result = scaffold_agent(
        name=args.agent_name,
        output_dir=args.output_dir,
        description=args.description,
        system_prompt=args.system_prompt,
        with_buzz=not args.no_buzz,
        buzz_relay_url=args.relay_url,
    )

    if result["status"] == "ok":
        print(f"Agent scaffolded at: {result['path']}")
        print(f"Files created: {result['files_created']}")
        if result.get("with_buzz"):
            print("Bundles: buzz")
    else:
        print(f"Error: {result['message']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
