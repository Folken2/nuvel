"""
FastAPI bridge between the PowerPoint add-in (Office.js) and ppt-king.

Four responsibilities:

1. POST /api/ppt/context — taskpane pushes the user's current slide and
   deck outline into ADK session state. The agent's ``get_current_slide``
   and ``get_deck_outline`` tools read from there.

2. POST /api/ppt/chat — the user types a prompt in the taskpane.
   Backend runs the agent and returns the final response.

3. POST /api/ppt/chat/stream — same, but SSE-streamed so the UI can
   render tool calls + tokens as they arrive.

4. POST /api/ppt/learn-slide — the taskpane fires this immediately
   after the user keeps a generated or tightened slide. The agent
   records a deck-style fingerprint without burning a chat turn.

Run:
    cd generated-agents/ppt-king
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
logger = logging.getLogger("ppt_king.backend")

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.genai import types

from ppt_king.agent import root_agent
from ppt_king.tools.ppt_context import (
    CURRENT_SLIDE_KEY,
    DECK_OUTLINE_KEY,
    OPENED_PRESENTATION_KEY,
    PENDING_ACTIONS_KEY,
    RECENT_EDITS_KEY,
)
from ppt_king.tools.style_tools import learn_style_from_kept_slide

APP_NAME = "ppt_king"
DEFAULT_USER_ID = "ppt-user"

session_service = InMemorySessionService()
artifact_service = InMemoryArtifactService()

_known_sessions: set[str] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ppt-king backend starting on PID %d", os.getpid())
    yield
    logger.info("ppt-king backend shutdown")


app = FastAPI(title="ppt-king backend", lifespan=lifespan)

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


class SelectedShapePayload(BaseModel):
    name: str = ""
    type: str = ""
    text: str = ""
    left: float = 0.0
    top: float = 0.0
    width: float = 0.0
    height: float = 0.0
    is_placeholder: bool = False


class CurrentSlidePayload(BaseModel):
    index: int = 0
    slide_id: str = ""
    title: str = ""
    bullets: list[str] = Field(default_factory=list)
    notes: str = ""
    layout_name: str = ""
    shape_count: int = 0
    selected_shapes: list[SelectedShapePayload] = Field(default_factory=list)


class DeckSlideSummary(BaseModel):
    index: int
    slide_id: str = ""
    title: str = ""
    bullet_count: int = 0
    has_notes: bool = False


class DeckOutlinePayload(BaseModel):
    slide_count: int = 0
    slides: list[DeckSlideSummary] = Field(default_factory=list)


class RecordEditRequest(BaseModel):
    session_id: str
    user_id: str | None = None
    action: str
    slide_index: int = -1
    summary: str = ""


class ContextRequest(BaseModel):
    session_id: str
    user_id: str | None = None
    current_slide: CurrentSlidePayload | None = None
    deck_outline: DeckOutlinePayload | None = None


class ChatRequest(BaseModel):
    session_id: str
    user_id: str | None = None
    prompt: str
    current_slide: CurrentSlidePayload | None = None
    deck_outline: DeckOutlinePayload | None = None


class PresentationOpenedRequest(BaseModel):
    session_id: str
    user_id: str | None = None
    title: str = ""
    slide_count: int = 0
    slide_titles: list[str] = Field(default_factory=list)
    is_new: bool = False


class LearnSlideRequest(BaseModel):
    title: str = ""
    bullets: list[str] = Field(default_factory=list)
    notes: str = ""
    layout_name: str = ""


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


async def _write_ppt_state(
    session_id: str,
    user_id: str,
    current_slide: CurrentSlidePayload | None,
    deck_outline: DeckOutlinePayload | None,
) -> None:
    """Persist the current PowerPoint context into ADK session state.

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
    if current_slide is not None:
        state_delta[CURRENT_SLIDE_KEY] = current_slide.model_dump()
    if deck_outline is not None:
        state_delta[DECK_OUTLINE_KEY] = deck_outline.model_dump()
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


@app.post("/api/ppt/context")
async def push_context(req: ContextRequest):
    """Push the user's current slide + deck outline into session state.

    Called by the taskpane every time the PowerPoint state changes (slide
    selection, slide content edit, deck open / close). Cheap idempotent
    write.
    """
    user_id = req.user_id or DEFAULT_USER_ID
    await _write_ppt_state(req.session_id, user_id, req.current_slide, req.deck_outline)
    return {"status": "ok"}


async def _drain_pending_actions(session_id: str, user_id: str) -> list[dict]:
    """Pop the agent's queued actions from session state and return them."""
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        return []
    actions = list(session.state.get(PENDING_ACTIONS_KEY) or [])
    if not actions:
        return []
    event = Event(
        invocation_id=f"drain-{uuid.uuid4().hex[:8]}",
        author="system",
        actions=EventActions(state_delta={PENDING_ACTIONS_KEY: []}),
    )
    await session_service.append_event(session, event)
    return actions


async def _run_agent_once(
    session_id: str,
    user_id: str,
    prompt: str,
    current_slide: CurrentSlidePayload | None,
    deck_outline: DeckOutlinePayload | None,
) -> tuple[str, list[dict]]:
    """Run the agent for a single user turn.

    Returns ``(final_text, pending_actions)``. ``pending_actions`` is the
    queue of actions the agent's tools enqueued during the turn — the
    taskpane is expected to execute them via Office.js.
    """
    await _write_ppt_state(session_id, user_id, current_slide, deck_outline)

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
    actions = await _drain_pending_actions(session_id, user_id)
    return final_text, actions


@app.post("/api/ppt/chat")
async def chat(req: ChatRequest):
    """Non-streaming chat. Returns the agent's final response."""
    user_id = req.user_id or DEFAULT_USER_ID
    if not req.prompt.strip():
        raise HTTPException(400, "Empty prompt.")
    try:
        text, actions = await _run_agent_once(
            req.session_id, user_id, req.prompt, req.current_slide, req.deck_outline
        )
    except Exception as exc:
        logger.exception("Agent run failed")
        raise HTTPException(500, f"Agent error: {exc}") from exc
    return {"status": "ok", "message": text, "actions": actions}


@app.post("/api/ppt/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE chat. Streams tool events and the final response."""
    user_id = req.user_id or DEFAULT_USER_ID
    if not req.prompt.strip():
        raise HTTPException(400, "Empty prompt.")

    await _write_ppt_state(req.session_id, user_id, req.current_slide, req.deck_outline)

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
            actions = await _drain_pending_actions(req.session_id, user_id)
            if actions:
                yield f"event: actions\ndata: {json.dumps({'actions': actions})}\n\n"
            yield "event: done\ndata: {}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Stream failed")
            yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/api/ppt/learn-slide")
async def learn_slide(req: LearnSlideRequest):
    """Record a deck-style fingerprint after the user keeps a slide.

    This is fire-and-forget from the taskpane's perspective — no chat turn,
    no agent reasoning. Just a direct write to the deck-style topic.
    """
    has_any = bool(req.title.strip() or req.bullets or req.notes.strip())
    if not has_any:
        return {"status": "skip", "reason": "empty slide"}
    result = learn_style_from_kept_slide(
        title=req.title,
        bullets=req.bullets,
        notes=req.notes,
        layout_name=req.layout_name,
    )
    return result


@app.post("/api/ppt/presentation-opened")
async def presentation_opened(req: PresentationOpenedRequest):
    """Record an early-context snapshot of the presentation the user just opened.

    Called by the taskpane the first time it mounts in a new session,
    *before* any chat turn — so the agent already knows the deck title,
    slide count, and slide titles when the user types their first prompt.
    Stored under ``ppt:opened_presentation``; the agent reads it via
    ``get_opened_presentation_snapshot``.

    Note: This is the closest analogue to a PowerPoint ``OnDocumentOpened``
    event we can wire today — the unified JSON manifest lists
    ``OnDocumentOpened`` as "Not yet supported" for PowerPoint
    (see README "JSON manifest features"). So the taskpane does the work
    on mount rather than the host firing a background handler.

    TODO(agent): hook this into a session-bootstrap callback so the agent
    can emit a one-line "I see you opened X — want a quick outline review?"
    proactively, rather than waiting for the first user prompt.
    """
    if not req.title.strip() and req.slide_count <= 0 and not req.slide_titles:
        raise HTTPException(400, "Empty presentation snapshot.")
    user_id = req.user_id or DEFAULT_USER_ID
    await _ensure_session(req.session_id, user_id)
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=req.session_id
    )
    if session is None:
        raise HTTPException(500, "Failed to load session")
    import datetime as _dt
    payload = {
        "title": req.title,
        "slide_count": req.slide_count,
        "slide_titles": list(req.slide_titles),
        "is_new": bool(req.is_new),
        "opened_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    event = Event(
        invocation_id=f"open-{uuid.uuid4().hex[:8]}",
        author="system",
        actions=EventActions(state_delta={OPENED_PRESENTATION_KEY: payload}),
    )
    await session_service.append_event(session, event)
    return {"status": "ok", "snapshot": payload}


@app.post("/api/ppt/record-edit")
async def record_edit(req: RecordEditRequest):
    """Append an entry to the session's rolling recent-edits log.

    Called by the taskpane after it successfully runs an agent-queued
    action against PowerPoint. The log lives in session state under
    ``ppt:recent_edits`` and is capped at 10 most recent entries — the
    agent reads it via ``get_recent_edits``.
    """
    user_id = req.user_id or DEFAULT_USER_ID
    await _ensure_session(req.session_id, user_id)
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=req.session_id
    )
    if session is None:
        raise HTTPException(500, "Failed to load session")
    edits = list(session.state.get(RECENT_EDITS_KEY) or [])
    import datetime as _dt
    edits.append({
        "action": req.action,
        "slide_index": req.slide_index,
        "summary": req.summary,
        "timestamp": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    })
    edits = edits[-10:]
    event = Event(
        invocation_id=f"edit-{uuid.uuid4().hex[:8]}",
        author="system",
        actions=EventActions(state_delta={RECENT_EDITS_KEY: edits}),
    )
    await session_service.append_event(session, event)
    return {"status": "ok", "count": len(edits)}
