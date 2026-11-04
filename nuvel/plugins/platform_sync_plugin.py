"""
PlatformSyncPlugin — writes each completed run to Postgres (Neon).

Sits next to TracePlugin and does not replace it: the JSONL files stay the full
record, and ``trace_events`` holds the same event names minus prompts and message
history. A row's ``run_id`` is ADK's invocation id, which ``run_start`` in the
JSONL also carries.

The write happens in a background task after the run, so a slow or unreachable
database never delays a reply; ``close()`` drains what is in flight. Failures
log one warning per outage and later runs try again. ADK re-raises plugin
exceptions into the run, so every callback swallows its own.

Configuration (see ``nuvel.platform_sync``):
  NUVEL_PLATFORM_SYNC: set to "1" to enable (default: off)
  NUVEL_PLATFORM_DATABASE_URL: Postgres connection string

Both are read on the first run: this module is imported before run_adk loads .env.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

from google.genai import types

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool

from nuvel.platform_sync import (
    DEGRADED_MESSAGE,
    PlatformStore,
    RunRow,
    TraceEventRow,
    store_from_env,
)

from .cost_guard_plugin import calculate_cost, _load_pricing
from .trace_plugin import (
    _MAX_ARGS_CHARS,
    _MAX_ERROR_CHARS,
    _MAX_RESPONSE_CHARS,
    _MAX_RESULT_CHARS,
    _extract_function_calls,
    _extract_text,
    _extract_usage,
    _safe_serialize,
    _tool_call_key,
)

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

# Runs whose after_run never fired (a crash mid-run) are dropped oldest-first
# once this many are open, so a long-lived server can't accumulate them.
_MAX_OPEN_RUNS = 1024
# Stay under ADK's 5s plugin-close budget, as SiblingRunner does.
_DRAIN_TIMEOUT = 4.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _never_raises(callback):
    """Telemetry must not fail the run: log a bug here, return "no opinion"."""

    @functools.wraps(callback)
    async def guarded(self, **kwargs):
        try:
            return await callback(self, **kwargs)
        except Exception:  # noqa: BLE001
            logger.debug("[platform_sync] %s failed", callback.__name__, exc_info=True)
            return None

    return guarded


@dataclass
class _Run:
    """What the plugin collects for one invocation (keyed by invocation id)."""

    row: RunRow
    run_start: float
    events: list[TraceEventRow] = field(default_factory=list)
    llm_starts: dict[str, float] = field(default_factory=dict)
    tool_starts: dict[str, float] = field(default_factory=dict)
    total_cost: float = 0.0

    def record(self, event: str, agent: Optional[str], data: dict) -> None:
        self.events.append(TraceEventRow(
            seq=len(self.events), event=event, occurred_at=_now(), agent=agent, data=data,
        ))


class PlatformSyncPlugin(BasePlugin):
    """Writes ``runs`` and ``trace_events`` when a run completes. Off by default."""

    def __init__(self, *, store: Optional[PlatformStore] = None) -> None:
        super().__init__(name="platform_sync")
        self._store = store
        self._resolved = store is not None
        self._pricing = None
        self._runs: dict[str, _Run] = {}
        self._pending: set[asyncio.Task] = set()
        self._warned = False

    def _active_store(self) -> Optional[PlatformStore]:
        if not self._resolved:
            self._resolved = True
            self._store = store_from_env()
        return self._store

    def _run_for(self, context: Any) -> Optional[_Run]:
        return self._runs.get(getattr(context, "invocation_id", None))

    # ── Run lifecycle ────────────────────────────────────────────────

    @_never_raises
    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> Optional[types.Content]:
        if self._active_store() is None:
            return None
        if self._pricing is None:
            self._pricing = _load_pricing()
        agent = invocation_context.agent.name
        session = invocation_context.session
        run = _Run(
            row=RunRow(
                run_id=invocation_context.invocation_id,
                kind="agent",
                agent=agent,
                started_at=_now(),
                session_id=session.id if session else None,
                user_id=invocation_context.user_id,
                app_name=invocation_context.app_name,
            ),
            run_start=time.monotonic(),
        )
        self._runs[invocation_context.invocation_id] = run
        while len(self._runs) > _MAX_OPEN_RUNS:
            self._runs.pop(next(iter(self._runs)))
        run.record("run_start", agent, {
            "user_input": _safe_serialize(_extract_text(invocation_context.user_content)),
        })
        return None

    @_never_raises
    async def after_run_callback(self, *, invocation_context: InvocationContext) -> None:
        run = self._runs.pop(invocation_context.invocation_id, None)
        if run is None:
            return
        row = run.row
        row.duration_ms = round((time.monotonic() - run.run_start) * 1000)
        row.cost_usd = round(run.total_cost, 8) if run.total_cost > 0 else None
        run.record("run_end", row.agent, {
            "duration_ms": row.duration_ms,
            "llm_calls": row.llm_calls,
            "tool_calls": row.tool_calls,
            "total_cost_usd": row.cost_usd,
        })
        task = asyncio.create_task(self._write(row, run.events))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _write(self, row: RunRow, events: list[TraceEventRow]) -> None:
        try:
            await self._store.write_run(row, events)
        except Exception as exc:  # noqa: BLE001 - telemetry never fails a run
            if not self._warned:
                self._warned = True
                logger.warning(DEGRADED_MESSAGE, type(exc).__name__)
            return
        if self._warned:
            self._warned = False
            logger.info("[platform_sync] database writes resumed")

    async def close(self) -> None:
        """Give in-flight writes a bounded chance to finish at shutdown."""
        if self._pending:
            await asyncio.wait(set(self._pending), timeout=_DRAIN_TIMEOUT)

    # ── Model ────────────────────────────────────────────────────────

    @_never_raises
    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> Optional[LlmResponse]:
        run = self._run_for(callback_context)
        if run is None:
            return None
        agent = callback_context.agent_name
        run.row.llm_calls += 1
        run.llm_starts[agent] = time.monotonic()
        run.record("llm_request", agent, {
            "call_index": run.row.llm_calls,
            "model": llm_request.model,
            "message_count": len(llm_request.contents or []),
            "tools_available": list(llm_request.tools_dict or {}),
        })
        return None

    @_never_raises
    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> Optional[LlmResponse]:
        run = self._run_for(callback_context)
        if run is None or llm_response.partial:  # the final chunk carries the totals
            return None
        agent = callback_context.agent_name
        start = run.llm_starts.pop(agent, None)
        usage = _extract_usage(llm_response)
        model = llm_response.model_version or ""
        cost_usd = None
        if usage:
            run.row.prompt_tokens += usage.get("prompt_tokens") or 0
            run.row.completion_tokens += usage.get("completion_tokens") or 0
            cost_usd = calculate_cost(
                model,
                usage.get("prompt_tokens") or 0,
                usage.get("completion_tokens") or 0,
                self._pricing,
            )
            if cost_usd is not None:
                run.total_cost += cost_usd
        if model and not run.row.model:
            run.row.model = model
        run.record("llm_response", agent, {
            "call_index": run.row.llm_calls,
            "model_version": model,
            "latency_ms": round((time.monotonic() - start) * 1000) if start else None,
            "finish_reason": str(llm_response.finish_reason) if llm_response.finish_reason else None,
            "usage": usage,
            "cost_usd": cost_usd,
            "response_text": _safe_serialize(
                _extract_text(llm_response.content), max_len=_MAX_RESPONSE_CHARS
            ),
            "function_calls": _safe_serialize(
                _extract_function_calls(llm_response.content), max_len=_MAX_ARGS_CHARS
            ),
        })
        return None

    @_never_raises
    async def on_model_error_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest, error: Exception
    ) -> Optional[LlmResponse]:
        run = self._run_for(callback_context)
        if run is None:
            return None
        run.row.error_count += 1
        run.record("llm_error", callback_context.agent_name, {
            "call_index": run.row.llm_calls,
            "model": llm_request.model,
            "error_type": type(error).__name__,
            "error_message": str(error)[:_MAX_ERROR_CHARS],
        })
        return None

    # ── Tools ────────────────────────────────────────────────────────

    @_never_raises
    async def before_tool_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext
    ) -> Optional[dict]:
        run = self._run_for(tool_context)
        if run is None:
            return None
        run.tool_starts[_tool_call_key(tool, tool_context)] = time.monotonic()
        run.record("tool_start", tool_context.agent_name, {
            "tool": tool.name,
            "args": _safe_serialize(tool_args, max_len=_MAX_ARGS_CHARS),
        })
        return None

    @_never_raises
    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict,
    ) -> Optional[dict]:
        run = self._run_for(tool_context)
        if run is None:
            return None
        run.row.tool_calls += 1
        start = run.tool_starts.pop(_tool_call_key(tool, tool_context), None)
        is_error = isinstance(result, dict) and result.get("status") == "error"
        if is_error:
            run.row.error_count += 1
        run.record("tool_end", tool_context.agent_name, {
            "tool": tool.name,
            "status": "error" if is_error else "success",
            "duration_ms": round((time.monotonic() - start) * 1000) if start else None,
            "result": _safe_serialize(result, max_len=_MAX_RESULT_CHARS),
        })
        return None

    @_never_raises
    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> Optional[dict]:
        run = self._run_for(tool_context)
        if run is None:
            return None
        run.tool_starts.pop(_tool_call_key(tool, tool_context), None)
        run.row.error_count += 1
        run.record("tool_exception", tool_context.agent_name, {
            "tool": tool.name,
            "error_type": type(error).__name__,
            "error_message": str(error)[:_MAX_ERROR_CHARS],
            "args": _safe_serialize(tool_args, max_len=_MAX_ARGS_CHARS),
        })
        return None
