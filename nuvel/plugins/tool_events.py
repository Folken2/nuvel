"""
ToolEventsPlugin — Observability plugin for tool calls.

Captures tool execution details (name, args, results, timing) and surfaces
them as structured events for both terminal logging and real-time SSE streaming.

Also captures base64 images from Composio tool outputs for later injection
into insert_image operations.

Registered at the Runner level so it applies globally to ALL agents and tools.
One instance serves every session on a server, so events and images are kept
per invocation (read them with the invocation id while the run is in flight;
they are dropped when it ends) and SSE queues are registered per session.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from google.adk.agents.invocation_context import InvocationContext
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

# Runs whose after_run never fired are dropped oldest-first past this many.
_MAX_OPEN_RUNS = 1024


@dataclass
class _ToolRun:
    """Tool events and captured images for one invocation."""

    session_id: str
    events: list[dict] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    tool_starts: dict[str, float] = field(default_factory=dict)


class ToolEventsPlugin(BasePlugin):
    """Streams tool start/end events via SSE and captures images from outputs."""

    def __init__(self) -> None:
        super().__init__(name="tool_events")
        self._runs: dict[str, _ToolRun] = {}
        self._queues: dict[str, asyncio.Queue] = {}

    # ── Public API (called by the runner) ───────────────────────────

    def set_event_queue(self, session_id: str, queue: asyncio.Queue) -> None:
        """Stream this session's tool events into ``queue`` (for SSE)."""
        self._queues[session_id] = queue

    def clear(self, session_id: str) -> None:
        """Stop streaming this session's tool events."""
        self._queues.pop(session_id, None)

    def get_tool_events(self, invocation_id: str) -> list[dict]:
        run = self._runs.get(invocation_id)
        return list(run.events) if run else []

    def get_captured_images(self, invocation_id: str) -> list[str]:
        run = self._runs.get(invocation_id)
        return list(run.images) if run else []

    # ── Plugin callbacks ────────────────────────────────────────────

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> None:
        self._runs.pop(invocation_context.invocation_id, None)
        self._open_run(invocation_context.invocation_id, invocation_context.session)
        return None

    async def after_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> None:
        self._runs.pop(invocation_context.invocation_id, None)
        return None

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Optional[dict]:
        """Log tool start and emit SSE event. Never blocks (always returns None)."""
        run = self._run_for(tool_context)
        run.tool_starts[_call_key(tool, tool_context)] = time.time()

        self._emit(run, {
            "type": "tool_start",
            "tool": tool.name,
            "status": "running",
            "args": _safe_args(tool_args),
        })

        return None  # never short-circuit

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict,
    ) -> Optional[dict]:
        """Log tool end, capture images, emit SSE event. Returns None (observe only)."""
        tool_name = tool.name
        end = time.time()
        run = self._run_for(tool_context)

        start = run.tool_starts.pop(_call_key(tool, tool_context), end)
        duration_ms = round((end - start) * 1000)

        is_error = isinstance(result, dict) and result.get("status") == "error"
        status = "error" if is_error else "success"

        # Capture base64 images before generating the truncated preview
        if not is_error and isinstance(result, dict):
            self._capture_images(run, result)

        result_preview = _preview(result)

        # Debug logging for Composio meta-tools and errors (stays in log file)
        if tool_name.startswith("COMPOSIO_SEARCH") or is_error:
            full_response = json.dumps(result, default=str)
            logger.debug(
                "TOOL_DETAIL | %s | %s",
                tool_name,
                full_response[:3000] + ("..." if len(full_response) > 3000 else ""),
            )

        self._emit(run, {
            "type": "tool_end",
            "tool": tool_name,
            "status": status,
            "duration_ms": duration_ms,
            "result_preview": result_preview,
        })

        return None  # never short-circuit — let other plugins see original result

    # ── Internal helpers ────────────────────────────────────────────

    def _open_run(self, invocation_id: Optional[str], session: Any) -> _ToolRun:
        run = self._runs[invocation_id] = _ToolRun(
            session_id=getattr(session, "id", None) or "unknown"
        )
        while len(self._runs) > _MAX_OPEN_RUNS:
            self._runs.pop(next(iter(self._runs)))
        return run

    def _run_for(self, tool_context: ToolContext) -> _ToolRun:
        invocation_id = getattr(tool_context, "invocation_id", None)
        run = self._runs.get(invocation_id)
        if run is None:
            run = self._open_run(invocation_id, getattr(tool_context, "session", None))
        return run

    def _emit(self, run: _ToolRun, event: dict) -> None:
        """Append event to the run and push it to the session's SSE queue."""
        run.events.append(event)
        queue = self._queues.get(run.session_id)
        if queue is not None:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Tool event queue full, dropping event: %s", event.get("tool"))

    def _capture_images(self, run: _ToolRun, tool_response: dict) -> None:
        """Scan a tool response for base64-encoded images and store them."""
        raw_text = _extract_text_from_response(tool_response)
        if not raw_text:
            return
        for text in raw_text:
            self._try_capture_b64_from_text(run, text)

    def _try_capture_b64_from_text(self, run: _ToolRun, text: str) -> None:
        """Try to parse JSON from text and capture image_base64 values."""
        if "image_base64" not in text and not any(p in text for p in _B64_IMAGE_PREFIXES):
            return

        for candidate in _json_candidates(text):
            b64 = candidate.get("image_base64")
            if isinstance(b64, str) and len(b64) >= _MIN_B64_IMAGE_LEN:
                if b64.startswith(tuple(_B64_IMAGE_PREFIXES)):
                    run.images.append(b64)
                    logger.info(
                        "Captured base64 image #%d (%d chars)",
                        len(run.images) - 1, len(b64),
                    )
                    return

            # Composio nests: data.results -> stdout -> JSON
            stdout = candidate.get("stdout") or candidate.get("data", {}).get("stdout", "")
            if isinstance(stdout, str) and "image_base64" in stdout:
                self._try_capture_b64_from_text(run, stdout)

            # Nested results array
            results = candidate.get("data", {}).get("results")
            if isinstance(results, list):
                for r in results:
                    if isinstance(r, dict):
                        inner_stdout = (
                            r.get("stdout", "")
                            or r.get("response", {}).get("data_preview", {}).get("stdout", "")
                        )
                        if isinstance(inner_stdout, str) and "image_base64" in inner_stdout:
                            self._try_capture_b64_from_text(run, inner_stdout)


# ── Module-level helpers (stateless, shared) ────────────────────────


def _call_key(tool: BaseTool, tool_context: ToolContext) -> str:
    """Identifies one tool call, so parallel calls of a tool time separately."""
    return getattr(tool_context, "function_call_id", None) or tool.name


def _safe_args(args: dict, max_len: int = 120) -> dict:
    """Return a copy of args with large values truncated for logging."""
    safe = {}
    for k, v in args.items():
        s = str(v)
        safe[k] = s if len(s) <= max_len else s[:max_len] + "\u2026"
    return safe


def _preview(response: dict, max_len: int = 200) -> str:
    """Return a short preview string from a tool response."""
    if isinstance(response, dict):
        msg = response.get("message") or response.get("result") or response.get("error")
        if msg:
            s = str(msg)
            return s if len(s) <= max_len else s[:max_len] + "\u2026"
    s = str(response)
    return s if len(s) <= max_len else s[:max_len] + "\u2026"


# Minimum length to consider a string a valid base64 image
_MIN_B64_IMAGE_LEN = 1_000
# PNG header in base64 is "iVBORw0KGgo", JPEG is "/9j/"
_B64_IMAGE_PREFIXES = ("iVBORw0KGgo", "/9j/")


def _extract_text_from_response(resp: dict) -> list[str]:
    """Pull all text strings out of a Composio-style tool response."""
    texts: list[str] = []

    # MCP-style: {"content": [{"type": "text", "text": "..."}]}
    content = resp.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))

    # Direct stdout
    stdout = resp.get("stdout") or resp.get("data", {}).get("stdout")
    if isinstance(stdout, str):
        texts.append(stdout)

    return texts


def _json_candidates(text: str) -> list[dict]:
    """Try to parse text as JSON; return list of parsed dicts (may be empty)."""
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return [obj]
    except (json.JSONDecodeError, ValueError):
        pass
    return []
