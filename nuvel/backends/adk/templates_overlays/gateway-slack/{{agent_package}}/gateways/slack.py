"""Slack gateway for {{agent_name}} via Composio Slackbot.

Receives Composio webhook deliveries at POST /gateways/slack/composio,
verifies the shared-secret query parameter, dispatches text messages to
the in-process ADK runner, and posts replies via the SLACKBOT_SEND_MESSAGE
Composio tool.

Setup is documented in this agent's README.md ("Channel: Slack" section).
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from {{agent_package}}.gateways._common import (
    InboundAttachment,
    enforce_attachment_limits,
    ensure_session,
    invoke_agent,
    session_key,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/gateways", tags=["gateway:slack"])

# Triggers that produce a user-facing reply.
INVOKE_TRIGGERS = {
    "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
    "SLACKBOT_CHANNEL_MESSAGE_RECEIVED",
}


def _verify_secret(request: Request) -> None:
    expected = os.environ.get("COMPOSIO_WEBHOOK_SECRET")
    if not expected:
        raise HTTPException(status_code=500, detail="COMPOSIO_WEBHOOK_SECRET not configured")
    provided = request.query_params.get("secret", "")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def _is_self_or_bot(payload: dict) -> bool:
    return bool(payload.get("bot_id")) or bool(payload.get("is_bot_message"))


def _should_invoke_channel_message(payload: dict) -> bool:
    """Channel-mention default: only invoke when the bot was @-mentioned.
    Override with SLACK_CHANNEL_TRIGGER_MODE=all to invoke on every message."""
    if os.environ.get("SLACK_CHANNEL_TRIGGER_MODE", "mention").lower() == "all":
        return True
    text = str(payload.get("text") or "")
    bot_user_id = os.environ.get("SLACK_BOT_USER_ID")
    if bot_user_id and f"<@{bot_user_id}>" in text:
        return True
    return False


async def _send_reply(composio_client, channel: str, text: str,
                      thread_ts: str | None = None) -> None:
    args = {"channel": channel, "markdown_text": text}
    if thread_ts:
        args["thread_ts"] = thread_ts
    try:
        # Composio Python SDK: synchronous .tools.execute(...). Run off-loop.
        await asyncio.to_thread(
            composio_client.tools.execute, "SLACKBOT_SEND_MESSAGE", arguments=args
        )
    except Exception:
        logger.exception("Slack: SLACKBOT_SEND_MESSAGE failed")


async def _download_slack_file(url: str, token: str) -> bytes | None:
    """Download a Slack file via its url_private with bot-token auth."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.content
    except Exception:
        logger.exception("Slack: failed to download file %s", url)
        return None


async def _collect_inbound_files(payload: dict) -> tuple[list[InboundAttachment], list[str]]:
    """Build InboundAttachment list for Slack `files[]`.

    Returns (kept_attachments, skip_notes_for_prompt).
    """
    files = payload.get("files") or []
    if not isinstance(files, list) or not files:
        return [], []

    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    if not bot_token:
        logger.warning(
            "Slack: payload contains %d file(s) but SLACK_BOT_TOKEN is unset — "
            "falling back to URL forwarding (most agent models cannot fetch authenticated URLs).",
            len(files),
        )

    items: list[InboundAttachment] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        url = str(f.get("url_private") or f.get("url_private_download") or "")
        mime = str(f.get("mimetype") or "application/octet-stream")
        name = str(f.get("name") or "slack-file")
        data: bytes | None = None
        if bot_token and url:
            data = await _download_slack_file(url, bot_token)
        items.append(InboundAttachment(
            mime_type=mime, display_name=name, data=data, file_uri=url or None,
        ))

    max_count = int(os.environ.get("GATEWAY_MAX_ATTACHMENT_COUNT", "5"))
    max_bytes = int(os.environ.get("GATEWAY_MAX_ATTACHMENT_BYTES", str(10 * 1024 * 1024)))
    return enforce_attachment_limits(items, max_count=max_count, max_bytes=max_bytes)


async def _process(request: Request, payload: dict, *, in_thread: bool) -> None:
    runner = request.app.state.runner
    app_name = request.app.state.app_name
    composio = request.app.state.composio_client

    user_id, session_id = session_key("slack", payload)
    await ensure_session(runner.session_service, app_name, user_id, session_id)

    text = str(payload.get("text") or "")
    attachments, skip_notes = await _collect_inbound_files(payload)
    if skip_notes:
        text = text + ("\n" + "\n".join(skip_notes) if text else "\n".join(skip_notes))

    inline_max_bytes = int(os.environ.get("GATEWAY_INLINE_DATA_MAX_BYTES", str(4 * 1024 * 1024)))
    try:
        reply = await invoke_agent(
            runner, user_id, session_id, text,
            attachments=attachments, inline_max_bytes=inline_max_bytes,
        )
    except Exception:
        logger.exception("Slack: agent invocation failed")
        reply_text = "Sorry, something went wrong."
        outbound: list = []
    else:
        reply_text = reply.text
        outbound = reply.attachments

    # Outbound upload comes in Task 4; for now pass through URI-only attachments as links.
    if outbound:
        link_lines = [f"\n• {a.display_name}: {a.file_uri}" for a in outbound if a.file_uri]
        if link_lines:
            reply_text = f"{reply_text}\n\nAttached:" + "".join(link_lines)
        for a in outbound:
            if a.data and not a.file_uri:
                logger.info("Slack: outbound attachment with bytes (%s, %d bytes) — upload deferred", a.display_name, len(a.data))

    channel = payload.get("channel")
    thread_ts = payload.get("thread_ts") or payload.get("ts") if in_thread else None
    await _send_reply(composio, channel, reply_text, thread_ts=thread_ts)


@router.post("/slack/composio")
async def composio_webhook(request: Request):
    _verify_secret(request)
    body = await request.json()
    slug = body.get("trigger_slug", "")
    payload = body.get("payload") or {}

    if slug not in INVOKE_TRIGGERS:
        # All other triggers (reactions, channel_created, future ones): log only.
        logger.info("Slack: log-only trigger %s", slug)
        return JSONResponse(content={"ok": True, "skipped": "log-only trigger"})

    if _is_self_or_bot(payload):
        return JSONResponse(content={"ok": True, "skipped": "bot/self message"})

    if slug == "SLACKBOT_CHANNEL_MESSAGE_RECEIVED" and not _should_invoke_channel_message(payload):
        return JSONResponse(content={"ok": True, "skipped": "channel: no mention"})

    in_thread = slug == "SLACKBOT_CHANNEL_MESSAGE_RECEIVED"
    asyncio.create_task(_process(request, payload, in_thread=in_thread))
    return JSONResponse(content={"ok": True})
