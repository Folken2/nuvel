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

from nuvel._defaults import DEFAULT_FAST_MODEL, DEFAULT_REASONING_MODEL

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

_TELEGRAM_ENV_BLOCK = (
    "# ── Telegram gateway ─────────────────────────────────────────────\n"
    "# Required: bot token from @BotFather\n"
    "TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here\n"
    "# Required: random string passed to Telegram when calling setWebhook;\n"
    "# Telegram echoes it back via the X-Telegram-Bot-Api-Secret-Token header.\n"
    "TELEGRAM_WEBHOOK_SECRET=change_me_to_a_long_random_string\n"
    "# Optional: bot username (without @) for group-mention detection.\n"
    "# TELEGRAM_BOT_USERNAME=your_bot_username\n"
    "\n"
)

_TELEGRAM_README_BLOCK = (
    "\n## Channel: Telegram\n"
    "\n"
    "1. Create a bot via @BotFather and copy the token into `TELEGRAM_BOT_TOKEN`.\n"
    "2. Set a long random string as `TELEGRAM_WEBHOOK_SECRET`.\n"
    "3. After deploying, register the webhook:\n"
    "\n"
    "   ```\n"
    "   curl -F \"url=https://<your-deployment>/gateways/telegram\" \\\n"
    "        -F \"secret_token=$TELEGRAM_WEBHOOK_SECRET\" \\\n"
    "        \"https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook\"\n"
    "   ```\n"
    "\n"
    "4. For local dev, run `ngrok http 8000` first and pass the ngrok URL above.\n"
    "5. The bot replies inline in DMs and in the same thread/topic in groups.\n"
    "\n"
    "### Multimodal (images and files)\n"
    "\n"
    "The gateway forwards user-uploaded images and files to the agent and surfaces agent-emitted artifacts back to the chat.\n"
    "\n"
    "**Supported envs:**\n"
    "\n"
    "| Env | Default | Purpose |\n"
    "|---|---|---|\n"
    "| `GATEWAY_MAX_ATTACHMENT_COUNT` | `5` | per inbound message |\n"
    "| `GATEWAY_MAX_ATTACHMENT_BYTES` | `10485760` (10 MiB) | per inbound file |\n"
    "| `GATEWAY_INLINE_DATA_MAX_BYTES` | `4194304` (4 MiB) | bytes ≤ this go inline; else the URI is forwarded |\n"
    "\n"
    "Telegram has its own 50 MB Bot API limit on inbound files; the gateway honors the smaller of that and `GATEWAY_MAX_ATTACHMENT_BYTES`.\n"
    "\n"
    "**Outbound:** the agent can send images and files back via either of:\n"
    "- emitting a `Part(inline_data=…)` or `Part(file_data=…)` in its event content;\n"
    "- saving an artifact via `tool_context.save_artifact(...)` (read from `actions.artifact_delta`).\n"
    "\n"
    "**Limitations (this release):**\n"
    "- Animated stickers (TGS format) are not parsed.\n"
)

_SLACK_ENV_BLOCK = (
    "# ── Slack gateway (via Composio Slackbot) ────────────────────────\n"
    "# Required: random shared secret used by Composio when delivering\n"
    "# trigger webhooks. Set the same value when running\n"
    "# `composio trigger create ... --webhook ...?secret=<this>`.\n"
    "COMPOSIO_WEBHOOK_SECRET=change_me_to_a_long_random_string\n"
    "# Optional: bot user ID for @-mention detection in channels.\n"
    "# SLACK_BOT_USER_ID=U0BOT...\n"
    "# Optional: 'all' to invoke on every channel message; default 'mention'.\n"
    "# SLACK_CHANNEL_TRIGGER_MODE=mention\n"
    "\n"
)

_SLACK_README_BLOCK = (
    "\n## Channel: Slack\n"
    "\n"
    "This gateway uses [Composio's Slackbot toolkit](https://docs.composio.dev/) for\n"
    "both inbound (webhook triggers) and outbound (`SLACKBOT_SEND_MESSAGE`).\n"
    "\n"
    "1. In the Composio dashboard, connect Slack to your workspace.\n"
    "2. Set `COMPOSIO_WEBHOOK_SECRET` in `.env` to a long random string.\n"
    "3. After deploying, register the triggers you want. Minimum:\n"
    "\n"
    "   ```\n"
    "   composio trigger create SLACKBOT_DIRECT_MESSAGE_RECEIVED \\\n"
    "       --webhook \"https://<your-deployment>/gateways/slack/composio?secret=$COMPOSIO_WEBHOOK_SECRET\"\n"
    "   composio trigger create SLACKBOT_CHANNEL_MESSAGE_RECEIVED \\\n"
    "       --webhook \"https://<your-deployment>/gateways/slack/composio?secret=$COMPOSIO_WEBHOOK_SECRET\"\n"
    "   ```\n"
    "\n"
    "4. (Optional) Set `SLACK_BOT_USER_ID` so channel-mentions only trigger replies\n"
    "   when the bot is explicitly @-mentioned (default behavior).\n"
    "\n"
    "### Multimodal (images and files)\n"
    "\n"
    "The gateway forwards user-uploaded images and files to the agent and surfaces agent-emitted artifacts back to the chat.\n"
    "\n"
    "**Supported envs:**\n"
    "\n"
    "| Env | Default | Purpose |\n"
    "|---|---|---|\n"
    "| `GATEWAY_MAX_ATTACHMENT_COUNT` | `5` | per inbound message |\n"
    "| `GATEWAY_MAX_ATTACHMENT_BYTES` | `10485760` (10 MiB) | per inbound file |\n"
    "| `GATEWAY_INLINE_DATA_MAX_BYTES` | `4194304` (4 MiB) | bytes ≤ this go inline; else the URI is forwarded |\n"
    "| `SLACK_BOT_TOKEN` | unset | **Slack only — required to download user-uploaded files**; without it, only the URL is forwarded |\n"
    "\n"
    "**Outbound:** the agent can send images and files back via either of:\n"
    "- emitting a `Part(inline_data=…)` or `Part(file_data=…)` in its event content;\n"
    "- saving an artifact via `tool_context.save_artifact(...)` (read from `actions.artifact_delta`).\n"
    "\n"
    "**Limitations (this release):**\n"
    "- `SLACK_BOT_TOKEN` is required for inbound bytes; without it the gateway receives only the file URL.\n"
)

_TEAMS_ENV_BLOCK = (
    "# ── Teams gateway (sidecar — runs separately) ───────────────────\n"
    "# The Teams bridge is a separate process. Run it with:\n"
    "#   python -m <agent_package>.gateways.teams_bridge\n"
    "#\n"
    "# SDK mode (production) — Azure Bot Service + Teams:\n"
    "# CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=...\n"
    "# CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=...\n"
    "# CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID=...\n"
    "#\n"
    "# Anonymous mode (Agents Playground / local dev): leave the three above\n"
    "# unset; the bridge accepts unauthenticated POSTs to /api/messages.\n"
    "#\n"
    "# Bridge → agent connection (defaults usually fine):\n"
    "# AGENT_BASE_URL=http://127.0.0.1:8000\n"
    "# AGENT_APP_NAME=<scaffolded agent name>\n"
    "# AGENT_TIMEOUT_SECONDS=120\n"
    "#\n"
    "# Bridge runtime:\n"
    "TEAMS_BRIDGE_PORT=3978\n"
    "# TEAMS_BRIDGE_HOST=localhost\n"
    "# TEAMS_ENABLE_INTERMEDIATE_MESSAGES=true\n"
    "# TEAMS_PROGRESS_TEXTS=Analyzing request...|Inspecting available data...|Running tools...|Preparing final response...\n"
    "# TEAMS_PROGRESS_MIN_DELAY_MS=350\n"
    "\n"
)

_TEAMS_README_BLOCK = (
    "\n## Channel: Microsoft Teams (sidecar)\n"
    "\n"
    "Teams is implemented as a separate process that proxies to this agent's\n"
    "REST API. It uses the Microsoft 365 Agents SDK, which is aiohttp-based\n"
    "and runs alongside (not inside) the FastAPI agent server.\n"
    "\n"
    "**Run command:**\n"
    "\n"
    "```\n"
    "python -m {{agent_package}}.gateways.teams_bridge\n"
    "```\n"
    "\n"
    "**Setup:**\n"
    "\n"
    "1. (Production) Register the bot in Azure Bot Service / Teams Developer Portal\n"
    "   and set `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__{CLIENTID,CLIENTSECRET,TENANTID}`.\n"
    "2. (Local dev) Skip step 1 and run the bridge against the\n"
    "   [Microsoft 365 Agents Playground](https://aka.ms/agents-playground).\n"
    "3. Point the bot's messaging endpoint at `https://<bridge-host>:3978/api/messages`.\n"
    "\n"
    "The bridge handles JWT validation in production mode and falls back to an\n"
    "anonymous-POST mode for the Agents Playground when SDK config is absent.\n"
    "\n"
    "### Multimodal (images and files)\n"
    "\n"
    "The gateway forwards user-uploaded images and files to the agent and surfaces agent-emitted artifacts back to the chat.\n"
    "\n"
    "**Supported envs:**\n"
    "\n"
    "| Env | Default | Purpose |\n"
    "|---|---|---|\n"
    "| `GATEWAY_MAX_ATTACHMENT_COUNT` | `5` | per inbound message |\n"
    "| `GATEWAY_MAX_ATTACHMENT_BYTES` | `10485760` (10 MiB) | per inbound file |\n"
    "| `GATEWAY_INLINE_DATA_MAX_BYTES` | `4194304` (4 MiB) | bytes ≤ this go inline; else the URI is forwarded |\n"
    "\n"
    "**Outbound:** the agent can send images and files back via either of:\n"
    "- emitting a `Part(inline_data=…)` or `Part(file_data=…)` in its event content;\n"
    "- saving an artifact via `tool_context.save_artifact(...)` (read from `actions.artifact_delta`).\n"
    "\n"
    "**Limitations (this release):**\n"
    "- Outbound `actions.artifact_delta` is not yet read by the Teams sidecar; only inline parts (`Part(inline_data=…)` / `Part(file_data=…)`) are surfaced. Saved-artifact outputs work on Slack and Telegram but not Teams in this release.\n"
)


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

    gateway_imports_lines: list[str] = []
    gateway_mounts_lines: list[str] = []
    gateway_requirements_lines: list[str] = []
    gateway_env_blocks: list[str] = []
    gateway_readme_blocks: list[str] = []

    if with_telegram:
        gateway_imports_lines.append(f"from {package}.gateways import telegram as gw_telegram")
        gateway_mounts_lines.append("    app.include_router(gw_telegram.router)")
        gateway_env_blocks.append(_TELEGRAM_ENV_BLOCK)
        gateway_readme_blocks.append(_TELEGRAM_README_BLOCK)
        gateway_requirements_lines.append("httpx>=0.27.0")

    if with_slack:
        gateway_imports_lines.append(f"from {package}.gateways import slack as gw_slack")
        gateway_mounts_lines.append("    app.include_router(gw_slack.router)")
        gateway_env_blocks.append(_SLACK_ENV_BLOCK)
        gateway_readme_blocks.append(_SLACK_README_BLOCK)

    if with_teams:
        # Teams runs as a sidecar; nothing to import or mount in run_adk.py.
        gateway_requirements_lines.extend([
            "microsoft-agents-hosting-aiohttp",
            "microsoft-agents-authentication-msal",
            "aiohttp",
            "pypdf",
        ])
        gateway_env_blocks.append(_TEAMS_ENV_BLOCK)
        # _TEAMS_README_BLOCK contains "{{agent_package}}" placeholder; substitute
        # at construction time since the block goes into the replacements dict value
        # (not a template file), so _stamp_tree won't process it again.
        gateway_readme_blocks.append(_TEAMS_README_BLOCK.replace("{{agent_package}}", package))

    # State-injection block: prepend to gateway_mounts_lines when any channel is active.
    # All gateway routers depend on app.state.runner, app.state.app_name.
    # Slack also requires app.state.composio_client.
    if with_slack or with_telegram or with_teams:
        state_lines = [
            f'    app.state.app_name = "{name}"',
            f"    from {package}.agent import root_agent as _root",
            f"    from {package}.harness import AgentHarness",
            "    # AgentHarness is the one place session/artifact services and",
            "    # plugins are built; the gateway runner shares it with the",
            "    # cron fallback runner (see below) since it's a singleton.",
            "    _harness = AgentHarness.get(app.state.app_name)",
            "    app.state.runner = _harness.build_runner(agent=_root)",
        ]
        if with_slack:
            state_lines.append(
                f"    from {package}.gateways._common import get_composio_client"
            )
            state_lines.append("    app.state.composio_client = get_composio_client()")
        gateway_mounts_lines = state_lines + gateway_mounts_lines

    gateway_imports = ("\n".join(gateway_imports_lines) + "\n") if gateway_imports_lines else ""
    gateway_mounts = ("\n".join(gateway_mounts_lines) + "\n") if gateway_mounts_lines else ""
    gateway_requirements = ("\n".join(gateway_requirements_lines) + "\n") if gateway_requirements_lines else ""
    gateway_env_block = "\n".join(gateway_env_blocks)
    gateway_readme_section = "\n".join(gateway_readme_blocks)

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
        # Model defaults (single source: nuvel/_defaults.py)
        "{{default_fast_model}}": DEFAULT_FAST_MODEL,
        "{{default_reasoning_model}}": DEFAULT_REASONING_MODEL,
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
        if with_telegram:
            _stamp_tree(OVERLAYS_DIR / "gateway-telegram", target, replacements, files_created)
        if with_slack:
            _stamp_tree(OVERLAYS_DIR / "gateway-slack", target, replacements, files_created)
        if with_teams:
            _stamp_tree(OVERLAYS_DIR / "gateway-teams", target, replacements, files_created)

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
