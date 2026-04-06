"""
CostGuardPlugin — Token cost tracking and budget enforcement.

Calculates USD cost for every LLM call using a pricing.json config file,
logs costs into session state for traces, and optionally blocks requests
that would exceed a per-session budget.

Configuration:
  COST_GUARD_BUDGET: max USD per session (default: 0 = no limit)
  COST_GUARD_PRICING: path to pricing.json (default: auto-detected)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from google.genai import types

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext

logger = logging.getLogger(__name__)

_DEFAULT_PRICING_PATH = Path(__file__).parent / "pricing.json"


def _load_pricing(path: Optional[str] = None) -> dict[str, dict[str, float]]:
    """Load model pricing from JSON file."""
    pricing_path = Path(path) if path else _DEFAULT_PRICING_PATH
    if not pricing_path.is_file():
        logger.warning("[CostGuard] Pricing file not found: %s", pricing_path)
        return {}
    try:
        data = json.loads(pricing_path.read_text(encoding="utf-8"))
        # Strip comment keys
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception as e:
        logger.error("[CostGuard] Failed to load pricing: %s", e)
        return {}


def _find_pricing(
    model: str, pricing: dict[str, dict[str, float]]
) -> Optional[dict[str, float]]:
    """Find pricing for a model, trying exact match then prefix match.

    Handles model IDs like 'openrouter/moonshotai/kimi-k2.5' by stripping
    the provider prefix and trying progressively shorter matches.
    """
    if not model:
        return None

    # Try exact match first
    if model in pricing:
        return pricing[model]

    # Strip common provider prefixes (openrouter/, litellm/, etc.)
    parts = model.split("/")
    for i in range(len(parts)):
        candidate = "/".join(parts[i:])
        if candidate in pricing:
            return pricing[candidate]

    return None


def calculate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    pricing: dict[str, dict[str, float]],
) -> Optional[float]:
    """Calculate USD cost for a single LLM call.

    Returns None if pricing is not available for the model.
    """
    model_pricing = _find_pricing(model, pricing)
    if not model_pricing:
        return None

    input_cost = prompt_tokens * model_pricing.get("input", 0)
    output_cost = completion_tokens * model_pricing.get("output", 0)
    return round(input_cost + output_cost, 8)


class CostGuardPlugin(BasePlugin):
    """Tracks LLM costs and enforces per-session budget limits.

    Cost data is propagated to clients via ADK's session state mechanism.
    After each LLM call, the plugin writes a ``cost_guard`` key to
    ``callback_context.state`` with the structure::

        {
            "call_cost_usd": 0.001234,
            "session_cost_usd": 0.005678,
            "budget_usd": 0.50,          # 0 = unlimited
            "blocked": false,
            "model": "moonshotai/kimi-k2.5-0127"
        }

    Custom UIs consuming the SSE event stream can read ``state.cost_guard``
    to display live cost info and react to budget blocks.
    """

    def __init__(self) -> None:
        super().__init__(name="cost_guard")
        self._pricing = _load_pricing(os.getenv("COST_GUARD_PRICING"))
        self._budget = float(os.getenv("COST_GUARD_BUDGET", "0"))
        self._session_cost: float = 0.0
        self._model: str = ""

        if self._pricing:
            logger.info(
                "[CostGuard] Loaded pricing for %d models, budget=$%.2f/session%s",
                len(self._pricing),
                self._budget,
                "" if self._budget > 0 else " (unlimited)",
            )
        else:
            logger.warning("[CostGuard] No pricing data loaded — costs will not be tracked")

    def _write_state(self, callback_context: CallbackContext, *, call_cost: float = 0, blocked: bool = False) -> None:
        """Write cost guard state for client consumption via SSE."""
        if hasattr(callback_context, "state"):
            callback_context.state["cost_guard"] = {
                "call_cost_usd": round(call_cost, 8),
                "session_cost_usd": round(self._session_cost, 8),
                "budget_usd": self._budget,
                "blocked": blocked,
                "model": self._model,
            }

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> Optional[types.Content]:
        self._model = ""
        return None

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> Optional[LlmResponse]:
        self._model = llm_request.model or ""

        # Budget enforcement
        if self._budget > 0 and self._session_cost >= self._budget:
            logger.warning(
                "[CostGuard] Budget exceeded: $%.4f >= $%.2f — blocking request",
                self._session_cost,
                self._budget,
            )
            self._write_state(callback_context, blocked=True)
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text=f"I've reached the cost limit for this session "
                            f"(${self._session_cost:.4f} / ${self._budget:.2f}). "
                            f"Please start a new session to continue."
                        )
                    ],
                ),
                turn_complete=True,
            )
        return None

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> Optional[LlmResponse]:
        if not self._pricing:
            return None

        usage = llm_response.usage_metadata
        if not usage:
            return None

        prompt_tokens = usage.prompt_token_count or 0
        completion_tokens = usage.candidates_token_count or 0

        cost = calculate_cost(
            self._model, prompt_tokens, completion_tokens, self._pricing
        )

        if cost is not None:
            self._session_cost += cost
            logger.info(
                "[CostGuard] LLM call: $%.6f (session total: $%.6f) — %s",
                cost,
                self._session_cost,
                self._model,
            )
            self._write_state(callback_context, call_cost=cost)
        else:
            logger.debug(
                "[CostGuard] No pricing for model '%s' — cost not tracked",
                self._model,
            )

        return None
