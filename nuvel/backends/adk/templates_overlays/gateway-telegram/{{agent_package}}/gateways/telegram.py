"""Telegram gateway for {{agent_name}}.

Receives Telegram bot webhook updates at POST /gateways/telegram, verifies
the secret token, dispatches text messages to the in-process ADK runner,
and posts replies via the Telegram Bot API. See the project README for
setup instructions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from {{agent_package}}.gateways._common import (
    InboundAttachment,
    enforce_attachment_limits,
    ensure_session,
    invoke_agent,
    session_key,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/gateways", tags=["gateway:telegram"])

TELEGRAM_API_BASE = "https://api.telegram.org"


def _verify_secret(token: str | None) -> None:
    expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if not expected:
        raise HTTPException(status_code=500, detail="TELEGRAM_WEBHOOK_SECRET not configured")
    if not secrets.compare_digest(token or "", expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def _bot_token() -> str:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not tok:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN not configured")
    return tok


async def _send_message(chat_id: int | str, text: str, *, reply_to: int | None = None,
                         message_thread_id: int | None = None) -> None:
    body: dict = {"chat_id": chat_id, "text": text}
    if reply_to is not None:
        body["reply_to_message_id"] = reply_to
    if message_thread_id is not None:
        body["message_thread_id"] = message_thread_id
    url = f"{TELEGRAM_API_BASE}/bot{_bot_token()}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=body)
        if r.status_code != 200:
            logger.warning("Telegram sendMessage failed: %s %s", r.status_code, r.text[:200])


async def _send_chat_action(chat_id: int | str, action: str = "typing") -> None:
    url = f"{TELEGRAM_API_BASE}/bot{_bot_token()}/sendChatAction"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json={"chat_id": chat_id, "action": action})
    except Exception:
        # Typing indicator is best-effort.
        pass


def _is_invokable_message(update: dict) -> bool:
    """Return True if the message has either text/caption or a supported file part."""
    msg = update.get("message")
    if not isinstance(msg, dict):
        return False
    if isinstance(msg.get("text"), str) and msg["text"]:
        return True
    if isinstance(msg.get("caption"), str) and msg["caption"]:
        return True
    return any(k in msg for k in ("photo", "document", "voice", "audio", "video", "video_note"))


_TELEGRAM_FILE_KINDS: tuple[tuple[str, str, str], ...] = (
    # (msg key, default mime, fallback display name template)
    ("document", "", "{kind}"),
    ("photo", "image/jpeg", "photo.jpg"),
    ("voice", "audio/ogg", "voice.ogg"),
    ("audio", "", "audio"),
    ("video", "video/mp4", "video.mp4"),
    ("video_note", "video/mp4", "video_note.mp4"),
)


def _select_file_descriptor(msg: dict) -> tuple[str, str, str] | None:
    """Pick (file_id, mime_type, display_name) for the first supported file part.

    For `photo`, picks the largest size.
    """
    for key, default_mime, default_name in _TELEGRAM_FILE_KINDS:
        item = msg.get(key)
        if not item:
            continue
        if key == "photo" and isinstance(item, list):
            largest = max(item, key=lambda p: p.get("file_size") or 0)
            return largest["file_id"], default_mime, default_name
        if isinstance(item, dict):
            file_id = item.get("file_id")
            if not file_id:
                continue
            mime = str(item.get("mime_type") or default_mime or "application/octet-stream")
            name = str(item.get("file_name") or default_name.format(kind=key))
            return file_id, mime, name
    return None


async def _fetch_telegram_file(file_id: str) -> tuple[bytes | None, str | None]:
    """Resolve file_id via getFile and download the bytes.

    Returns (bytes, file_path) or (None, None) on failure.
    """
    token = _bot_token()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/getFile",
                json={"file_id": file_id},
            )
            r.raise_for_status()
            data = r.json()
            file_path = (data.get("result") or {}).get("file_path")
            if not file_path:
                return None, None
            url = f"{TELEGRAM_API_BASE}/file/bot{token}/{file_path}"
            dl = await client.get(url)
            dl.raise_for_status()
            return dl.content, file_path
    except Exception:
        logger.exception("Telegram: failed to fetch file_id=%s", file_id)
        return None, None


async def _collect_inbound_files(msg: dict) -> tuple[list[InboundAttachment], list[str]]:
    desc = _select_file_descriptor(msg)
    if desc is None:
        return [], []
    file_id, mime, name = desc
    data, _path = await _fetch_telegram_file(file_id)
    item = InboundAttachment(mime_type=mime, display_name=name, data=data)
    max_count = int(os.environ.get("GATEWAY_MAX_ATTACHMENT_COUNT", "5"))
    max_bytes = int(os.environ.get("GATEWAY_MAX_ATTACHMENT_BYTES", str(10 * 1024 * 1024)))
    return enforce_attachment_limits([item], max_count=max_count, max_bytes=max_bytes)


def _should_invoke_in_group(msg: dict, bot_username: str | None) -> bool:
    """Mirror the well-behaved-bot convention: in groups, only invoke when
    the bot is mentioned, the message is a slash command targeting the bot,
    or the message replies to a bot-authored message."""
    chat_type = (msg.get("chat") or {}).get("type", "private")
    if chat_type == "private":
        return True
    text = msg.get("text") or msg.get("caption") or ""
    if bot_username and f"@{bot_username}" in text:
        return True
    if text.startswith("/"):
        return True
    reply_to = msg.get("reply_to_message") or {}
    if (reply_to.get("from") or {}).get("is_bot"):
        return True
    return False


async def _process_message(request: Request, msg: dict) -> None:
    runner = request.app.state.runner
    app_name = request.app.state.app_name
    user_id, session_id = session_key("telegram", msg)
    await ensure_session(runner.session_service, app_name, user_id, session_id)

    chat_id = (msg.get("chat") or {}).get("id")
    thread_id = msg.get("message_thread_id")
    reply_to = msg.get("message_id") if (msg.get("chat") or {}).get("type") != "private" else None

    text = (msg.get("text") or msg.get("caption") or "").strip()
    attachments, skip_notes = await _collect_inbound_files(msg)
    if skip_notes:
        text = (text + ("\n" if text else "") + "\n".join(skip_notes)).strip()
    if not text and not attachments:
        # Nothing to do.
        return

    inline_max_bytes = int(os.environ.get("GATEWAY_INLINE_DATA_MAX_BYTES", str(4 * 1024 * 1024)))

    # Best-effort typing indicator while the agent runs.
    keepalive = asyncio.create_task(_typing_keepalive(chat_id))
    try:
        reply = await invoke_agent(
            runner, user_id, session_id, text or "(file attached)",
            attachments=attachments, inline_max_bytes=inline_max_bytes,
        )
        reply_text = reply.text
        outbound = reply.attachments
    except Exception:
        logger.exception("Telegram: agent invocation failed")
        reply_text = "Sorry, something went wrong."
        outbound = []
    finally:
        keepalive.cancel()
        try:
            await keepalive
        except asyncio.CancelledError:
            pass

    # Outbound upload comes in Task 6; pass URI-only as link lines for now.
    if outbound:
        link_lines = [f"\n• {a.display_name}: {a.file_uri}" for a in outbound if a.file_uri]
        if link_lines:
            reply_text = f"{reply_text}\n\nAttached:" + "".join(link_lines)
        for a in outbound:
            if a.data and not a.file_uri:
                logger.info("Telegram: outbound attachment with bytes (%s, %d bytes) — upload deferred", a.display_name, len(a.data))

    await _send_message(chat_id, reply_text, reply_to=reply_to, message_thread_id=thread_id)


async def _typing_keepalive(chat_id: int | str) -> None:
    """Re-send `typing` every 4s until cancelled (Telegram's indicator lasts ~5s)."""
    try:
        while True:
            await _send_chat_action(chat_id, "typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        return


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    _verify_secret(x_telegram_bot_api_secret_token)
    update = await request.json()

    if not _is_invokable_message(update):
        return JSONResponse(content={"ok": True, "skipped": "no text or supported file"})

    msg = update["message"]
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME") or None
    if not _should_invoke_in_group(msg, bot_username):
        return JSONResponse(content={"ok": True, "skipped": "group: no mention/command/reply"})

    asyncio.create_task(_process_message(request, msg))
    return JSONResponse(content={"ok": True})
