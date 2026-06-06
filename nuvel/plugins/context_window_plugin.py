"""
ContextWindowPlugin — Live context-window usage tracking.

Knows the total context window of the selected model and counts how many
tokens are occupied after every LLM call, so a frontend can show a live
"context used" indicator (like the one Claude Code displays).

The plugin is read-only: it never mutates the request and never blocks. It
only publishes a ``context_window`` snapshot into session state after each
model response so SSE clients can render it.

Configuration:
  CONTEXT_WINDOW_CONFIG: path to context_windows.json (default: auto-detected)
  CONTEXT_WINDOW_DEFAULT: fallback window size for unknown models
                          (default: 0 = unknown -> percentages omitted)
  CONTEXT_WINDOW_WARN_PCT: log a warning once usage crosses this percent
                           (default: 0 = disabled)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from google.genai import types

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext

logger = logging.getLogger(__name__)

_DEFAULT_WINDOWS_PATH = Path(__file__).parent / "context_windows.json"


def _load_windows(path: Optional[str] = None) -> dict[str, int]:
    """Load model -> max-context-token mappings from a JSON file."""
    windows_path = Path(path) if path else _DEFAULT_WINDOWS_PATH
    if not windows_path.is_file():
        logger.warning("[ContextWindow] Config file not found: %s", windows_path)
        return {}
    try:
        data = json.loads(windows_path.read_text(encoding="utf-8"))
        # Strip comment keys and coerce values to int.
        return {
            k: int(v)
            for k, v in data.items()
            if not k.startswith("_") and isinstance(v, (int, float))
        }
    except Exception as e:  # noqa: BLE001 - config errors must not crash the agent
        logger.error("[ContextWindow] Failed to load config: %s", e)
        return {}


def _find_window(model: str, windows: dict[str, int]) -> Optional[int]:
    """Find the context window for a model, trying exact then prefix match.

    Handles model IDs like 'openrouter/anthropic/claude-sonnet-4' by stripping
    the provider prefix and trying progressively shorter matches — mirrors the
    matching used by CostGuardPlugin so both configs share the same keys.
    """
    if not model:
        return None

    if model in windows:
        return windows[model]

    parts = model.split("/")
    for i in range(len(parts)):
        candidate = "/".join(parts[i:])
        if candidate in windows:
            return windows[candidate]

    return None


def compute_context_usage(
    model: str,
    used_tokens: int,
    windows: dict[str, int],
    default_max: int = 0,
) -> dict[str, object]:
    """Build a context-window usage snapshot for a single point in time.

    ``used_tokens`` is how many tokens are currently occupied in the window
    (typically the latest call's total token count: prompt history + output).

    Returns a plain dict, always including ``model``, ``used_tokens`` and
    ``max_tokens`` (None when unknown). When ``max_tokens`` is known the dict
    also includes ``remaining_tokens``, ``used_pct`` and ``remaining_pct``.
    """
    max_tokens = _find_window(model, windows)
    if max_tokens is None and default_max > 0:
        max_tokens = default_max

    snapshot: dict[str, object] = {
        "model": model,
        "used_tokens": used_tokens,
        "max_tokens": max_tokens,
    }

    if max_tokens and max_tokens > 0:
        remaining = max(max_tokens - used_tokens, 0)
        used_pct = round(used_tokens / max_tokens * 100, 2)
        snapshot.update(
            {
                "remaining_tokens": remaining,
                "used_pct": used_pct,
                "remaining_pct": round(100.0 - used_pct, 2),
            }
        )

    return snapshot


class ContextWindowPlugin(BasePlugin):
    """Tracks how full the model's context window is after each LLM call.

    Usage data is propagated to clients via ADK's session state mechanism.
    After each LLM call, the plugin writes a ``context_window`` key to
    ``callback_context.state`` with the structure::

        {
            "model": "anthropic/claude-sonnet-4",
            "used_tokens": 12345,        # prompt history + this turn's output
            "prompt_tokens": 12000,
            "completion_tokens": 345,
            "max_tokens": 200000,        # None when the model is unknown
            "remaining_tokens": 187655,  # omitted when max is unknown
            "used_pct": 6.17,            # omitted when max is unknown
            "remaining_pct": 93.83,      # omitted when max is unknown
        }

    Custom UIs consuming the SSE event stream can read ``state.context_window``
    to render a live "context used" indicator on every agent response.
    """

    def __init__(self) -> None:
        super().__init__(name="context_window")
        self._windows = _load_windows(os.getenv("CONTEXT_WINDOW_CONFIG"))
        self._default_max = int(os.getenv("CONTEXT_WINDOW_DEFAULT", "0"))
        self._warn_pct = float(os.getenv("CONTEXT_WINDOW_WARN_PCT", "0"))
        self._model: str = ""
        self._warned: bool = False

        if self._windows:
            logger.info(
                "[ContextWindow] Loaded windows for %d models%s",
                len(self._windows),
                f", default={self._default_max}" if self._default_max else "",
            )
        else:
            logger.warning(
                "[ContextWindow] No window config loaded — usage percentages "
                "will be omitted for all models"
            )

    @staticmethod
    def _used_tokens(usage: types.GenerateContentResponseUsageMetadata) -> int:
        """Best-effort count of tokens occupying the window right now.

        Prefer ``total_token_count`` (includes thinking tokens) and fall back
        to prompt + candidates when the provider doesn't report a total.
        """
        total = usage.total_token_count
        if total:
            return total
        prompt = usage.prompt_token_count or 0
        completion = usage.candidates_token_count or 0
        return prompt + completion

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> Optional[types.Content]:
        self._model = ""
        return None

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> Optional[LlmResponse]:
        self._model = llm_request.model or ""
        return None

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> Optional[LlmResponse]:
        usage = llm_response.usage_metadata
        if not usage:
            return None

        used_tokens = self._used_tokens(usage)
        snapshot = compute_context_usage(
            self._model, used_tokens, self._windows, self._default_max
        )
        snapshot["prompt_tokens"] = usage.prompt_token_count or 0
        snapshot["completion_tokens"] = usage.candidates_token_count or 0

        if hasattr(callback_context, "state"):
            callback_context.state["context_window"] = snapshot

        used_pct = snapshot.get("used_pct")
        logger.info(
            "[ContextWindow] %s: %d tokens used%s",
            self._model or "unknown",
            used_tokens,
            f" ({used_pct}% of {snapshot['max_tokens']})"
            if used_pct is not None
            else "",
        )

        if (
            self._warn_pct > 0
            and isinstance(used_pct, (int, float))
            and used_pct >= self._warn_pct
            and not self._warned
        ):
            logger.warning(
                "[ContextWindow] Context usage at %.1f%% (>= %.1f%% threshold) "
                "for model %s — consider compacting the conversation",
                used_pct,
                self._warn_pct,
                self._model,
            )
            self._warned = True

        return None
