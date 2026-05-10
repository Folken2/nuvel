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
from {{agent_package}}.gateways.commands import CommandContext, try_dispatch
from {{agent_package}}.gateways.transcription import (
    FALLBACK_MARKER,
    TranscriptionError,
    audio_marker,
    is_audio_attachment,
    transcribe_audio,
    transcription_enabled,
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


async def _send_photo(chat_id: int | str, *, data: bytes | None, file_uri: str | None,
                     caption: str | None, reply_to: int | None,
                     message_thread_id: int | None, filename: str) -> None:
    url = f"{TELEGRAM_API_BASE}/bot{_bot_token()}/sendPhoto"
    fields: dict = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption
    if reply_to is not None:
        fields["reply_to_message_id"] = str(reply_to)
    if message_thread_id is not None:
        fields["message_thread_id"] = str(message_thread_id)

    async with httpx.AsyncClient(timeout=60) as client:
        if data is not None:
            files = {"photo": (filename, data, "application/octet-stream")}
            r = await client.post(url, data=fields, files=files)
        else:
            body = dict(fields)
            body["photo"] = file_uri
            r = await client.post(url, json=body)
        if r.status_code != 200:
            logger.warning("Telegram sendPhoto failed: %s %s", r.status_code, r.text[:200])
            raise RuntimeError(f"sendPhoto returned {r.status_code}")


async def _send_document(chat_id: int | str, *, data: bytes | None, file_uri: str | None,
                        mime_type: str, caption: str | None, reply_to: int | None,
                        message_thread_id: int | None, filename: str) -> None:
    url = f"{TELEGRAM_API_BASE}/bot{_bot_token()}/sendDocument"
    fields: dict = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption
    if reply_to is not None:
        fields["reply_to_message_id"] = str(reply_to)
    if message_thread_id is not None:
        fields["message_thread_id"] = str(message_thread_id)

    async with httpx.AsyncClient(timeout=60) as client:
        if data is not None:
            files = {"document": (filename, data, mime_type or "application/octet-stream")}
            r = await client.post(url, data=fields, files=files)
        else:
            body = dict(fields)
            body["document"] = file_uri
            r = await client.post(url, json=body)
        if r.status_code != 200:
            logger.warning("Telegram sendDocument failed: %s %s", r.status_code, r.text[:200])
            raise RuntimeError(f"sendDocument returned {r.status_code}")


def _is_image(mime: str) -> bool:
    return mime.lower().startswith("image/")


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
    if data is None:
        return [], [f'[attachment "{name}" skipped: fetch failed]']
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


async def _transcribe_voice_attachments(
    attachments: list[InboundAttachment],
    msg: dict,
) -> tuple[list[InboundAttachment], list[str]]:
    """Strip audio attachments and transcribe them inline.

    Returns (kept_attachments, voice_markers). When transcription is disabled
    or no attachments are audio, returns (attachments, []) unchanged.
    """
    if not transcription_enabled() or not attachments:
        return attachments, []

    # Telegram exposes duration (seconds) on `voice` and `audio` parts.
    duration: float | None = None
    for key in ("voice", "audio", "video_note"):
        item = msg.get(key)
        if isinstance(item, dict) and item.get("duration"):
            try:
                duration = float(item["duration"])
                break
            except (TypeError, ValueError):
                pass

    kept: list[InboundAttachment] = []
    markers: list[str] = []
    for att in attachments:
        if not is_audio_attachment(att.mime_type, att.display_name):
            kept.append(att)
            continue
        if att.data is None:
            logger.warning("Telegram: skipping voice memo %r (no bytes)", att.display_name)
            markers.append(FALLBACK_MARKER)
            continue
        try:
            transcript = await transcribe_audio(att.data, att.mime_type)
        except TranscriptionError:
            logger.exception("Telegram: transcription failed for %r", att.display_name)
            markers.append(FALLBACK_MARKER)
            continue
        markers.append(audio_marker(transcript, duration))
    return kept, markers


async def _process_message(request: Request, msg: dict) -> None:
    runner = request.app.state.runner
    app_name = request.app.state.app_name
    user_id, session_id = session_key("telegram", msg)
    await ensure_session(runner.session_service, app_name, user_id, session_id)

    chat_id = (msg.get("chat") or {}).get("id")
    thread_id = msg.get("message_thread_id")
    reply_to = msg.get("message_id") if (msg.get("chat") or {}).get("type") != "private" else None

    text = (msg.get("text") or msg.get("caption") or "").strip()

    # Slash-command interception. Runs before forwarding to the agent.
    cmd_ctx = CommandContext(
        user_id=user_id, channel=str(chat_id), session_id=session_id,
        text=text, runner=runner, app_name=app_name,
        reply=lambda t: _send_message(chat_id, t, reply_to=reply_to, message_thread_id=thread_id),
    )
    cmd_result = await try_dispatch(text, cmd_ctx)
    if cmd_result.handled:
        for line in cmd_result.replies:
            await _send_message(chat_id, line, reply_to=reply_to, message_thread_id=thread_id)
        return

    attachments, skip_notes = await _collect_inbound_files(msg)
    if skip_notes:
        text = (text + ("\n" if text else "") + "\n".join(skip_notes)).strip()

    # Voice-memo transcription (opt-in via GATEWAY_TRANSCRIBE_AUDIO=1).
    # Audio attachments are removed from the forwarded list and replaced with
    # an inline marker in the text so the agent sees a regular text turn.
    attachments, voice_markers = await _transcribe_voice_attachments(attachments, msg)
    if voice_markers:
        text = (text + ("\n" if text else "") + "\n".join(voice_markers)).strip()

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

    if not reply_text and not outbound:
        return

    if outbound:
        first_caption = reply_text or None
        first_ok = False
        for i, a in enumerate(outbound):
            cap = first_caption if i == 0 else None
            try:
                if _is_image(a.mime_type):
                    await _send_photo(
                        chat_id, data=a.data, file_uri=a.file_uri,
                        caption=cap, reply_to=reply_to,
                        message_thread_id=thread_id, filename=a.display_name,
                    )
                else:
                    await _send_document(
                        chat_id, data=a.data, file_uri=a.file_uri,
                        mime_type=a.mime_type, caption=cap,
                        reply_to=reply_to, message_thread_id=thread_id,
                        filename=a.display_name,
                    )
                if i == 0:
                    first_ok = True
            except Exception:
                logger.exception("Telegram: outbound send failed for %s", a.display_name)
        if first_ok or not reply_text:
            return  # text was carried in caption, or there was no text to send
        # First send failed — fall through so the text reply still goes out.

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
