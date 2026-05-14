"""
Bidirectional streaming support via Gemini Live API.

Activated when STREAMING_ENABLED=true. Provides a WebSocket endpoint
at /ws/{user_id}/{session_id} for real-time voice/video communication.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import secrets

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.runners import Runner
from google.genai import types

logger = logging.getLogger(__name__)


def create_run_config() -> RunConfig:
    """Build RunConfig for bidirectional streaming with Gemini Live API."""
    return RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        session_resumption=types.SessionResumptionConfig(),
    )


async def _upstream_task(
    websocket: WebSocket,
    live_request_queue: LiveRequestQueue,
) -> None:
    """Receive messages from WebSocket client and forward to LiveRequestQueue."""
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "text")

            if msg_type == "text":
                content = types.Content(
                    parts=[types.Part(text=data.get("text", ""))]
                )
                live_request_queue.send_content(content)

            elif msg_type == "audio":
                audio_bytes = base64.b64decode(data["data"])
                audio_blob = types.Blob(
                    mime_type=data.get("mime_type", "audio/pcm;rate=16000"),
                    data=audio_bytes,
                )
                live_request_queue.send_realtime(audio_blob)

            elif msg_type == "activity_end":
                live_request_queue.send_activity_end()

    except WebSocketDisconnect:
        logger.info("Client disconnected (upstream)")
    except Exception as e:
        logger.error("Upstream error: %s", e)


async def _downstream_task(
    websocket: WebSocket,
    runner: Runner,
    user_id: str,
    session_id: str,
    live_request_queue: LiveRequestQueue,
    run_config: RunConfig,
) -> None:
    """Consume events from run_live() and stream to WebSocket client."""
    try:
        async for event in runner.run_live(
            user_id=user_id,
            session_id=session_id,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
            await websocket.send_text(
                event.model_dump_json(exclude_none=True, by_alias=True)
            )
    except WebSocketDisconnect:
        logger.info("Client disconnected (downstream)")
    except Exception as e:
        logger.error("Downstream error: %s", e)


def mount_streaming(
    app: FastAPI,
    runner: Runner,
    session_service,
    app_name: str,
) -> None:
    """Register the WebSocket streaming endpoint on the FastAPI app."""

    api_key = os.getenv("API_KEY", "")

    @app.websocket("/ws/{user_id}/{session_id}")
    async def websocket_endpoint(
        websocket: WebSocket,
        user_id: str,
        session_id: str,
        token: str = Query(default=""),
    ) -> None:
        # Auth check
        if api_key and not secrets.compare_digest(token, api_key):
            await websocket.close(code=4001, reason="Unauthorized")
            return

        await websocket.accept()
        logger.info("WebSocket connected: user=%s session=%s", user_id, session_id)

        # Get or create session
        session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if not session:
            await session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )

        live_request_queue = LiveRequestQueue()
        run_config = create_run_config()

        try:
            await asyncio.gather(
                _upstream_task(websocket, live_request_queue),
                _downstream_task(
                    websocket, runner, user_id, session_id,
                    live_request_queue, run_config,
                ),
                return_exceptions=True,
            )
        finally:
            live_request_queue.close()
            logger.info("WebSocket closed: user=%s session=%s", user_id, session_id)

    logger.info("Streaming endpoint mounted: /ws/{user_id}/{session_id}")
