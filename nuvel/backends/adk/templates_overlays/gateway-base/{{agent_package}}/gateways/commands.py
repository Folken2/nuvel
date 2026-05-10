"""Unified slash-command registry shared by every messaging gateway.

Inspired by Hermes Agent's /command surface. A single registry lets the CLI,
Slack, Telegram, and Teams expose the same control verbs (`/new`, `/help`,
`/usage`, `/stop`) without each gateway re-implementing them.

Gateways call :func:`try_dispatch` *before* forwarding to the agent. If the
text starts with a registered command token the registry handles it and
returns ``handled=True``; otherwise the gateway forwards the text to the
agent normally.

Cancellation (``/stop``) is cooperative. The registry exposes a
:func:`get_cancel_event` keyed by ``session_id``; long-running gateway
operations may poll the event between agent steps to stop early. This is
a hook — gateways are not required to wire it up immediately.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# --- Personalities (lightweight runtime overlays) ---------------------------
#
# Cousin of the heavier ``--persona`` build-time SOUL.md system. A personality
# is a plain markdown file at ``~/.nuvel/personalities/<name>.md`` whose body
# is prepended to the user message as a system-style preamble. Active
# personality is tracked per ``session_id`` in-process — no DB.

PERSONALITIES_DIR = Path(os.path.expanduser("~/.nuvel/personalities"))

# session_id -> personality name (lowercase, no extension)
_ACTIVE_PERSONALITIES: dict[str, str] = {}


def _personality_path(name: str) -> Path:
    return PERSONALITIES_DIR / f"{name}.md"


def _list_personality_names() -> list[str]:
    if not PERSONALITIES_DIR.is_dir():
        return []
    return sorted(p.stem for p in PERSONALITIES_DIR.glob("*.md") if p.is_file())


def _read_personality(name: str) -> tuple[str, dict[str, str]]:
    """Return (body, frontmatter) for ``name``. Raises FileNotFoundError if missing."""
    path = _personality_path(name)
    raw = path.read_text(encoding="utf-8")
    fm: dict[str, str] = {}
    body = raw
    if raw.startswith("---\n"):
        end = raw.find("\n---", 4)
        if end != -1:
            front = raw[4:end]
            body = raw[end + 4 :].lstrip("\n")
            try:
                import yaml  # type: ignore
                parsed = yaml.safe_load(front) or {}
                if isinstance(parsed, dict):
                    fm = {str(k): str(v) for k, v in parsed.items()}
            except Exception:
                logger.warning("personalities: malformed YAML frontmatter in %s", path)
    return body.strip(), fm


def get_active_personality(session_id: str) -> str | None:
    """Return the active personality body for ``session_id`` or None."""
    name = _ACTIVE_PERSONALITIES.get(session_id)
    if not name:
        return None
    try:
        body, _ = _read_personality(name)
    except FileNotFoundError:
        # File deleted out from under us — clear and ignore.
        _ACTIVE_PERSONALITIES.pop(session_id, None)
        return None
    except Exception:
        logger.exception("personalities: failed to read %s", name)
        return None
    return body or None


def _set_active_personality(session_id: str, name: str) -> None:
    _ACTIVE_PERSONALITIES[session_id] = name


def _clear_active_personality(session_id: str) -> None:
    _ACTIVE_PERSONALITIES.pop(session_id, None)


# --- Public dataclasses ------------------------------------------------------


@dataclass
class CommandContext:
    """Carrier for everything a command handler may need.

    `runner` and `app_name` are optional to keep the registry usable from
    the CLI (which has neither). `reply` is an async callable so handlers
    can stream multiple lines if they want; the gateway provides it.
    """
    user_id: str
    channel: str
    session_id: str
    text: str
    runner: Any = None
    app_name: str = ""
    reply: Callable[[str], Awaitable[None]] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandResult:
    """Outcome of :func:`try_dispatch`.

    - ``handled=False`` means the text was not a slash command (or matched
      no registration) — the gateway should forward it to the agent.
    - ``handled=True`` means the registry consumed the text. ``replies``
      contains any text the gateway should still echo to the user (the
      handler may also have already used ``ctx.reply`` directly).
    """
    handled: bool = False
    replies: list[str] = field(default_factory=list)


# --- Registry ---------------------------------------------------------------


@dataclass
class _Registration:
    name: str
    aliases: tuple[str, ...]
    help: str
    handler: Callable[[CommandContext], Awaitable[CommandResult]]


_REGISTRY: dict[str, _Registration] = {}
_CANCEL_EVENTS: dict[str, asyncio.Event] = {}


def _normalize(token: str) -> str:
    token = token.strip()
    if not token.startswith("/"):
        token = "/" + token
    return token.lower()


def command(
    name: str,
    *aliases: str,
    help: str = "",
) -> Callable[[Callable[[CommandContext], Awaitable[CommandResult]]],
              Callable[[CommandContext], Awaitable[CommandResult]]]:
    """Decorator: register a handler under `name` and any `aliases`."""
    canon = _normalize(name)
    canon_aliases = tuple(_normalize(a) for a in aliases)

    def deco(fn):
        reg = _Registration(name=canon, aliases=canon_aliases, help=help, handler=fn)
        _REGISTRY[canon] = reg
        for a in canon_aliases:
            _REGISTRY[a] = reg
        return fn

    return deco


def is_command(text: str) -> bool:
    """Return True iff `text` looks like a slash command we know."""
    if not text:
        return False
    head = text.strip().split(maxsplit=1)
    if not head:
        return False
    first = head[0]
    if not first.startswith("/"):
        return False
    return _normalize(first) in _REGISTRY


def list_commands() -> list[_Registration]:
    """Return canonical registrations (no alias duplicates), sorted by name."""
    seen: set[str] = set()
    out: list[_Registration] = []
    for reg in _REGISTRY.values():
        if reg.name in seen:
            continue
        seen.add(reg.name)
        out.append(reg)
    out.sort(key=lambda r: r.name)
    return out


def get_cancel_event(session_id: str) -> asyncio.Event:
    """Return (creating if needed) the cancel Event for `session_id`."""
    ev = _CANCEL_EVENTS.get(session_id)
    if ev is None:
        ev = asyncio.Event()
        _CANCEL_EVENTS[session_id] = ev
    return ev


def clear_cancel_event(session_id: str) -> None:
    _CANCEL_EVENTS.pop(session_id, None)


async def try_dispatch(text: str, ctx: CommandContext) -> CommandResult:
    """If `text` is a registered slash command, run it. Otherwise no-op.

    The handler may push replies via ``ctx.reply`` *and/or* return them in
    :class:`CommandResult.replies`. Gateways should send any returned
    replies after dispatch.
    """
    if not text:
        return CommandResult(handled=False)
    stripped = text.strip()
    if not stripped.startswith("/"):
        return CommandResult(handled=False)
    head, _, rest = stripped.partition(" ")
    key = _normalize(head)
    reg = _REGISTRY.get(key)
    if reg is None:
        return CommandResult(handled=False)
    # Pass the command's argument tail through the context for handlers.
    ctx.text = rest.strip()
    try:
        return await reg.handler(ctx)
    except Exception:
        logger.exception("commands: handler %s failed", reg.name)
        return CommandResult(handled=True, replies=["Sorry — that command failed."])


# --- Built-in commands ------------------------------------------------------


@command("/new", "/reset", help="Start a fresh session (clears conversation memory)")
async def _cmd_new(ctx: CommandContext) -> CommandResult:
    if ctx.runner is None or not ctx.app_name:
        return CommandResult(handled=True, replies=["Session reset is unavailable here."])

    svc = ctx.runner.session_service
    try:
        existing = await svc.get_session(
            app_name=ctx.app_name, user_id=ctx.user_id, session_id=ctx.session_id,
        )
        if existing is not None:
            await svc.delete_session(
                app_name=ctx.app_name, user_id=ctx.user_id, session_id=ctx.session_id,
            )
        await svc.create_session(
            app_name=ctx.app_name, user_id=ctx.user_id, session_id=ctx.session_id, state={},
        )
    except Exception:
        logger.exception("commands: /new failed")
        return CommandResult(handled=True, replies=["Could not reset the session."])

    clear_cancel_event(ctx.session_id)
    return CommandResult(handled=True, replies=["Started a fresh session."])


@command("/help", help="List available commands")
async def _cmd_help(ctx: CommandContext) -> CommandResult:
    lines = ["Available commands:"]
    for reg in list_commands():
        aliases = f" ({', '.join(reg.aliases)})" if reg.aliases else ""
        lines.append(f"  {reg.name}{aliases} — {reg.help}")
    return CommandResult(handled=True, replies=["\n".join(lines)])


@command("/usage", help="Show this session's turn count and a token-cost estimate")
async def _cmd_usage(ctx: CommandContext) -> CommandResult:
    turns: int | None = None
    if ctx.runner is not None and ctx.app_name:
        try:
            sess = await ctx.runner.session_service.get_session(
                app_name=ctx.app_name, user_id=ctx.user_id, session_id=ctx.session_id,
            )
            events = getattr(sess, "events", None) or []
            turns = sum(1 for e in events if getattr(e, "author", None) == "user")
        except Exception:
            logger.exception("commands: /usage session lookup failed")
    if turns is None:
        return CommandResult(handled=True, replies=["Usage stats unavailable in this context."])
    return CommandResult(
        handled=True,
        replies=[f"Session usage: {turns} user turn(s). Token-cost estimate: not yet wired."],
    )


@command("/stop", help="Cancel the current run if one is in progress")
async def _cmd_stop(ctx: CommandContext) -> CommandResult:
    ev = _CANCEL_EVENTS.get(ctx.session_id)
    if ev is None or ev.is_set():
        return CommandResult(handled=True, replies=["Nothing to stop right now."])
    ev.set()
    return CommandResult(
        handled=True,
        replies=["Stop requested — the current run will end at the next checkpoint."],
    )


@command(
    "/personality",
    "/persona",
    help="List/set a personality overlay (~/.nuvel/personalities/<name>.md)",
)
async def _cmd_personality(ctx: CommandContext) -> CommandResult:
    arg = (ctx.text or "").strip()
    if not arg:
        names = _list_personality_names()
        active = _ACTIVE_PERSONALITIES.get(ctx.session_id)
        if not names:
            return CommandResult(
                handled=True,
                replies=[
                    "No personalities found. Drop markdown files into "
                    f"{PERSONALITIES_DIR} (e.g. concise.md), then run /personality <name>."
                ],
            )
        lines = ["Available personalities:"]
        for n in names:
            marker = " (active)" if n == active else ""
            try:
                _, fm = _read_personality(n)
                desc = fm.get("description", "")
            except Exception:
                desc = ""
            suffix = f" — {desc}" if desc else ""
            lines.append(f"  {n}{marker}{suffix}")
        if active and active not in names:
            lines.append(f"Active: {active} (file missing)")
        elif not active:
            lines.append("No personality is active. Use /personality <name>.")
        return CommandResult(handled=True, replies=["\n".join(lines)])

    if arg.lower() in {"off", "reset", "clear", "none"}:
        _clear_active_personality(ctx.session_id)
        return CommandResult(handled=True, replies=["Personality cleared."])

    name = arg.split()[0].lower()
    if not _personality_path(name).is_file():
        avail = ", ".join(_list_personality_names()) or "(none)"
        return CommandResult(
            handled=True,
            replies=[f"No personality named {name!r}. Available: {avail}"],
        )
    _set_active_personality(ctx.session_id, name)
    return CommandResult(handled=True, replies=[f"Personality set to {name!r}."])


# --- /cron slash command ---------------------------------------------------


def _split_argv(text: str) -> list[str]:
    """Lightweight shlex with mismatched-quote tolerance."""
    import shlex
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _origin_for_ctx(ctx: "CommandContext") -> dict[str, Any] | None:
    """Best-effort origin metadata for the gateway dispatching the command.

    The gateway populates ``ctx.extra`` with platform-specific keys when it
    creates the context. Slack/Telegram set ``platform`` plus the channel
    identifiers needed to route a future delivery back to the same place.
    """
    ex = ctx.extra or {}
    platform = ex.get("platform")
    if not platform:
        return None
    if platform == "slack":
        return {
            "platform": "slack",
            "channel": ex.get("channel") or ctx.channel,
            "thread_ts": ex.get("thread_ts"),
        }
    if platform == "telegram":
        return {
            "platform": "telegram",
            "chat_id": ex.get("chat_id") or ctx.channel,
            "message_thread_id": ex.get("message_thread_id"),
        }
    return {"platform": platform}


@command("/cron", help="Manage scheduled prompts (list/add/pause/resume/run/remove)")
async def _cmd_cron(ctx: CommandContext) -> CommandResult:
    try:
        from {{agent_package}}.cron.service import get_service
    except Exception:
        return CommandResult(handled=True, replies=["Cron is not available in this build."])

    svc = get_service()
    argv = _split_argv((ctx.text or "").strip())

    if not argv:
        return CommandResult(handled=True, replies=[
            "Cron commands:\n"
            "  /cron list\n"
            "  /cron add \"<schedule>\" \"<prompt>\" [--name <n>] [--deliver origin|local|slack:<ch>|telegram:<chat>]\n"
            "  /cron pause <id>\n"
            "  /cron resume <id>\n"
            "  /cron run <id>\n"
            "  /cron remove <id>"
        ])

    sub = argv[0].lower()
    rest = argv[1:]

    if sub == "list":
        jobs = svc.list_jobs()
        if not jobs:
            return CommandResult(handled=True, replies=["No cron jobs."])
        lines = ["Cron jobs:"]
        for j in jobs:
            lines.append(
                f"  {j.get('id')}  {j.get('status'):8s}  {j.get('schedule'):20s}"
                f"  next={j.get('next_run_at')}  name={j.get('name')!r}"
            )
        return CommandResult(handled=True, replies=["\n".join(lines)])

    if sub in {"pause", "resume", "run", "remove", "rm", "del", "delete"}:
        if not rest:
            return CommandResult(handled=True, replies=[f"Usage: /cron {sub} <id>"])
        jid = rest[0]
        try:
            if sub == "pause":
                svc.pause(jid)
                return CommandResult(handled=True, replies=[f"Paused {jid}."])
            if sub == "resume":
                svc.resume(jid)
                return CommandResult(handled=True, replies=[f"Resumed {jid}."])
            if sub == "run":
                svc.trigger_now(jid)
                return CommandResult(handled=True, replies=[f"Job {jid} queued for the next tick."])
            # remove/rm/del/delete
            if not svc.delete_job(jid):
                return CommandResult(handled=True, replies=[f"No job {jid!r}."])
            return CommandResult(handled=True, replies=[f"Removed {jid}."])
        except KeyError:
            return CommandResult(handled=True, replies=[f"No job {jid!r}."])
        except ValueError as exc:
            return CommandResult(handled=True, replies=[f"Error: {exc}"])

    if sub == "add":
        # Parse: <schedule> <prompt> [--name N] [--deliver D]
        positional: list[str] = []
        name = ""
        delivery = "origin" if (ctx.extra or {}).get("platform") else "local"
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok in ("--name", "-n") and i + 1 < len(rest):
                name = rest[i + 1]; i += 2; continue
            if tok in ("--deliver", "-d") and i + 1 < len(rest):
                delivery = rest[i + 1]; i += 2; continue
            positional.append(tok); i += 1
        if len(positional) < 2:
            return CommandResult(handled=True, replies=[
                "Usage: /cron add \"<schedule>\" \"<prompt>\" [--name <n>] [--deliver <target>]"
            ])
        schedule = positional[0]
        prompt = " ".join(positional[1:])
        if not name:
            name = f"job-{schedule}"
        origin = _origin_for_ctx(ctx) if delivery == "origin" else None
        try:
            job = svc.create_job(
                name=name, prompt=prompt, schedule=schedule,
                delivery=delivery, origin=origin,
            )
        except ValueError as exc:
            return CommandResult(handled=True, replies=[f"Error: {exc}"])
        return CommandResult(handled=True, replies=[
            f"Scheduled {job['id']}: {job['name']!r} — next run at {job['next_run_at']}"
        ])

    return CommandResult(handled=True, replies=[f"Unknown subcommand: /cron {sub}"])


__all__ = [
    "CommandContext",
    "CommandResult",
    "command",
    "is_command",
    "list_commands",
    "try_dispatch",
    "get_cancel_event",
    "clear_cancel_event",
    "get_active_personality",
    "PERSONALITIES_DIR",
]
