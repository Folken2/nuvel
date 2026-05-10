"""Voice-memo transcription for messaging gateways.

Provider-agnostic façade around cloud Whisper-style transcription APIs.
Used by the Slack and Telegram gateways to convert inbound voice notes to
text BEFORE the agent sees them, so a voice memo arrives as a regular
text turn (e.g. "[Voice memo, 0:23]: <transcript>").

Activation
----------
Off by default. Set `GATEWAY_TRANSCRIBE_AUDIO=1` to enable.

Provider selection
------------------
`GATEWAY_TRANSCRIBE_PROVIDER` — one of `openai` (default) or `groq`.

  - `openai` — Whisper via the OpenAI audio.transcriptions REST endpoint.
    Requires `OPENAI_API_KEY`. Default model: `whisper-1`.
  - `groq` — Groq's Whisper-large-v3 endpoint (faster + cheaper).
    Requires `GROQ_API_KEY`. Default model: `whisper-large-v3`.

Both adapters call HTTP directly via `httpx` (already a gateway dep) so we
don't pull in the full `openai` SDK for one endpoint.

Helpers
-------
`is_audio_attachment(mime_type, filename)` — does this look like a voice memo
the gateway should transcribe?

`audio_marker(transcript, duration_seconds)` — render the inline marker text
that replaces the audio attachment in the user prompt.
"""

from __future__ import annotations

import logging
import os
from pathlib import PurePosixPath

import httpx

logger = logging.getLogger(__name__)

# File extensions we treat as voice memos when the mime_type is unknown.
_AUDIO_EXTS = (".ogg", ".oga", ".opus", ".m4a", ".mp3", ".wav", ".webm", ".flac", ".aac")

# Default model names per provider.
_DEFAULT_MODELS = {
    "openai": "whisper-1",
    "groq": "whisper-large-v3",
}

# Endpoint per provider.
_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/audio/transcriptions",
    "groq": "https://api.groq.com/openai/v1/audio/transcriptions",
}

# Env var holding the API key per provider.
_API_KEY_VARS = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
}


class TranscriptionError(RuntimeError):
    """Raised when audio transcription fails for any reason."""


def is_audio_attachment(mime_type: str | None, filename: str | None = None) -> bool:
    """True iff `mime_type` or `filename` looks like a voice memo."""
    if mime_type and mime_type.lower().startswith("audio/"):
        return True
    if filename:
        ext = PurePosixPath(filename).suffix.lower()
        if ext in _AUDIO_EXTS:
            return True
    return False


def transcription_enabled() -> bool:
    """True iff `GATEWAY_TRANSCRIBE_AUDIO=1`."""
    return os.environ.get("GATEWAY_TRANSCRIBE_AUDIO", "0").strip() in ("1", "true", "True", "yes")


def audio_marker(transcript: str, duration_seconds: int | float | None = None) -> str:
    """Render the inline marker that replaces the audio attachment."""
    if duration_seconds is not None:
        try:
            total = int(duration_seconds)
            mm, ss = divmod(max(total, 0), 60)
            stamp = f"{mm}:{ss:02d}"
            return f"[Voice memo, {stamp}]: {transcript}".strip()
        except (TypeError, ValueError):
            pass
    return f"[Voice memo]: {transcript}".strip()


# Sent on transcription failure so the user knows their voice note arrived.
FALLBACK_MARKER = "[Voice memo received but transcription failed]"


def _filename_for_upload(mime_type: str) -> str:
    """Cloud APIs require a filename with a recognized extension."""
    mt = (mime_type or "").lower()
    if "ogg" in mt or "opus" in mt:
        return "voice.ogg"
    if "mpeg" in mt or "mp3" in mt:
        return "voice.mp3"
    if "wav" in mt or "x-wav" in mt:
        return "voice.wav"
    if "m4a" in mt or "mp4" in mt or "aac" in mt:
        return "voice.m4a"
    if "webm" in mt:
        return "voice.webm"
    if "flac" in mt:
        return "voice.flac"
    return "voice.ogg"


async def transcribe_audio(audio_bytes: bytes, mime_type: str) -> str:
    """Transcribe `audio_bytes` to text. Provider chosen by env var.

    Raises:
        TranscriptionError: provider unsupported, API key missing, HTTP error,
            or no text returned.
    """
    if not audio_bytes:
        raise TranscriptionError("empty audio payload")

    provider = os.environ.get("GATEWAY_TRANSCRIBE_PROVIDER", "openai").strip().lower()
    if provider not in _ENDPOINTS:
        raise TranscriptionError(f"unsupported transcription provider: {provider!r}")

    api_key = os.environ.get(_API_KEY_VARS[provider])
    if not api_key:
        raise TranscriptionError(
            f"{_API_KEY_VARS[provider]} is not set — required for provider {provider!r}"
        )

    model = (
        os.environ.get("GATEWAY_TRANSCRIBE_MODEL")
        or _DEFAULT_MODELS[provider]
    )
    timeout = float(os.environ.get("GATEWAY_TRANSCRIBE_TIMEOUT_SEC", "60"))

    filename = _filename_for_upload(mime_type)
    url = _ENDPOINTS[provider]
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": (filename, audio_bytes, mime_type or "application/octet-stream")}
    data = {"model": model, "response_format": "json"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, data=data, files=files)
    except httpx.HTTPError as exc:
        raise TranscriptionError(f"{provider} HTTP error: {exc!r}") from exc

    if response.status_code != 200:
        raise TranscriptionError(
            f"{provider} returned {response.status_code}: {response.text[:200]}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise TranscriptionError(f"{provider} returned non-JSON body") from exc

    text = (body or {}).get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise TranscriptionError(f"{provider} returned no transcript text")
    return text.strip()
