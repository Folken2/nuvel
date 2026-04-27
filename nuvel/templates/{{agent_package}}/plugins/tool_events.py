"""
ToolEventsPlugin — Observability plugin for the Excel AI Agent.

Captures tool execution details (name, args, results, timing) and surfaces
them as structured events for both terminal logging and real-time SSE streaming.

Also captures base64 images from Composio tool outputs for later injection
into insert_image operations.

Registered at the Runner level so it applies globally to ALL agents and tools.
"""

import asyncio
import json
import logging
import time
from typing import Any, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)


class ToolEventsPlugin(BasePlugin):
    """Streams tool start/end events via SSE and captures images from outputs."""

    def __init__(self) -> None:
        super().__init__(name="tool_events")
        self._event_queue: asyncio.Queue | None = None
        self._tool_events: list[dict] = []
        self._captured_images: list[str] = []

    # ── Public API (called by the runner) ───────────────────────────

    def set_event_queue(self, queue: asyncio.Queue) -> None:
        """Inject the asyncio queue for real-time SSE streaming."""
        self._event_queue = queue

    def clear(self) -> None:
        """Reset state before each agent run."""
        self._tool_events.clear()
        self._captured_images.clear()
        self._event_queue = None

    def get_tool_events(self) -> list[dict]:
        return list(self._tool_events)

    def get_captured_images(self) -> list[str]:
        return list(self._captured_images)

    # ── Plugin callbacks ────────────────────────────────────────────

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Optional[dict]:
        """Log tool start and emit SSE event. Never blocks (always returns None)."""
        tool_name = tool.name
        start = time.time()

        tool_context.state[f"_tool_start_{tool_name}"] = start

        self._emit({
            "type": "tool_start",
            "tool": tool_name,
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

        start = tool_context.state.get(f"_tool_start_{tool_name}", end)
        duration_ms = round((end - start) * 1000)

        is_error = isinstance(result, dict) and result.get("status") == "error"
        status = "error" if is_error else "success"

        # Capture base64 images before generating the truncated preview
        if not is_error and isinstance(result, dict):
            self._capture_images(result)

        result_preview = _preview(result)

        # Debug logging for Composio meta-tools and errors (stays in log file)
        if tool_name.startswith("COMPOSIO_SEARCH") or is_error:
            full_response = json.dumps(result, default=str)
            logger.debug(
                "TOOL_DETAIL | %s | %s",
                tool_name,
                full_response[:3000] + ("..." if len(full_response) > 3000 else ""),
            )

        self._emit({
            "type": "tool_end",
            "tool": tool_name,
            "status": status,
            "duration_ms": duration_ms,
            "result_preview": result_preview,
        })

        return None  # never short-circuit — let other plugins see original result

    # ── Internal helpers ────────────────────────────────────────────

    def _emit(self, event: dict) -> None:
        """Push event to SSE queue and append to collection list."""
        self._tool_events.append(event)
        if self._event_queue is not None:
            try:
                self._event_queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Tool event queue full, dropping event: %s", event.get("tool"))

    def _capture_images(self, tool_response: dict) -> None:
        """Scan a tool response for base64-encoded images and store them."""
        raw_text = _extract_text_from_response(tool_response)
        if not raw_text:
            return
        for text in raw_text:
            self._try_capture_b64_from_text(text)

    def _try_capture_b64_from_text(self, text: str) -> None:
        """Try to parse JSON from text and capture image_base64 values."""
        if "image_base64" not in text and not any(p in text for p in _B64_IMAGE_PREFIXES):
            return

        for candidate in _json_candidates(text):
            b64 = candidate.get("image_base64")
            if isinstance(b64, str) and len(b64) >= _MIN_B64_IMAGE_LEN:
                if b64.startswith(tuple(_B64_IMAGE_PREFIXES)):
                    self._captured_images.append(b64)
                    logger.info(
                        "Captured base64 image #%d (%d chars)",
                        len(self._captured_images) - 1, len(b64),
                    )
                    return

            # Composio nests: data.results -> stdout -> JSON
            stdout = candidate.get("stdout") or candidate.get("data", {}).get("stdout", "")
            if isinstance(stdout, str) and "image_base64" in stdout:
                self._try_capture_b64_from_text(stdout)

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
                            self._try_capture_b64_from_text(inner_stdout)


# ── Module-level helpers (stateless, shared) ────────────────────────


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
