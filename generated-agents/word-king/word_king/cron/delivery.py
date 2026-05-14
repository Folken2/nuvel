"""Cron output delivery.

Wraps the agent's final response and routes it to the configured
destination. Honors the ``[SILENT]`` prefix as a "skip delivery" signal
(the output file is still written by the scheduler).

Supported delivery strings:

* ``local``                — file-only, no message sent.
* ``origin``               — re-use the gateway path the job was created from.
* ``slack:<channel>``      — direct to a specific Slack channel via Composio.
* ``telegram:<chat_id>``   — direct to a Telegram chat via Bot API.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SILENT_PREFIX = "[SILENT]"


@dataclass
class DeliveryResult:
    delivered: bool
    silent: bool = False
    target: str = ""
    error: str | None = None


def is_silent(text: str) -> bool:
    return bool(text) and text.lstrip().lower().startswith(SILENT_PREFIX.lower())


def strip_silent(text: str) -> str:
    if not is_silent(text):
        return text
    s = text.lstrip()
    return s[len(SILENT_PREFIX):].lstrip()


def wrap_response(name: str, output: str) -> str:
    """Wrap with the canonical cron header/footer."""
    return (
        f"Cronjob Response: {name}\n"
        "-------------\n"
        f"{output}\n\n"
        "Note: The agent cannot see this message, and therefore cannot respond to it."
    )


async def _send_slack(channel: str, text: str) -> None:
    """Direct Slack send using Composio. Best-effort; logs on failure."""
    import asyncio
    try:
        from word_king.gateways._common import get_composio_client
    except Exception as exc:
        raise RuntimeError("slack delivery requires the gateway-base overlay") from exc
    client = get_composio_client()
    await asyncio.to_thread(
        client.tools.execute,
        "SLACKBOT_SEND_MESSAGE",
        arguments={"channel": channel, "markdown_text": text},
    )


async def _send_telegram(chat_id: str, text: str) -> None:
    import httpx  # type: ignore[import-not-found]
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN unset — telegram delivery unavailable")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json={"chat_id": chat_id, "text": text})
        if r.status_code != 200:
            raise RuntimeError(f"telegram sendMessage failed: {r.status_code} {r.text[:200]}")


async def _send_origin(origin: dict[str, Any], text: str) -> None:
    platform = origin.get("platform")
    if platform == "slack":
        channel = origin.get("channel")
        if not channel:
            raise RuntimeError("origin missing slack channel")
        await _send_slack(str(channel), text)
        return
    if platform == "telegram":
        chat_id = origin.get("chat_id") or origin.get("channel")
        if chat_id is None:
            raise RuntimeError("origin missing telegram chat_id")
        await _send_telegram(str(chat_id), text)
        return
    raise RuntimeError(f"unknown origin platform: {platform!r}")


async def deliver(
    *,
    name: str,
    response: str,
    delivery: str,
    origin: dict[str, Any] | None = None,
    wrap: bool = True,
) -> DeliveryResult:
    """Deliver ``response`` per ``delivery``. Never raises — returns a result."""
    if is_silent(response):
        return DeliveryResult(delivered=False, silent=True, target=delivery)

    body = wrap_response(name, response) if wrap else response

    if delivery == "local":
        return DeliveryResult(delivered=True, target="local")

    try:
        if delivery == "origin":
            if not origin:
                return DeliveryResult(
                    delivered=False, target="origin",
                    error="no origin metadata stored on the job",
                )
            await _send_origin(origin, body)
            return DeliveryResult(delivered=True, target="origin")

        if delivery.startswith("slack:"):
            await _send_slack(delivery.split(":", 1)[1], body)
            return DeliveryResult(delivered=True, target=delivery)

        if delivery.startswith("telegram:"):
            await _send_telegram(delivery.split(":", 1)[1], body)
            return DeliveryResult(delivered=True, target=delivery)

        return DeliveryResult(
            delivered=False, target=delivery,
            error=f"unknown delivery target {delivery!r}",
        )
    except Exception as exc:
        logger.exception("cron: delivery failed for %s", delivery)
        return DeliveryResult(delivered=False, target=delivery, error=str(exc))
