"""
ConversationTraceWriter — Consolidated conversation traces for evals.

Accumulates turn-level data during an agent run and writes a single
JSON file per conversation to traces/conversations/.

No ADK dependency — receives data via simple method calls from TracePlugin.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MAX_SYSTEM_PROMPT_CHARS = 50_000
_MAX_FIELD_CHARS = 5_000


def _safe_serialize(obj: Any, max_len: int = _MAX_FIELD_CHARS) -> Any:
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


class ConversationTraceWriter:
    """Accumulates conversation data and writes a consolidated JSON trace."""

    def __init__(self, trace_dir: Path) -> None:
        self._trace_dir = trace_dir
        self._reset()

    def _reset(self) -> None:
        self._conversation_id: str = ""
        self._session_id: str = ""
        self._agent: str = ""
        self._model: str = ""
        self._system_prompt: str = ""
        self._skills_loaded: list[str] = []
        self._tools_available: list[str] = []
        self._timestamp_start: str = ""
        self._turns: list[dict] = []
        self._current_turn: Optional[dict] = None
        self._run_start: float = 0

    def start_run(
        self,
        *,
        conversation_id: str,
        session_id: str,
        agent: str,
        model: str,
        system_prompt: str,
        skills_loaded: list[str],
        tools_available: list[str],
    ) -> None:
        self._reset()
        self._conversation_id = conversation_id
        self._session_id = session_id
        self._agent = agent
        self._model = model
        self._system_prompt = system_prompt[:_MAX_SYSTEM_PROMPT_CHARS]
        self._skills_loaded = skills_loaded
        self._tools_available = tools_available
        self._timestamp_start = datetime.now(timezone.utc).isoformat()
        self._run_start = time.monotonic()

    def update_run_metadata(
        self,
        *,
        model: str = "",
        system_prompt: str = "",
        tools_available: Optional[list[str]] = None,
    ) -> None:
        """Update metadata that wasn't available at start_run time."""
        if model:
            self._model = model
        if system_prompt:
            self._system_prompt = system_prompt[:_MAX_SYSTEM_PROMPT_CHARS]
        if tools_available is not None:
            self._tools_available = tools_available

    def add_user_turn(self, user_input: str) -> None:
        # Close any unclosed previous turn
        if self._current_turn is not None:
            self.close_turn()
        self._current_turn = {
            "turn_index": len(self._turns) + 1,
            "user_input": user_input,
            "tool_calls": [],
            "llm_calls": [],
            "turn_start": time.monotonic(),
        }

    def add_tool_call(
        self,
        *,
        tool: str,
        args: Any,
        result: Any,
        status: str,
        duration_ms: Optional[int],
    ) -> None:
        if self._current_turn is None:
            return
        self._current_turn["tool_calls"].append({
            "tool": tool,
            "args": _safe_serialize(args),
            "result": _safe_serialize(result),
            "status": status,
            "duration_ms": duration_ms,
        })

    def add_llm_call(
        self,
        *,
        thinking: Optional[str],
        response: Optional[str],
        function_calls: list[dict],
        usage: Optional[dict],
        latency_ms: Optional[int],
        cost_usd: Optional[float] = None,
    ) -> None:
        if self._current_turn is None:
            return
        self._current_turn["llm_calls"].append({
            "thinking": thinking,
            "response": _safe_serialize(response, max_len=10_000),
            "function_calls": function_calls,
            "usage": usage,
            "latency_ms": latency_ms,
            "cost_usd": cost_usd,
        })

    def close_turn(self) -> None:
        if self._current_turn is None:
            return
        turn_start = self._current_turn.pop("turn_start", None)
        if turn_start is not None:
            self._current_turn["turn_duration_ms"] = round(
                (time.monotonic() - turn_start) * 1000
            )
        else:
            self._current_turn["turn_duration_ms"] = None
        self._turns.append(self._current_turn)
        self._current_turn = None

    def finish_run(self) -> Path:
        # Close any unclosed turn
        if self._current_turn is not None:
            self.close_turn()

        total_llm_calls = sum(len(t["llm_calls"]) for t in self._turns)
        total_tool_calls = sum(len(t["tool_calls"]) for t in self._turns)
        total_tokens = 0
        total_cost = 0.0
        for t in self._turns:
            for lc in t["llm_calls"]:
                usage = lc.get("usage") or {}
                total_tokens += (usage.get("prompt_tokens") or 0) + (
                    usage.get("completion_tokens") or 0
                )
                cost = lc.get("cost_usd")
                if cost is not None:
                    total_cost += cost

        trace = {
            "meta": {
                "conversation_id": self._conversation_id,
                "session_id": self._session_id,
                "timestamp_start": self._timestamp_start,
                "timestamp_end": datetime.now(timezone.utc).isoformat(),
                "model": self._model,
                "agent": self._agent,
            },
            "system_prompt": self._system_prompt,
            "skills_loaded": self._skills_loaded,
            "tools_available": self._tools_available,
            "turns": self._turns,
            "summary": {
                "turn_count": len(self._turns),
                "total_llm_calls": total_llm_calls,
                "total_tool_calls": total_tool_calls,
                "total_tokens": total_tokens,
                "total_cost_usd": round(total_cost, 8) if total_cost > 0 else None,
                "total_duration_ms": round(
                    (time.monotonic() - self._run_start) * 1000
                ),
            },
        }

        out_dir = self._trace_dir / "conversations"
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        safe_id = self._session_id[:16].replace("/", "_")
        path = out_dir / f"{date_str}_{safe_id}.json"

        try:
            path.write_text(
                json.dumps(trace, indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(
                "[ConversationTrace] Wrote %d turns to %s", len(self._turns), path
            )
        except Exception as e:
            logger.error("[ConversationTrace] Failed to write trace: %s", e)

        self._reset()
        return path
