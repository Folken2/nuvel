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
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


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


__all__ = [
    "CommandContext",
    "CommandResult",
    "command",
    "is_command",
    "list_commands",
    "try_dispatch",
    "get_cancel_event",
    "clear_cancel_event",
]
