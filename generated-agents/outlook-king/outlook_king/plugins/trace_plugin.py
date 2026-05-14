"""
TracePlugin — Full observability traces for prompt optimization and evals.

Captures every agent lifecycle event as structured records:
- User messages
- LLM requests (model, system prompt size, message count, tools available)
- LLM responses (content, token usage, finish reason, latency)
- LLM errors
- Tool calls (name, args, result, duration, success/failure)
- Tool errors (exceptions)
- Agent start/end
- Run start/end (with aggregate stats)

Storage backends (concurrent, non-blocking):
  - JSONL files (always, for local dev and backup)
  - PostgreSQL table (when TRACE_DB=true and SESSION_SERVICE_URI is set)

Output format: one JSON object per line, compatible with eval frameworks
(OpenAI Evals, Braintrust, LangSmith, custom pipelines).

Configuration:
  TRACE_ENABLED: set to "false" to disable all tracing (default: true)
  TRACE_DIR: directory for JSONL files (default: ./traces)
  TRACE_DB: set to "true" to also write to PostgreSQL (default: false)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from google.genai import types

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool

from .conversation_trace_writer import ConversationTraceWriter
from .cost_guard_plugin import calculate_cost, _load_pricing

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.tools.tool_context import ToolContext
    from google.adk.events.event import Event

logger = logging.getLogger(__name__)

_TRACE_DIR = Path(os.getenv("TRACE_DIR", "./traces"))
_TRACE_ENABLED = os.getenv("TRACE_ENABLED", "true").lower() not in ("false", "0", "no")
_TRACE_DB = os.getenv("TRACE_DB", "false").lower() in ("true", "1", "yes")


# ── Helpers ──────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: str) -> datetime:
    """Parse an ISO timestamp string back to a datetime for asyncpg."""
    return datetime.fromisoformat(value)


def _extract_text(content: Optional[types.Content]) -> Optional[str]:
    """Extract concatenated text from a Content object (excludes thinking parts)."""
    if not content or not content.parts:
        return None
    texts = [p.text for p in content.parts if p.text and not p.thought]
    return "\n".join(texts) if texts else None


def _extract_thinking(content: Optional[types.Content]) -> Optional[str]:
    """Extract thinking/reasoning text from a Content object.

    Returns text from parts marked as thought (part.thought == True).
    Returns None if no thinking parts found.
    """
    if not content or not content.parts:
        return None
    thoughts = [p.text for p in content.parts if p.thought and p.text]
    return "\n".join(thoughts) if thoughts else None


def _extract_system_instruction(llm_request: LlmRequest) -> Optional[str]:
    """Extract full system instruction text from an LLM request."""
    if not llm_request.config or not llm_request.config.system_instruction:
        return None
    si = llm_request.config.system_instruction
    if isinstance(si, str):
        return si[:50_000]
    if hasattr(si, "parts") and si.parts:
        text = "\n".join(p.text or "" for p in si.parts)
        return text[:50_000] if text else None
    return None


def _extract_messages(contents: Optional[list[types.Content]]) -> list[dict]:
    """Extract message history as a list of {role, content_preview} dicts."""
    if not contents:
        return []
    messages = []
    for content in contents:
        role = content.role or "user"
        text = _extract_text(content)
        fn_calls = _extract_function_calls(content)
        preview = text[:500] if text else None
        messages.append({
            "role": role,
            "content_preview": preview,
            "has_function_calls": len(fn_calls) > 0,
        })
    return messages


def _extract_function_calls(content: Optional[types.Content]) -> list[dict]:
    """Extract function call info from a Content object."""
    if not content or not content.parts:
        return []
    calls = []
    for p in content.parts:
        if p.function_call:
            calls.append({
                "name": p.function_call.name,
                "args": dict(p.function_call.args) if p.function_call.args else {},
            })
    return calls


def _extract_usage(resp: LlmResponse) -> Optional[dict]:
    """Extract token usage from LlmResponse."""
    um = resp.usage_metadata
    if not um:
        return None
    return {
        "prompt_tokens": um.prompt_token_count,
        "completion_tokens": um.candidates_token_count,
        "total_tokens": um.total_token_count,
        "thoughts_tokens": um.thoughts_token_count,
        "cached_tokens": um.cached_content_token_count,
    }


def _safe_serialize(obj: Any, max_len: int = 5000) -> Any:
    """Make an object JSON-serializable, truncating large values."""
    if obj is None:
        return None
    if isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return obj[:max_len] + "...[truncated]" if len(obj) > max_len else obj
    if isinstance(obj, dict):
        return {k: _safe_serialize(v, max_len) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v, max_len) for v in obj]
    return str(obj)[:max_len]


# ── JSONL file writer ────────────────────────────────────────────────


class _FileWriter:
    """Async JSONL file writer. Non-blocking via background task."""

    def __init__(self, trace_dir: Path):
        self._trace_dir = trace_dir
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=10_000)
        self._task: Optional[asyncio.Task] = None

    def write(self, record: dict) -> None:
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            logger.warning("Trace file queue full, dropping record")

        if self._task is None or self._task.done():
            try:
                loop = asyncio.get_running_loop()
                self._task = loop.create_task(self._flush_loop())
            except RuntimeError:
                pass

    def _get_file_path(self, session_id: str) -> Path:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        safe_id = session_id[:16].replace("/", "_")
        return self._trace_dir / f"{date_str}_{safe_id}.jsonl"

    async def _flush_loop(self) -> None:
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                record = await asyncio.wait_for(self._queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                if self._queue.empty():
                    return
                continue
            try:
                file_path = self._get_file_path(record.get("session_id", "unknown"))
                line = json.dumps(record, default=str, ensure_ascii=False)
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception as e:
                logger.error("Failed to write trace to file: %s", e)

    async def flush(self) -> None:
        while not self._queue.empty():
            try:
                record = self._queue.get_nowait()
                file_path = self._get_file_path(record.get("session_id", "unknown"))
                line = json.dumps(record, default=str, ensure_ascii=False)
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                break


# ── PostgreSQL writer ────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_traces (
    id              BIGSERIAL PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    event           TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    data            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_traces_session_id ON agent_traces (session_id);
CREATE INDEX IF NOT EXISTS idx_traces_trace_id ON agent_traces (trace_id);
CREATE INDEX IF NOT EXISTS idx_traces_event ON agent_traces (event);
CREATE INDEX IF NOT EXISTS idx_traces_timestamp ON agent_traces (timestamp DESC);
"""

_INSERT_SQL = """
INSERT INTO agent_traces (trace_id, session_id, event, timestamp, data)
VALUES ($1, $2, $3, $4, $5::jsonb)
"""


_TRACE_DB_URI = os.getenv("SESSION_SERVICE_URI", "")


class _DbWriter:
    """Async PostgreSQL trace writer. Connects to Neon (SESSION_SERVICE_URI).

    Auto-creates the agent_traces table on first write.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=10_000)
        self._task: Optional[asyncio.Task] = None
        self._table_ready = False
        self._pool = None

    def write(self, record: dict) -> None:
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            logger.warning("Trace DB queue full, dropping record")

        if self._task is None or self._task.done():
            try:
                loop = asyncio.get_running_loop()
                self._task = loop.create_task(self._flush_loop())
            except RuntimeError:
                pass

    async def _get_pool(self):
        """Get or create an asyncpg pool for the trace database (Neon)."""
        if self._pool is not None:
            return self._pool
        import asyncpg

        dsn = _TRACE_DB_URI
        if not dsn:
            raise RuntimeError("SESSION_SERVICE_URI not set — cannot write traces to DB")

        self._pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=1,
            max_size=3,
            command_timeout=15,
        )
        logger.info("[TracePlugin] Created trace DB pool (Neon)")
        return self._pool

    async def _ensure_table(self, pool) -> None:
        """Create traces table and indexes if they don't exist."""
        if self._table_ready:
            return
        try:
            async with pool.acquire() as conn:
                await conn.execute(_CREATE_TABLE_SQL)
                await conn.execute(_CREATE_INDEXES_SQL)
            self._table_ready = True
            logger.info("[TracePlugin] agent_traces table ready (Neon)")
        except Exception as e:
            logger.error("[TracePlugin] Failed to create traces table: %s", e)

    async def _flush_loop(self) -> None:
        """Drain queue and batch-insert to Postgres (Neon)."""
        try:
            pool = await self._get_pool()
        except Exception as e:
            logger.error("[TracePlugin] Cannot get DB pool for traces: %s", e)
            # Drain queue so we don't accumulate forever
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            return

        await self._ensure_table(pool)

        while True:
            # Collect a batch (up to 50 records or 2s timeout)
            batch: list[dict] = []
            try:
                record = await asyncio.wait_for(self._queue.get(), timeout=2.0)
                batch.append(record)
            except asyncio.TimeoutError:
                if self._queue.empty():
                    return
                continue

            # Drain remaining available records (non-blocking)
            while len(batch) < 50:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            # Batch insert
            try:
                async with pool.acquire() as conn:
                    await conn.executemany(
                        _INSERT_SQL,
                        [
                            (
                                r.get("trace_id", ""),
                                r.get("session_id", ""),
                                r.get("event", ""),
                                _parse_ts(r.get("timestamp", _now_iso())),
                                json.dumps(
                                    {k: v for k, v in r.items()
                                     if k not in ("trace_id", "session_id", "event", "timestamp")},
                                    default=str,
                                    ensure_ascii=False,
                                ),
                            )
                            for r in batch
                        ],
                    )
            except Exception as e:
                logger.error("[TracePlugin] Failed to write %d traces to DB: %s", len(batch), e)

    async def flush(self) -> None:
        """Flush remaining records to DB."""
        if self._queue.empty():
            return
        try:
            pool = await self._get_pool()
            await self._ensure_table(pool)
            async with pool.acquire() as conn:
                while not self._queue.empty():
                    try:
                        r = self._queue.get_nowait()
                        await conn.execute(
                            _INSERT_SQL,
                            r.get("trace_id", ""),
                            r.get("session_id", ""),
                            r.get("event", ""),
                            _parse_ts(r.get("timestamp", _now_iso())),
                            json.dumps(
                                {k: v for k, v in r.items()
                                 if k not in ("trace_id", "session_id", "event", "timestamp")},
                                default=str,
                                ensure_ascii=False,
                            ),
                        )
                    except asyncio.QueueEmpty:
                        break
        except Exception as e:
            logger.error("[TracePlugin] Failed to flush traces to DB: %s", e)


# ── Plugin ───────────────────────────────────────────────────────────


class TracePlugin(BasePlugin):
    """Records full agent traces for evals and prompt optimization.

    Writes to JSONL files (always) and PostgreSQL (when TRACE_DB=true).
    """

    def __init__(self) -> None:
        super().__init__(name="trace")
        self._file_writer = _FileWriter(_TRACE_DIR)
        self._db_writer = _DbWriter() if _TRACE_DB else None
        self._conversation_writer = ConversationTraceWriter(trace_dir=_TRACE_DIR)
        self._run_id: str = ""
        self._session_id: str = ""
        self._run_start: float = 0
        self._llm_starts: dict[str, float] = {}
        self._llm_call_index: int = 0
        self._tool_count: int = 0
        self._llm_count: int = 0
        self._total_prompt_tokens: int = 0
        self._total_completion_tokens: int = 0
        self._first_system_prompt: str = ""
        self._first_tools_available: list[str] = []
        self._pricing = _load_pricing()
        self._total_cost: float = 0.0

        if _TRACE_DB:
            logger.info("[TracePlugin] DB tracing enabled (TRACE_DB=true)")
        logger.info("[TracePlugin] File tracing to %s", _TRACE_DIR)

    def _record(self, event_type: str, data: dict) -> None:
        """Build and dispatch a trace record to all writers."""
        if not _TRACE_ENABLED:
            return
        record = {
            "trace_id": self._run_id,
            "session_id": self._session_id,
            "event": event_type,
            "timestamp": _now_iso(),
            **data,
        }
        self._file_writer.write(record)
        if self._db_writer is not None:
            self._db_writer.write(record)

    # ── User message ─────────────────────────────────────────────────

    async def on_user_message_callback(
        self, *, invocation_context: InvocationContext, user_message: types.Content
    ) -> Optional[types.Content]:
        self._record("user_message", {
            "content": _extract_text(user_message),
        })
        return None

    # ── Run lifecycle ────────────────────────────────────────────────

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> Optional[types.Content]:
        self._run_id = uuid.uuid4().hex[:16]
        self._session_id = (
            invocation_context.session.id if invocation_context.session else "unknown"
        )
        self._run_start = time.monotonic()
        self._llm_call_index = 0
        self._tool_count = 0
        self._llm_count = 0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0

        self._record("run_start", {
            "invocation_id": invocation_context.invocation_id,
            "agent": invocation_context.agent.name,
            "user_input": _extract_text(invocation_context.user_content),
        })

        # Detect skills loaded from the agent's tools
        skills_loaded = []
        if hasattr(invocation_context.agent, "tools") and invocation_context.agent.tools:
            for tool in invocation_context.agent.tools:
                if hasattr(tool, "skills"):
                    skills_loaded = [s.name for s in tool.skills]
                    break

        self._conversation_writer.start_run(
            conversation_id=self._run_id,
            session_id=self._session_id,
            agent=invocation_context.agent.name,
            model="",  # captured on first LLM call
            system_prompt="",  # captured on first LLM call
            skills_loaded=skills_loaded,
            tools_available=[],  # captured on first LLM call
        )

        # Add user turn AFTER start_run (on_user_message fires before
        # before_run, so we'd lose the turn if we added it there)
        user_input = _extract_text(invocation_context.user_content)
        if user_input:
            self._conversation_writer.add_user_turn(user_input)

        return None

    async def after_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> None:
        elapsed_ms = round((time.monotonic() - self._run_start) * 1000)
        self._record("run_end", {
            "duration_ms": elapsed_ms,
            "llm_calls": self._llm_count,
            "tool_calls": self._tool_count,
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "total_tokens": self._total_prompt_tokens + self._total_completion_tokens,
            "total_cost_usd": round(self._total_cost, 8) if self._total_cost > 0 else None,
        })
        if _TRACE_ENABLED:
            self._conversation_writer.finish_run()

    # ── Agent lifecycle ──────────────────────────────────────────────

    async def before_agent_callback(
        self, *, agent: BaseAgent, callback_context: CallbackContext
    ) -> Optional[types.Content]:
        self._record("agent_start", {"agent": agent.name})
        return None

    async def after_agent_callback(
        self, *, agent: BaseAgent, callback_context: CallbackContext
    ) -> Optional[types.Content]:
        self._record("agent_end", {"agent": agent.name})
        return None

    # ── LLM lifecycle ────────────────────────────────────────────────

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> Optional[LlmResponse]:
        self._llm_count += 1
        self._llm_call_index += 1
        agent_name = (
            callback_context.agent_name
            if hasattr(callback_context, "agent_name")
            else "unknown"
        )
        self._llm_starts[agent_name] = time.monotonic()

        tools_available = list(llm_request.tools_dict.keys()) if llm_request.tools_dict else []
        system_instruction = _extract_system_instruction(llm_request)
        messages = _extract_messages(llm_request.contents)

        # Capture system prompt on first LLM call for the conversation writer
        if self._llm_call_index == 1 and system_instruction:
            self._first_system_prompt = system_instruction
            self._first_tools_available = tools_available

        self._record("llm_request", {
            "call_index": self._llm_call_index,
            "model": llm_request.model,
            "message_count": len(messages),
            "messages": messages,
            "tools_available": tools_available,
            "system_instruction": _safe_serialize(system_instruction, max_len=50_000),
            "system_instruction_chars": len(system_instruction) if system_instruction else 0,
        })
        return None

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> Optional[LlmResponse]:
        agent_name = (
            callback_context.agent_name
            if hasattr(callback_context, "agent_name")
            else "unknown"
        )
        start = self._llm_starts.pop(agent_name, None)
        latency_ms = round((time.monotonic() - start) * 1000) if start else None

        usage = _extract_usage(llm_response)
        if usage:
            self._total_prompt_tokens += usage.get("prompt_tokens") or 0
            self._total_completion_tokens += usage.get("completion_tokens") or 0

        # Calculate cost
        model_id = llm_response.model_version or ""
        cost_usd = None
        if usage and self._pricing:
            cost_usd = calculate_cost(
                model_id,
                usage.get("prompt_tokens") or 0,
                usage.get("completion_tokens") or 0,
                self._pricing,
            )
            if cost_usd is not None:
                self._total_cost += cost_usd

        text = _extract_text(llm_response.content)
        thinking = _extract_thinking(llm_response.content)
        function_calls = _extract_function_calls(llm_response.content)

        self._record("llm_response", {
            "call_index": self._llm_call_index,
            "model_version": model_id,
            "latency_ms": latency_ms,
            "turn_complete": llm_response.turn_complete,
            "finish_reason": str(llm_response.finish_reason) if llm_response.finish_reason else None,
            "usage": usage,
            "cost_usd": cost_usd,
            "thinking": _safe_serialize(thinking, max_len=10_000),
            "response_text": _safe_serialize(text, max_len=10_000),
            "function_calls": function_calls,
        })

        # Update model/system_prompt on first LLM call
        if self._llm_call_index == 1:
            self._conversation_writer.update_run_metadata(
                model=llm_response.model_version or "",
                system_prompt=self._first_system_prompt,
                tools_available=self._first_tools_available,
            )

        self._conversation_writer.add_llm_call(
            thinking=thinking,
            response=text,
            function_calls=function_calls,
            usage=usage,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
        )
        return None

    async def on_model_error_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
        error: Exception,
    ) -> Optional[LlmResponse]:
        self._record("llm_error", {
            "call_index": self._llm_call_index,
            "model": llm_request.model,
            "error_type": type(error).__name__,
            "error_message": str(error)[:2000],
        })
        return None

    # ── Tool lifecycle ───────────────────────────────────────────────

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Optional[dict]:
        tool_context.state[f"_trace_start_{tool.name}"] = time.monotonic()
        self._record("tool_start", {
            "tool": tool.name,
            "args": _safe_serialize(tool_args, max_len=2000),
        })
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict,
    ) -> Optional[dict]:
        self._tool_count += 1
        start = tool_context.state.get(f"_trace_start_{tool.name}")
        duration_ms = round((time.monotonic() - start) * 1000) if start else None

        is_error = isinstance(result, dict) and result.get("status") == "error"

        self._record("tool_end", {
            "tool": tool.name,
            "status": "error" if is_error else "success",
            "duration_ms": duration_ms,
            "result": _safe_serialize(result, max_len=5000),
        })
        self._conversation_writer.add_tool_call(
            tool=tool.name,
            args=tool_args,
            result=result,
            status="error" if is_error else "success",
            duration_ms=duration_ms,
        )
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> Optional[dict]:
        self._record("tool_exception", {
            "tool": tool.name,
            "error_type": type(error).__name__,
            "error_message": str(error)[:2000],
            "args": _safe_serialize(tool_args, max_len=2000),
        })
        return None

    # ── Event stream ─────────────────────────────────────────────────

    async def on_event_callback(
        self, *, invocation_context: InvocationContext, event: Event
    ) -> Optional[Event]:
        if event.content and event.content.parts:
            self._record("event", {
                "event_id": event.id,
                "author": event.author,
                "invocation_id": event.invocation_id,
                "turn_complete": event.turn_complete,
                "has_text": any(p.text for p in event.content.parts),
                "has_function_call": any(p.function_call for p in event.content.parts),
                "has_function_response": any(p.function_response for p in event.content.parts),
            })
        return None
