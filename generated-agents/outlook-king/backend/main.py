"""
FastAPI bridge between the Outlook add-in (Office.js) and outlook-king.

Three responsibilities:

1. POST /api/outlook/context — taskpane pushes the user's current compose
   window and selected message into ADK session state. The agent's
   ``get_current_compose`` / ``get_selected_message`` tools read from there.

2. POST /api/outlook/chat — the user types a prompt in the taskpane.
   Backend runs the agent and returns the final response.

3. POST /api/outlook/chat/stream — same, but SSE-streamed so the UI can
   render tool calls + tokens as they arrive.

4. POST /api/outlook/learn-sent — the taskpane fires this immediately
   after the user clicks Send. The agent records a style fingerprint
   without burning a chat turn.

Run:
    cd generated-agents/outlook-king
    uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# Load .env from the agent root (one level up from backend/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("outlook_king.backend")

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.genai import types

from outlook_king.agent import root_agent
from outlook_king.tools.outlook_context import COMPOSE_KEY, MESSAGE_KEY
from outlook_king.tools.style_tools import learn_style_from_sent_email

APP_NAME = "outlook_king"
DEFAULT_USER_ID = "outlook-user"

session_service = InMemorySessionService()
artifact_service = InMemoryArtifactService()

_known_sessions: set[str] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("outlook-king backend starting on PID %d", os.getpid())
    yield
    logger.info("outlook-king backend shutdown")


app = FastAPI(title="outlook-king backend", lifespan=lifespan)

_cors_origins = [
    o.strip() for o in os.getenv(
        "CORS_ORIGINS",
        "https://localhost:3000,http://localhost:3000,null",
    ).split(",")
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_backend_api_key = os.getenv("BACKEND_API_KEY")


@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    if _backend_api_key and request.url.path.startswith("/api/") and request.method != "OPTIONS":
        if request.headers.get("X-API-Key") != _backend_api_key:
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
    return await call_next(request)


# ── Models ──────────────────────────────────────────────────────────


class ComposePayload(BaseModel):
    body: str = ""
    subject: str = ""
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    mode: str = "newMail"  # "newMail" | "reply" | "forward"
    conversation_id: str | None = None


class SelectedMessagePayload(BaseModel):
    id: str | None = None
    subject: str = ""
    sender: str = Field("", alias="from")
    to: list[str] = Field(default_factory=list)
    body: str = ""
    conversation_id: str | None = None
    received: str | None = None
    has_attachments: bool = False

    class Config:
        populate_by_name = True


class ContextRequest(BaseModel):
    session_id: str
    user_id: str | None = None
    compose: ComposePayload | None = None
    selected: SelectedMessagePayload | None = None


class ChatRequest(BaseModel):
    session_id: str
    user_id: str | None = None
    prompt: str
    compose: ComposePayload | None = None
    selected: SelectedMessagePayload | None = None


class LearnSentRequest(BaseModel):
    body: str
    recipient: str = ""
    subject: str = ""


# ── Session helpers ─────────────────────────────────────────────────


async def _ensure_session(session_id: str, user_id: str) -> None:
    cache_key = f"{user_id}:{session_id}"
    if cache_key in _known_sessions:
        return
    try:
        await session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
    except Exception:
        pass
    _known_sessions.add(cache_key)


async def _write_outlook_state(
    session_id: str,
    user_id: str,
    compose: ComposePayload | None,
    selected: SelectedMessagePayload | None,
) -> None:
    """Persist the current Outlook context into ADK session state.

    State mutations must go through ``session_service.append_event`` with
    ``EventActions(state_delta=…)`` — directly mutating ``session.state[k]``
    bypasses ADK's state tracking and the agent's tools never see the value.
    """
    await _ensure_session(session_id, user_id)
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        raise HTTPException(500, "Failed to load session after creation")

    state_delta: dict = {}
    if compose is not None:
        state_delta[COMPOSE_KEY] = compose.model_dump()
    if selected is not None:
        state_delta[MESSAGE_KEY] = selected.model_dump(by_alias=True)
    if not state_delta:
        return

    event = Event(
        invocation_id=f"ctx-{uuid.uuid4().hex[:8]}",
        author="system",
        actions=EventActions(state_delta=state_delta),
    )
    await session_service.append_event(session, event)


# ── Routes ──────────────────────────────────────────────────────────


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": APP_NAME}


@app.post("/api/outlook/context")
async def push_context(req: ContextRequest):
    """Push the user's current compose + selected message into session state.

    Called by the taskpane every time the Outlook state changes (compose
    body edit, new message selection). Cheap idempotent write.
    """
    user_id = req.user_id or DEFAULT_USER_ID
    await _write_outlook_state(req.session_id, user_id, req.compose, req.selected)
    return {"status": "ok"}


async def _run_agent_once(
    session_id: str,
    user_id: str,
    prompt: str,
    compose: ComposePayload | None,
    selected: SelectedMessagePayload | None,
) -> str:
    """Run the agent for a single user turn and return the final text."""
    await _write_outlook_state(session_id, user_id, compose, selected)

    runner = Runner(
        app_name=APP_NAME,
        agent=root_agent,
        session_service=session_service,
        artifact_service=artifact_service,
    )
    content = types.Content(role="user", parts=[types.Part(text=prompt)])
    final_text = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=content
    ):
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if getattr(part, "text", None):
                    final_text = part.text
    return final_text


@app.post("/api/outlook/chat")
async def chat(req: ChatRequest):
    """Non-streaming chat. Returns the agent's final response."""
    user_id = req.user_id or DEFAULT_USER_ID
    if not req.prompt.strip():
        raise HTTPException(400, "Empty prompt.")
    try:
        text = await _run_agent_once(
            req.session_id, user_id, req.prompt, req.compose, req.selected
        )
    except Exception as exc:
        logger.exception("Agent run failed")
        raise HTTPException(500, f"Agent error: {exc}") from exc
    return {"status": "ok", "message": text}


@app.post("/api/outlook/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE chat. Streams tool events and the final response."""
    user_id = req.user_id or DEFAULT_USER_ID
    if not req.prompt.strip():
        raise HTTPException(400, "Empty prompt.")

    await _write_outlook_state(req.session_id, user_id, req.compose, req.selected)

    async def event_gen():
        try:
            runner = Runner(
                app_name=APP_NAME,
                agent=root_agent,
                session_service=session_service,
                artifact_service=artifact_service,
            )
            content = types.Content(role="user", parts=[types.Part(text=req.prompt)])
            async for event in runner.run_async(
                user_id=user_id, session_id=req.session_id, new_message=content
            ):
                if event.get_function_calls():
                    for call in event.get_function_calls():
                        yield f"event: tool_start\ndata: {json.dumps({'tool': call.name})}\n\n"
                if event.get_function_responses():
                    for resp in event.get_function_responses():
                        yield f"event: tool_end\ndata: {json.dumps({'tool': resp.name})}\n\n"
                if event.is_final_response() and event.content and event.content.parts:
                    text = "".join(p.text or "" for p in event.content.parts)
                    yield f"event: final\ndata: {json.dumps({'text': text})}\n\n"
            yield "event: done\ndata: {}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Stream failed")
            yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/api/outlook/learn-sent")
async def learn_sent(req: LearnSentRequest):
    """Record a style fingerprint after the user sends an email.

    This is fire-and-forget from the taskpane's perspective — no chat turn,
    no agent reasoning. Just a direct write to the writing-style topic.
    """
    if not req.body.strip():
        return {"status": "skip", "reason": "empty body"}
    result = learn_style_from_sent_email(
        body=req.body, recipient=req.recipient, subject=req.subject
    )
    return result
