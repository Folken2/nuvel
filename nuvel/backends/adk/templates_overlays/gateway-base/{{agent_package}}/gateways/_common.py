"""Shared helpers for in-process messaging gateways (Slack, Telegram).

Teams uses its own sidecar and does not import this module; its session-key
composition is duplicated inside `teams_bridge.py` to keep the sidecar
independently importable.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService
from google.genai import types as genai_types

logger = logging.getLogger(__name__)


def session_key(platform: str, payload: dict[str, Any]) -> tuple[str, str]:
    """Compose (user_id, session_id) for an inbound platform event.

    See spec §6 for the policy table. Hybrid: thread-scoped in channels,
    user-scoped in DMs.

    Raises:
        ValueError: if `platform` is unknown.
    """
    if platform == "slack":
        team = payload.get("team_id") or payload.get("team", "unknown")
        user = payload.get("user", "anonymous")
        channel = payload.get("channel", "unknown")
        is_dm = payload.get("channel_type") == "im" or str(channel).startswith("D")
        if is_dm:
            return f"slack:{team}:{user}", f"slack:dm:{team}:{channel}"
        thread = payload.get("thread_ts") or payload.get("ts")
        return f"slack:{team}:{user}", f"slack:thread:{team}:{channel}:{thread}"

    if platform == "telegram":
        from_user = (payload.get("from") or {}).get("id", "anonymous")
        chat = payload.get("chat") or {}
        chat_type = chat.get("type", "private")
        chat_id = chat.get("id", "unknown")
        if chat_type == "private":
            return f"telegram:{from_user}", f"telegram:dm:{from_user}"
        thread = payload.get("message_thread_id")
        suffix = f":{thread}" if thread is not None else ""
        return f"telegram:{from_user}", f"telegram:group:{chat_id}{suffix}"

    raise ValueError(f"Unknown platform: {platform!r}")


async def ensure_session(
    session_service: BaseSessionService,
    app_name: str,
    user_id: str,
    session_id: str,
) -> None:
    """Create the session if it does not already exist. Idempotent."""
    existing = await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    if existing is None:
        await session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id, state={}
        )


async def invoke_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    text: str,
) -> str:
    """Run the agent in-process and return the final assistant text reply.

    Iterates `runner.run_async(...)` events, collects all text parts emitted
    by non-user events, and returns the **last non-empty** text — matching
    the v1 Teams bridge's extraction rule.
    """
    new_message = genai_types.Content(role="user", parts=[genai_types.Part(text=text)])
    texts: list[str] = []
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=new_message
    ):
        if getattr(event, "author", None) == "user":
            continue
        content = getattr(event, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            piece = getattr(part, "text", None)
            if piece:
                texts.append(piece)
    return texts[-1] if texts else "Agent did not return text."


def get_composio_client():
    """Lazy import: only used when the Slack overlay is active."""
    from composio import Composio
    return Composio(api_key=os.environ.get("COMPOSIO_API_KEY"))
