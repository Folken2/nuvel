"""``GuardrailsPlugin`` — one ``BasePlugin`` binding the halt guards together.

Wraps the no-progress and repeated-failure guards behind the shared halt latch
so they all observe the same turn boundary:

* ``before_model_callback`` -> ``halt_consumer_callback`` short-circuits the
  model while a halt is latched.
* ``after_model_callback`` -> ``NoProgressGuard`` watches for repeated text.
* ``after_tool_callback`` -> ``RepeatedFailureGuard`` watches for tool loops.

The ADK plugin hooks name their tool params ``tool_args``/``result``; the
wrapped guard expects ``args``/``tool_response``, so this adapter renames them.
"""

from __future__ import annotations

from typing import Any

from google.adk.models import LlmRequest, LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

from .halt_consumer import halt_consumer_callback
from .no_progress import NoProgressGuard
from .repeated_failure import RepeatedFailureGuard


class GuardrailsPlugin(BasePlugin):
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        no_progress_window: int = 5,
    ) -> None:
        super().__init__(name="guardrails")
        self._repeated_failure = RepeatedFailureGuard(threshold=failure_threshold)
        self._no_progress = NoProgressGuard(window=no_progress_window)

    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        return await halt_consumer_callback(
            callback_context=callback_context, llm_request=llm_request
        )

    async def after_model_callback(
        self,
        *,
        callback_context: Any,
        llm_response: LlmResponse,
    ) -> None:
        return await self._no_progress.after_model_callback(
            callback_context=callback_context, llm_response=llm_response
        )

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: Any,
        tool_context: Any,
        result: Any,
    ) -> None:
        return await self._repeated_failure.after_tool_callback(
            tool=tool,
            args=tool_args,
            tool_response=result,
            tool_context=tool_context,
        )


__all__ = ["GuardrailsPlugin"]
