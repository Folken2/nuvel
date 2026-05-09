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

from {{agent_package}}.gateways._common import ensure_session, invoke_agent, session_key

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


def _is_text_message(update: dict) -> bool:
    msg = update.get("message")
    return isinstance(msg, dict) and isinstance(msg.get("text"), str) and bool(msg["text"])


def _should_invoke_in_group(msg: dict, bot_username: str | None) -> bool:
    """Mirror the well-behaved-bot convention: in groups, only invoke when
    the bot is mentioned, the message is a slash command targeting the bot,
    or the message replies to a bot-authored message."""
    chat_type = (msg.get("chat") or {}).get("type", "private")
    if chat_type == "private":
        return True
    text = msg.get("text", "")
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

    # Best-effort typing indicator while the agent runs.
    keepalive = asyncio.create_task(_typing_keepalive(chat_id))
    try:
        reply = await invoke_agent(runner, user_id, session_id, msg["text"])
    except Exception:
        logger.exception("Telegram: agent invocation failed")
        reply = "Sorry, something went wrong."
    finally:
        keepalive.cancel()
        try:
            await keepalive
        except asyncio.CancelledError:
            pass

    await _send_message(chat_id, reply, reply_to=reply_to, message_thread_id=thread_id)


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

    if not _is_text_message(update):
        return JSONResponse(content={"ok": True, "skipped": "non-text update"})

    msg = update["message"]
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME") or None
    if not _should_invoke_in_group(msg, bot_username):
        return JSONResponse(content={"ok": True, "skipped": "group: no mention/command/reply"})

    asyncio.create_task(_process_message(request, msg))
    return JSONResponse(content={"ok": True})
