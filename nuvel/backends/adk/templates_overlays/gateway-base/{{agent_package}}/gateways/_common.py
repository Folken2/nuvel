"""Shared helpers for in-process messaging gateways (Slack, Telegram).

Teams uses its own sidecar and does not import this module; its session-key
composition is duplicated inside `teams_bridge.py` to keep the sidecar
independently importable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService
from google.genai import types as genai_types

logger = logging.getLogger(__name__)


@dataclass
class InboundAttachment:
    """A platform-side file inbound to the agent.

    Either `data` (preferred) or `file_uri` should be set.
    """
    mime_type: str
    display_name: str
    data: bytes | None = None
    file_uri: str | None = None


@dataclass
class OutboundAttachment:
    """An agent-side artifact outbound to the platform."""
    mime_type: str
    display_name: str
    data: bytes | None = None
    file_uri: str | None = None


@dataclass
class AgentReply:
    """Structured reply returned by `invoke_agent`."""
    text: str
    attachments: list[OutboundAttachment] = field(default_factory=list)


def _humanize_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def attachments_to_parts(
    items: list[InboundAttachment],
    *,
    inline_max_bytes: int,
) -> list[genai_types.Part]:
    """Convert inbound attachments to ADK Parts.

    - bytes <= inline_max_bytes -> Part(inline_data=Blob)
    - else if file_uri set -> Part(file_data=FileData)
    - else -> Part(text=...) skip note (so the agent has a hint)
    """
    parts: list[genai_types.Part] = []
    for item in items:
        if item.data is not None and len(item.data) <= inline_max_bytes:
            parts.append(genai_types.Part(
                inline_data=genai_types.Blob(mime_type=item.mime_type, data=item.data)
            ))
            continue
        if item.file_uri:
            parts.append(genai_types.Part(
                file_data=genai_types.FileData(
                    file_uri=item.file_uri,
                    mime_type=item.mime_type,
                    display_name=item.display_name,
                )
            ))
            continue
        size_hint = _humanize_bytes(len(item.data)) if item.data is not None else "no bytes available"
        logger.warning(
            "Gateway: dropping attachment %r — no usable representation (%s)",
            item.display_name, size_hint,
        )
        parts.append(genai_types.Part(
            text=f'[attachment "{item.display_name}" ({size_hint}) skipped: no usable representation]'
        ))
    return parts


def enforce_attachment_limits(
    items: list[InboundAttachment],
    *,
    max_count: int,
    max_bytes: int,
) -> tuple[list[InboundAttachment], list[str]]:
    """Trim list to max_count and drop items whose `data` exceeds max_bytes.

    Returns (kept_items, skip_notes). Each skip_note is a single-line string
    suitable for appending to the user's prompt.
    """
    kept: list[InboundAttachment] = []
    notes: list[str] = []
    for i, item in enumerate(items):
        if i >= max_count:
            notes.append(
                f'[attachment "{item.display_name}" skipped: exceeds GATEWAY_MAX_ATTACHMENT_COUNT ({max_count})]'
            )
            continue
        if item.data is not None and len(item.data) > max_bytes:
            notes.append(
                f'[attachment "{item.display_name}" ({_humanize_bytes(len(item.data))}) '
                f'skipped: exceeds GATEWAY_MAX_ATTACHMENT_BYTES ({_humanize_bytes(max_bytes)})]'
            )
            continue
        kept.append(item)
    return kept, notes


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
    attachments: list[InboundAttachment] | None = None,
    *,
    inline_max_bytes: int = 4_194_304,
) -> AgentReply:
    """Run the agent in-process and return text + collected outbound artifacts.

    Reads three sources for outbound attachments on each non-user event:
      - `inline_data` parts (Blob)
      - `file_data` parts (FileData with file_uri)
      - `actions.artifact_delta` (loaded via runner.artifact_service if set)

    Inbound attachments are converted via `attachments_to_parts` and prepended
    after the user-text part.
    """
    parts: list[genai_types.Part] = [genai_types.Part(text=text)]
    if attachments:
        parts.extend(attachments_to_parts(attachments, inline_max_bytes=inline_max_bytes))
    new_message = genai_types.Content(role="user", parts=parts)

    texts: list[str] = []
    out_attachments: list[OutboundAttachment] = []
    seen_keys: set[tuple[str, int]] = set()

    artifact_service = getattr(runner, "artifact_service", None)
    app_name = getattr(runner, "app_name", "")

    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=new_message
    ):
        if getattr(event, "author", None) == "user":
            continue

        # Walk content parts.
        content = getattr(event, "content", None)
        for part in (getattr(content, "parts", None) or []):
            piece = getattr(part, "text", None)
            if piece:
                texts.append(piece)
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                key = (inline.mime_type, len(inline.data))
                if key not in seen_keys:
                    seen_keys.add(key)
                    out_attachments.append(OutboundAttachment(
                        mime_type=inline.mime_type,
                        display_name="agent-output",
                        data=inline.data,
                    ))
            fdata = getattr(part, "file_data", None)
            if fdata is not None and getattr(fdata, "file_uri", None):
                out_attachments.append(OutboundAttachment(
                    mime_type=getattr(fdata, "mime_type", "") or "application/octet-stream",
                    display_name=getattr(fdata, "display_name", "") or "agent-file",
                    file_uri=fdata.file_uri,
                ))

        # Walk artifact_delta entries (saved via tool_context.save_artifact).
        actions = getattr(event, "actions", None)
        delta = getattr(actions, "artifact_delta", None) or {}
        if delta and artifact_service is None:
            logger.info(
                "Gateway: agent emitted %d artifact(s) but no artifact_service is configured; skipping.",
                len(delta),
            )
            continue
        for filename, version in delta.items():
            try:
                loaded = await artifact_service.load_artifact(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                    filename=filename,
                    version=version,
                )
            except Exception:
                logger.exception("Gateway: load_artifact failed for %s@%s", filename, version)
                continue
            if loaded is None:
                continue
            inline = getattr(loaded, "inline_data", None)
            fdata = getattr(loaded, "file_data", None)
            if inline is not None and getattr(inline, "data", None):
                key = (inline.mime_type, len(inline.data))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                out_attachments.append(OutboundAttachment(
                    mime_type=inline.mime_type,
                    display_name=filename,
                    data=inline.data,
                ))
            elif fdata is not None and getattr(fdata, "file_uri", None):
                out_attachments.append(OutboundAttachment(
                    mime_type=getattr(fdata, "mime_type", "") or "application/octet-stream",
                    display_name=filename,
                    file_uri=fdata.file_uri,
                ))

    return AgentReply(
        text=texts[-1] if texts else "Agent did not return text.",
        attachments=out_attachments,
    )


def get_composio_client():
    """Lazy import: only used when the Slack overlay is active.

    Raises RuntimeError at construction time if COMPOSIO_API_KEY is unset,
    so misconfiguration fails loudly at server startup instead of silently
    at the first webhook delivery.
    """
    from composio import Composio
    api_key = os.environ.get("COMPOSIO_API_KEY")
    if not api_key:
        raise RuntimeError("COMPOSIO_API_KEY is required when the Slack gateway is active.")
    return Composio(api_key=api_key)
