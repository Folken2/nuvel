"""
FastAPI bridge between the Outlook add-in (Office.js) and outlook-king.

Responsibilities:

1. POST /api/outlook/context — taskpane pushes the user's current compose
   window, selected message, and account info into ADK session state. The
   agent's ``get_current_compose`` / ``get_selected_message`` /
   ``get_outlook_account`` tools read from there.

2. POST /api/outlook/chat — non-streaming chat. Returns the agent's final
   response *plus* any pending Outlook actions the agent queued during
   the turn (see outlook_actions.py).

3. POST /api/outlook/chat/stream — same as /chat but SSE-streamed so the
   UI can render tool calls + tokens + queued actions as they arrive.

4. POST /api/outlook/learn-sent — fired by the taskpane immediately after
   the user clicks Send. Records a style fingerprint without burning a
   chat turn.

5. POST /api/outlook/action-result — the taskpane posts the outcome of
   each executed action back here. The result is stored in session state
   so the agent can inspect it on the next turn via
   ``get_recent_action_results``.

Run:
    cd generated-agents/outlook-king
    uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# psycopg's async pool can't use Windows' default ProactorEventLoop —
# every Neon connection retries-and-fails without this. No-op on POSIX.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("outlook_king.backend")

from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.genai import types

from outlook_king.agent import root_agent
from outlook_king.tools.outlook_context import (
    COMPOSE_KEY,
    MESSAGE_KEY,
    ACCOUNT_KEY,
    RECENT_ACTIONS_KEY,
    COMPOSE_DRAFT_KEY,
    SPAM_REPORTS_KEY,
)
from outlook_king.tools.outlook_actions import (
    PENDING_ACTIONS_KEY,
    ACTION_RESULTS_KEY,
)
from outlook_king.tools.attachment_tools import (
    FETCHED_ATTACHMENTS_KEY,
    MAX_FETCH_BYTES,
)
from outlook_king.utils.attachment_extract import (
    extract_attachment_text,
    guess_mime_type,
)
from outlook_king.tools.style_tools import record_sent_fingerprint
from outlook_king.state.memory_service import NeonMemoryService
from outlook_king.state.memory_service_dev import InMemoryMemoryService
from outlook_king.state.memory_singleton import set_memory_service
from outlook_king.plugins.memory_plugin import MemoryPlugin
from outlook_king.plugins.context_budget_plugin import ContextBudgetPlugin

APP_NAME = "outlook_king"
MAX_RECENT_ACTIONS = 25

session_service = InMemorySessionService()
artifact_service = InMemoryArtifactService()


def _build_compaction_config() -> EventsCompactionConfig | None:
    """Conversation compaction via ADK's built-in sliding window.

    Every COMPACTION_INTERVAL user invocations, older events are
    summarized into a single compaction event (carrying
    COMPACTION_OVERLAP previously-compacted invocations for
    continuity), so plain conversational history stops growing
    unboundedly. The summarizer defaults to the root agent's own model
    (works with LiteLLM/OpenRouter). Complements ContextBudgetPlugin,
    which elides heavy tool payloads immediately rather than every N
    turns. Set COMPACTION_INTERVAL=0 to disable.
    """
    interval = int(os.getenv("COMPACTION_INTERVAL", "10"))
    if interval <= 0:
        return None
    overlap = int(os.getenv("COMPACTION_OVERLAP", "2"))
    return EventsCompactionConfig(
        compaction_interval=interval, overlap_size=overlap
    )


# Single App shared by all requests: root agent + plugins + compaction.
# (Runner's `plugins=` argument is deprecated — they live on the App now.)
adk_app = App(
    name=APP_NAME,
    root_agent=root_agent,
    plugins=[MemoryPlugin(), ContextBudgetPlugin()],
    events_compaction_config=_build_compaction_config(),
)

_known_sessions: set[str] = set()
_db_pool: AsyncConnectionPool | None = None
_memory_service: NeonMemoryService | InMemoryMemoryService | None = None


def _is_dev_mode() -> bool:
    return os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_pool, _memory_service
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        _db_pool = AsyncConnectionPool(database_url, min_size=1, max_size=10, open=False)
        await _db_pool.open()
        _memory_service = NeonMemoryService(_db_pool, app_name=APP_NAME)
        logger.info("outlook-king backend starting on PID %d (Neon pool open)", os.getpid())
    elif _is_dev_mode():
        _memory_service = InMemoryMemoryService(app_name=APP_NAME)
        logger.info(
            "outlook-king backend starting on PID %d "
            "(DEV_MODE: in-memory memory service, resets on restart)",
            os.getpid(),
        )
    else:
        raise RuntimeError(
            "DATABASE_URL is required. Point it at your Neon connection string, "
            "or set DEV_MODE=true for an in-memory store that resets on restart."
        )
    set_memory_service(_memory_service)
    try:
        yield
    finally:
        logger.info("outlook-king backend shutdown")
        if _db_pool is not None:
            await _db_pool.close()


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


class AttachmentInfo(BaseModel):
    name: str = ""
    size: int = 0
    content_type: str = ""
    is_inline: bool = False
    id: str | None = None


class ComposePayload(BaseModel):
    body: str = ""
    body_html: str = ""
    subject: str = ""
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    mode: str = "newMail"  # "newMail" | "reply" | "forward"
    conversation_id: str | None = None
    selection: str = ""
    selection_is_html: bool = False
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    importance: str = "normal"


class SelectedMessagePayload(BaseModel):
    id: str | None = None
    subject: str = ""
    sender: str = Field("", alias="from")
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    body: str = ""
    conversation_id: str | None = None
    received: str | None = None
    has_attachments: bool = False
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    folder: str = ""
    categories: list[str] = Field(default_factory=list)
    flag: str = "none"  # "none" | "flagged" | "complete"

    class Config:
        populate_by_name = True


class AccountPayload(BaseModel):
    email: str = ""
    display_name: str = ""
    time_zone: str = ""
    host: str = ""  # "Outlook" | "OutlookWebApp" | etc.
    platform: str = ""


class ContextRequest(BaseModel):
    session_id: str
    user_id: str | None = None  # deprecated, ignored; resolved from X-User-Email header
    compose: ComposePayload | None = None
    selected: SelectedMessagePayload | None = None
    account: AccountPayload | None = None


class ChatRequest(BaseModel):
    session_id: str
    user_id: str | None = None  # deprecated, ignored; resolved from X-User-Email header
    prompt: str
    compose: ComposePayload | None = None
    selected: SelectedMessagePayload | None = None
    account: AccountPayload | None = None


class LearnSentRequest(BaseModel):
    body: str
    recipient: str = ""
    subject: str = ""


class ComposeOpenedRequest(BaseModel):
    session_id: str
    user_id: str | None = None  # deprecated, ignored; resolved from X-User-Email header
    compose_type: str = "newMail"  # "newMail" | "reply" | "forward"
    compose: ComposePayload | None = None


class PreSendCheckRequest(BaseModel):
    session_id: str
    user_id: str | None = None  # deprecated, ignored; resolved from X-User-Email header
    compose: ComposePayload | None = None


class SpamReportRequest(BaseModel):
    session_id: str
    user_id: str | None = None  # deprecated, ignored; resolved from X-User-Email header
    message_id: str | None = None
    conversation_id: str | None = None
    subject: str = ""
    sender: str = ""
    options: list | dict | None = None
    free_text: str = ""


class ActionResultRequest(BaseModel):
    session_id: str
    user_id: str | None = None  # deprecated, ignored; resolved from X-User-Email header
    action_id: str
    action_type: str = ""
    status: str = "ok"  # "ok" | "error" | "skipped"
    error: str = ""
    detail: dict = Field(default_factory=dict)


class AttachmentContentRequest(BaseModel):
    session_id: str
    user_id: str | None = None  # deprecated, ignored; resolved from X-User-Email header
    attachment_id: str
    name: str
    content_type: str = ""
    # Office.js AttachmentContentFormat: "base64" for files, "eml" /
    # "iCalendar" arrive as plain text. "url" (cloud attachments) is
    # rejected client-side — there are no bytes to upload.
    format: str = "base64"
    content: str


class FrontendLogEntry(BaseModel):
    level: str = "info"
    message: str = ""
    data: dict | None = None
    timestamp: str = ""
    source: str = "frontend"


class FrontendLogBatch(BaseModel):
    entries: list[FrontendLogEntry] = Field(default_factory=list)


# ── Error classification ────────────────────────────────────────────
#
# Raw exception strings must never reach the add-in — they can contain
# connection strings, provider payloads, or tracebacks. Every agent-run
# failure is mapped to a stable code + a message safe to show the user;
# the raw exception only goes to the server log.


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def classify_agent_error(exc: BaseException) -> tuple[int, str, str]:
    """Map an exception to ``(http_status, error_code, user_message)``."""
    chain = _exception_chain(exc)
    names = {type(e).__name__ for e in chain}
    modules = {type(e).__module__ or "" for e in chain}
    text = " ".join(str(e) for e in chain).lower()

    if any(m.startswith("psycopg") for m in modules) or "PoolTimeout" in names:
        return (
            503,
            "memory_unavailable",
            "The assistant's memory store is temporarily unavailable. "
            "Please try again in a moment.",
        )
    if "RateLimitError" in names or "rate limit" in text or "quota exceeded" in text:
        return (
            503,
            "llm_rate_limited",
            "The AI service is handling too many requests right now. "
            "Please try again in a few seconds.",
        )
    if (
        any(n in names for n in ("AuthenticationError", "PermissionDeniedError"))
        or "invalid api key" in text
        or "authentication" in text
    ):
        return (
            502,
            "llm_auth_failed",
            "The AI service rejected this deployment's credentials. "
            "Please contact your administrator.",
        )
    if (
        any("Timeout" in n or n == "TimeoutError" for n in names)
        or "timed out" in text
        or "timeout" in text
    ):
        return (
            504,
            "upstream_timeout",
            "The request took too long to complete. Please try again.",
        )
    if any(n in names for n in ("APIConnectionError", "ConnectionError", "ServiceUnavailableError")) or (
        "connection" in text and any(w in text for w in ("refused", "reset", "closed", "failed"))
    ):
        return (
            503,
            "upstream_unavailable",
            "A service the assistant depends on is temporarily unreachable. "
            "Please try again in a moment.",
        )
    return (
        500,
        "internal_error",
        "Something went wrong while processing your request. Please try "
        "again — if it keeps happening, contact support.",
    )


def _agent_error_detail(exc: BaseException) -> tuple[int, dict]:
    if isinstance(exc, HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {
            "code": "internal_error",
            "message": str(exc.detail),
        }
        return exc.status_code, detail
    status, code, message = classify_agent_error(exc)
    return status, {"code": code, "message": message}


# ── Session helpers ─────────────────────────────────────────────────


async def _ensure_session(session_id: str, user_id: str) -> None:
    cache_key = f"{user_id}:{session_id}"
    if cache_key in _known_sessions:
        return
    try:
        existing = await session_service.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        if existing is None:
            await session_service.create_session(
                app_name=APP_NAME, user_id=user_id, session_id=session_id
            )
    except Exception as exc:
        logger.exception("Failed to ensure session %s for user %s", session_id, user_id)
        raise HTTPException(
            503,
            {
                "code": "session_unavailable",
                "message": "Could not initialize the conversation session. Please try again.",
            },
        ) from exc
    _known_sessions.add(cache_key)


async def _append_state_delta(
    session_id: str, user_id: str, state_delta: dict, author: str = "system"
) -> None:
    """Persist a state delta via the ADK event stream.

    Mutating ``session.state[k]`` directly bypasses ADK's state tracking
    and the agent's tools never see the value — always go through
    ``append_event`` with ``EventActions(state_delta=…)``.
    """
    if not state_delta:
        return
    await _ensure_session(session_id, user_id)
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        raise HTTPException(
            503,
            {
                "code": "session_unavailable",
                "message": "Could not load the conversation session. Please try again.",
            },
        )
    event = Event(
        invocation_id=f"sys-{uuid.uuid4().hex[:8]}",
        author=author,
        actions=EventActions(state_delta=state_delta),
    )
    await session_service.append_event(session, event)


async def _write_outlook_state(
    session_id: str,
    user_id: str,
    compose: ComposePayload | None,
    selected: SelectedMessagePayload | None,
    account: AccountPayload | None = None,
) -> None:
    state_delta: dict = {}
    if compose is not None:
        state_delta[COMPOSE_KEY] = compose.model_dump()
    if selected is not None:
        state_delta[MESSAGE_KEY] = selected.model_dump(by_alias=True)
    if account is not None:
        state_delta[ACCOUNT_KEY] = account.model_dump()
    await _append_state_delta(session_id, user_id, state_delta)


async def _drain_pending_actions(session_id: str, user_id: str) -> list[dict]:
    """Pop the queued Outlook actions from session state and return them.

    Called once at the end of every chat turn. Actions are then shipped
    to the add-in for execution.
    """
    await _ensure_session(session_id, user_id)
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        return []
    pending = list(session.state.get(PENDING_ACTIONS_KEY) or [])
    if not pending:
        return []
    await _append_state_delta(
        session_id, user_id, {PENDING_ACTIONS_KEY: []}
    )
    return pending


async def get_user_id(
    x_user_email: str = Header(..., alias="X-User-Email"),
    x_user_display_name: str | None = Header(None, alias="X-User-Display-Name"),
) -> str:
    """Resolve the X-User-Email header to a stable surrogate user_id.

    Idempotent — upserts on every request (cheap; bumps last_seen_at).
    Missing header → FastAPI returns 422 automatically (Header(...)).
    """
    assert _memory_service is not None  # lifespan invariant
    return await _memory_service.upsert_user(x_user_email, x_user_display_name)


# ── Routes ──────────────────────────────────────────────────────────


@app.get("/api/health")
async def health():
    """Liveness + dependency probe.

    Returns 200 when fully healthy, 503 with per-check detail when a
    dependency is down — so load balancers and the add-in's warm-up
    banner can tell "booting" apart from "up but degraded".
    """
    checks: dict[str, str] = {}
    healthy = True
    if _db_pool is not None:
        try:
            async with _db_pool.connection(timeout=2) as conn:
                await conn.execute("SELECT 1")
            checks["database"] = "ok"
        except Exception as exc:
            logger.warning("Health check: database unreachable: %s", exc)
            checks["database"] = "error"
            healthy = False
    body = {"status": "ok" if healthy else "degraded", "app": APP_NAME, "checks": checks}
    if not healthy:
        return JSONResponse(status_code=503, content=body)
    return body


_frontend_logger = logging.getLogger("outlook_king.frontend")
_FRONTEND_LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
}


@app.post("/api/logs")
async def ingest_frontend_logs(batch: FrontendLogBatch):
    """Sink for the add-in's batched remote logger (src/config/logger.ts).

    Without this route every frontend log line 404s silently and add-in
    failures are invisible in server logs.
    """
    for entry in batch.entries[:100]:
        level = _FRONTEND_LOG_LEVELS.get(entry.level, logging.INFO)
        _frontend_logger.log(
            level, "%s | %s | %s", entry.source, entry.message, entry.data or {}
        )
    return {"status": "ok"}


@app.post("/api/outlook/context")
async def push_context(req: ContextRequest, user_id: str = Depends(get_user_id)):
    """Push the user's current Outlook view into session state.

    Called by the taskpane every time the Outlook state changes (compose
    body edit, selection change, new message selection). Cheap idempotent
    write — safe to call on every keystroke debounce.
    """
    await _write_outlook_state(
        req.session_id, user_id, req.compose, req.selected, req.account
    )
    return {"status": "ok"}


async def _run_agent_once(
    session_id: str,
    user_id: str,
    prompt: str,
    compose: ComposePayload | None,
    selected: SelectedMessagePayload | None,
    account: AccountPayload | None,
) -> tuple[str, list[dict]]:
    await _write_outlook_state(session_id, user_id, compose, selected, account)

    runner = Runner(
        app=adk_app,
        session_service=session_service,
        artifact_service=artifact_service,
        memory_service=_memory_service,
    )
    content = types.Content(role="user", parts=[types.Part(text=prompt)])
    final_text = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=content
    ):
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                text_part = getattr(part, "text", None)
                if text_part:
                    final_text = text_part
    actions = await _drain_pending_actions(session_id, user_id)
    return final_text, actions


@app.post("/api/outlook/chat")
async def chat(req: ChatRequest, user_id: str = Depends(get_user_id)):
    """Non-streaming chat. Returns the agent's final response + actions."""
    if not req.prompt.strip():
        raise HTTPException(400, "Empty prompt.")
    try:
        text, actions = await _run_agent_once(
            req.session_id, user_id, req.prompt, req.compose, req.selected, req.account
        )
    except HTTPException:
        raise
    except Exception as exc:
        status, detail = _agent_error_detail(exc)
        logger.exception("Agent run failed (code=%s)", detail.get("code"))
        raise HTTPException(status, detail) from exc
    return {"status": "ok", "message": text, "actions": actions}


@app.post("/api/outlook/chat/stream")
async def chat_stream(req: ChatRequest, user_id: str = Depends(get_user_id)):
    """SSE chat. Streams tool events, the final response, and queued actions."""
    if not req.prompt.strip():
        raise HTTPException(400, "Empty prompt.")

    await _write_outlook_state(
        req.session_id, user_id, req.compose, req.selected, req.account
    )

    async def event_gen():
        try:
            runner = Runner(
                app=adk_app,
                session_service=session_service,
                artifact_service=artifact_service,
                memory_service=_memory_service,
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
            for act in actions:
                yield f"event: action\ndata: {json.dumps(act)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _, detail = _agent_error_detail(exc)
            logger.exception("Stream failed (code=%s)", detail.get("code"))
            yield f"event: error\ndata: {json.dumps(detail)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/api/outlook/learn-sent")
async def learn_sent(req: LearnSentRequest, user_id: str = Depends(get_user_id)):
    """Record a style fingerprint after the user sends an email.

    Fire-and-forget from the taskpane — no chat turn, no agent reasoning.
    """
    if not req.body.strip():
        return {"status": "skip", "reason": "empty body"}
    return await record_sent_fingerprint(
        user_id=user_id,
        body=req.body,
        recipient=req.recipient,
        subject=req.subject,
    )


@app.post("/api/outlook/action-result")
async def action_result(req: ActionResultRequest, user_id: str = Depends(get_user_id)):
    """Record the outcome of an action the add-in just executed.

    Stored in session state under ``outlook:action_results`` (full log,
    bounded) and ``outlook:recent_actions`` (recent compact summary). The
    agent can inspect either via ``get_recent_action_results`` /
    ``get_full_outlook_state`` on the next turn.
    """
    await _ensure_session(req.session_id, user_id)
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=req.session_id
    )
    if session is None:
        raise HTTPException(
            404,
            {"code": "session_not_found", "message": "Conversation session not found."},
        )

    existing_results = list(session.state.get(ACTION_RESULTS_KEY) or [])
    existing_results.append(
        {
            "action_id": req.action_id,
            "type": req.action_type,
            "status": req.status,
            "error": req.error,
            "detail": req.detail,
        }
    )
    existing_results = existing_results[-MAX_RECENT_ACTIONS:]

    recent = list(session.state.get(RECENT_ACTIONS_KEY) or [])
    recent.append({"type": req.action_type, "status": req.status})
    recent = recent[-MAX_RECENT_ACTIONS:]

    await _append_state_delta(
        req.session_id,
        user_id,
        {ACTION_RESULTS_KEY: existing_results, RECENT_ACTIONS_KEY: recent},
    )
    return {"status": "ok"}


@app.post("/api/outlook/attachment-content")
async def attachment_content(
    req: AttachmentContentRequest, user_id: str = Depends(get_user_id)
):
    """Receive attachment bytes from the add-in (``fetch_attachment`` action).

    Raw content is stored as an ADK artifact (``attachment:<name>``); for
    PDF / Excel / CSV / text files the extracted text lands in a companion
    artifact (``attachment_text:<name>``). An index entry is written to
    session state under ``outlook:fetched_attachments`` so the agent's
    ``read_attachment`` / ``load_artifacts`` tools can find it next turn.
    """
    fmt = (req.format or "base64").lower()
    if fmt == "url":
        raise HTTPException(
            422,
            {
                "code": "cloud_attachment",
                "message": "Cloud attachments only expose a link — their "
                "content can't be downloaded via Office.js.",
            },
        )
    if fmt == "base64":
        try:
            data = base64.b64decode(req.content, validate=True)
        except Exception:
            raise HTTPException(
                400, {"code": "bad_content", "message": "content is not valid base64."}
            )
    else:
        # eml / iCalendar item attachments arrive as plain text.
        data = req.content.encode("utf-8", errors="replace")
    if not data:
        raise HTTPException(
            400, {"code": "bad_content", "message": "Attachment content is empty."}
        )
    if len(data) > MAX_FETCH_BYTES:
        raise HTTPException(
            413,
            {
                "code": "attachment_too_large",
                "message": f"Attachment exceeds the "
                f"{MAX_FETCH_BYTES // (1024 * 1024)} MB limit.",
            },
        )

    mime = guess_mime_type(req.name, req.content_type)
    raw_artifact = f"attachment:{req.name}"
    await _ensure_session(req.session_id, user_id)
    await artifact_service.save_artifact(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=req.session_id,
        filename=raw_artifact,
        artifact=types.Part.from_bytes(data=data, mime_type=mime),
    )

    extraction = extract_attachment_text(data, req.name, mime)
    text_artifact = None
    if extraction.text:
        text_artifact = f"attachment_text:{req.name}"
        await artifact_service.save_artifact(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=req.session_id,
            filename=text_artifact,
            artifact=types.Part.from_text(text=extraction.text),
        )

    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=req.session_id
    )
    fetched = dict((session.state.get(FETCHED_ATTACHMENTS_KEY) if session else None) or {})
    fetched[req.name] = {
        "name": req.name,
        "attachment_id": req.attachment_id,
        "content_type": mime,
        "size_bytes": len(data),
        "kind": extraction.kind,
        "artifact": raw_artifact,
        "text_artifact": text_artifact,
        "text_chars": len(extraction.text or ""),
        "extraction_error": extraction.error,
        # Navigation aids so the agent can decide what to read without
        # pulling the document into context.
        "structure": extraction.structure,
        "preview": (extraction.text or "")[:300],
    }
    await _append_state_delta(
        req.session_id, user_id, {FETCHED_ATTACHMENTS_KEY: fetched}
    )
    logger.info(
        "attachment stored: %s (%d bytes, kind=%s, text_chars=%d)",
        req.name,
        len(data),
        extraction.kind,
        len(extraction.text or ""),
    )
    return {
        "status": "ok",
        "name": req.name,
        "kind": extraction.kind,
        "text_chars": len(extraction.text or ""),
        "extraction_error": extraction.error,
    }


# ── JSON-manifest-only event hooks ──────────────────────────────────
#
# Powered by manifest.json's `autoRunEvents` + spamPreProcessingDialog
# surfaces. The XML manifest doesn't wire any of these, so they only
# fire when the add-in is sideloaded as JSON.


_ATTACHMENT_HINTS = (
    "attached",
    "attachment",
    "attachments",
    "enclosed",
    "adjunto",
    "adjunta",
    "adjuntos",
    "se adjunta",
    "ci-joint",
    "anexo",
    "anbei",
    "allegato",
)


def _missing_attachment_heuristic(body: str, attachments: list) -> tuple[bool, str]:
    """Block-friendly heuristic: body mentions an attachment but none exist.

    Returns ``(allow, message)``. ``allow=False`` triggers a soft-block in
    the Smart Alerts dialog. Kept intentionally dumb — the agent will own
    smarter checks later.
    """
    text = (body or "").lower()
    if not text.strip():
        return True, ""
    if attachments:
        return True, ""
    if any(h in text for h in _ATTACHMENT_HINTS):
        return (
            False,
            "Your draft mentions an attachment, but nothing is attached. "
            "Attach the file or remove the reference, then send again.",
        )
    return True, ""


@app.post("/api/outlook/compose-opened")
async def compose_opened(req: ComposeOpenedRequest, user_id: str = Depends(get_user_id)):
    """Push an early compose-mode snapshot into session state.

    Fired by the JSON manifest's ``OnNewMessageCompose`` / ``OnMessageCompose``
    event handlers. Lets the agent answer "what's in my draft?" before the
    user opens the task pane. Stored under ``outlook:compose_draft``.
    """
    snapshot = (req.compose.model_dump() if req.compose else {}) | {
        "compose_type": req.compose_type,
    }
    await _append_state_delta(req.session_id, user_id, {COMPOSE_DRAFT_KEY: snapshot})
    return {"status": "ok"}


@app.post("/api/outlook/pre-send-check")
async def pre_send_check(req: PreSendCheckRequest, user_id: str = Depends(get_user_id)):
    """Smart Alerts pre-send check.

    Runs cheap heuristics and returns ``{allow, message}``. The add-in
    soft-blocks the send when ``allow=False`` and surfaces ``message``.

    Currently only the missing-attachment heuristic is wired. Future
    checks (tone, missing recipients, agent-side review) should be added
    here — call the agent via ``Runner`` if you need a model in the loop.
    The contract back to the add-in must stay ``{allow, message}``.
    """
    del user_id  # required header only — defense-in-depth for identity invariant
    body = req.compose.body if req.compose else ""
    atts = [a.model_dump() for a in (req.compose.attachments if req.compose else [])]
    allow, message = _missing_attachment_heuristic(body, atts)
    # TODO(agent): wire optional agent-side review here. For now this is
    # the only concrete check; tone / missing-recipient / etc. are stubs.
    return {"allow": allow, "message": message}


@app.post("/api/outlook/report-spam")
async def report_spam(req: SpamReportRequest, user_id: str = Depends(get_user_id)):
    """Integrated spam-reporting sink.

    Logs the report and appends metadata to session state so the agent
    can later triage. Full agent integration is intentionally stubbed.
    """
    entry = {
        "message_id": req.message_id,
        "conversation_id": req.conversation_id,
        "subject": req.subject,
        "sender": req.sender,
        "options": req.options,
        "free_text": req.free_text,
    }
    logger.info("spam-report received: %s", entry)
    await _ensure_session(req.session_id, user_id)
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=req.session_id
    )
    existing = list((session.state.get(SPAM_REPORTS_KEY) if session else None) or [])
    existing.append(entry)
    existing = existing[-MAX_RECENT_ACTIONS:]
    await _append_state_delta(req.session_id, user_id, {SPAM_REPORTS_KEY: existing})
    return {"status": "ok"}


# ── Programmatic launcher ───────────────────────────────────────────
#
# On Windows, the `uvicorn` CLI sets WindowsProactorEventLoopPolicy
# *before* importing this module — too early for the module-level
# WindowsSelectorEventLoopPolicy override above to take effect, and
# psycopg's async pool only works on the selector loop. Run with
# `python -m backend.main` (or `python backend/main.py`) so the policy
# above lands before uvicorn's loop is created; we then pass
# `loop="none"` so uvicorn doesn't reset it.
#
# On POSIX the policy override at the top is a no-op and either launch
# style works.

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        loop="none",
    )
