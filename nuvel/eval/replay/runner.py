"""Replay orchestration.

``_call_litellm_chat`` is the litellm boundary (system+user → output). It is
NOT the judge's adapter: replay generates prose, so it sends no
``response_format`` hint and therefore needs no empty-content fallback (that
fallback exists only because the judge requests JSON). ``replay_run`` does one
trace; ``ReplayRunner`` (Task 4) is the batch driver.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from dataclasses import asdict
from typing import Awaitable, Callable

from nuvel.eval.replay.schema import ReplayResult, Variant
from nuvel.eval.rubric import DEFAULT_RUBRIC, Rubric
from nuvel.eval.scorer import JudgeFn, score_run
from nuvel.traces_cli import Run

logger = logging.getLogger(__name__)

# (model, system, user, *, temperature, max_tokens) -> (output_text, cost_usd)
ChatFn = Callable[..., Awaitable[tuple[str, float]]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _call_litellm_chat(
    model: str,
    system_prompt: str,
    user_input: str,
    *,
    temperature: float,
    max_tokens: int,
) -> tuple[str, float]:
    """Thin litellm adapter for replay. Returns (output_text, cost_usd)."""
    import litellm

    from nuvel.eval.judge import _extract_cost, _quiet_litellm_once

    _quiet_litellm_once()
    response = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = (response.choices[0].message.content or "").strip()
    return content, _extract_cost(response, model)


def build_synthetic_run(src: Run, output_text: str) -> Run:
    """Shape a ``Run`` the scorer can judge from a single replayed output.

    CRITICAL: ``apply_heuristics`` early-exits with ``skip_judge=True`` if the
    run has no ``run_end`` event (incomplete trace) or ``completion_tokens == 0``
    (no assistant output). Both must be satisfied or the judge never runs and
    quality silently stays 0.0. Hence the explicit ``run_end`` event and
    ``completion_tokens=1``.
    """
    return Run(
        agent=src.agent,
        file=src.file,
        session_id=src.session_id,
        trace_id=src.trace_id,
        user_input=src.user_input,
        completion_tokens=1,
        schema="adk",
        events=[
            {"event": "run_start", "user_input": src.user_input or ""},
            {"event": "llm_response", "response_text": output_text},
            {"event": "run_end"},
        ],
    )


async def replay_run(
    src: Run,
    variant: Variant,
    *,
    rubric: Rubric | None = None,
    _call: ChatFn = _call_litellm_chat,
    judge_fn: JudgeFn | None = None,
) -> ReplayResult:
    """Replay one trace under ``variant`` and score the output.

    Assumes ``src.user_input`` is non-empty (caller filters). Retries the chat
    call once on exception (mirrors the judge's retry); re-raises on the second
    failure so the batch driver can record ``replay.error`` and skip the trace.
    """
    model = variant.resolved_model()
    user_input = src.user_input or ""

    last_exc: Exception | None = None
    output_text = ""
    replay_cost = 0.0
    for attempt in (1, 2):
        try:
            output_text, replay_cost = await _call(
                model, variant.system_prompt, user_input,
                temperature=variant.temperature, max_tokens=variant.max_tokens,
            )
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001 — litellm surface is wide
            last_exc = exc
            logger.warning("replay chat failed (attempt %d): %s", attempt, exc)
    if last_exc is not None:
        raise last_exc

    synthetic = build_synthetic_run(src, output_text)
    scored = await score_run(synthetic, rubric=rubric or DEFAULT_RUBRIC, judge_fn=judge_fn)

    return ReplayResult(
        trace_id=src.trace_id or src.session_id or "",
        agent=src.agent,
        variant_name=variant.name,
        variant_version=variant.version,
        replayed_at=_now_iso(),
        model=model,
        output_text=output_text,
        replay_cost_usd=replay_cost,
        scored=asdict(scored),
    )
