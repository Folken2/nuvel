"""LLM judge: one structured call per qualifying run.

Reads the assistant's response text from ``llm_response`` events and the
sequence of tool invocations, asks the configured model to grade
``did_solve`` and ``quality`` on 0..1 scales, parses JSON, returns a
``JudgeResult``. Retries once on parse failure or transient errors.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from nuvel.eval.rubric import Rubric
from nuvel.eval.schema import JudgeResult
from nuvel.traces_cli import Run


logger = logging.getLogger(__name__)


_MAX_ASSISTANT_CHARS = 8000
_MAX_TOOLS_LISTED = 25


_PROMPT_TEMPLATE = """\
You are an automated quality evaluator for AI agent runs.

USER REQUEST:
{user_input}

ASSISTANT OUTPUT (concatenated turns, truncated if long):
{assistant_text}

TOOL CALLS ({tool_call_count} total):
{tool_summary}

{extra_criteria}\
Score this run on two axes:
  - did_solve: 0.0..1.0  — did the assistant actually address the user's intent?
  - quality:   0.0..1.0  — was the output coherent, accurate, well-formed?

Return ONE JSON object and nothing else. Schema:
{{
  "did_solve": <number 0..1>,
  "quality":   <number 0..1>,
  "efficiency_note": "<short text or empty>",
  "notes":            "<one sentence summary>"
}}
"""


def _assistant_text(run: Run) -> str:
    chunks: list[str] = []
    remaining = _MAX_ASSISTANT_CHARS
    for ev in run.events:
        if ev.get("event") != "llm_response":
            continue
        text = ev.get("response_text") or ""
        if not text:
            continue
        if remaining <= 0:
            chunks.append("[…truncated]")
            break
        snippet = text if len(text) <= remaining else text[:remaining] + "[…]"
        chunks.append(snippet)
        remaining -= len(snippet)
    return "\n---\n".join(chunks) or "(no assistant output)"


def _tool_summary(run: Run) -> tuple[str, int]:
    """Return (rendered multi-line summary, total count)."""
    calls: list[tuple[str, str]] = []  # (tool, status)
    pending: dict[str, str] = {}
    for ev in run.events:
        name = ev.get("event")
        if name == "tool_start":
            pending[ev.get("tool") or "?"] = "started"
        elif name == "tool_end":
            tool = ev.get("tool") or "?"
            status = ev.get("status") or "ok"
            calls.append((tool, status))
            pending.pop(tool, None)
        elif name == "tool_exception":
            tool = ev.get("tool") or "?"
            calls.append((tool, "exception"))
            pending.pop(tool, None)
    # Tools that never resolved.
    for tool in pending:
        calls.append((tool, "pending"))

    count = len(calls)
    if not calls:
        return ("(no tool calls)", 0)
    listed = calls[:_MAX_TOOLS_LISTED]
    rendered = "\n".join(f"  - {tool} [{status}]" for tool, status in listed)
    if count > _MAX_TOOLS_LISTED:
        rendered += f"\n  …and {count - _MAX_TOOLS_LISTED} more"
    return (rendered, count)


def _build_prompt(run: Run, rubric: Rubric) -> str:
    tools, count = _tool_summary(run)
    extra = (rubric.extra_criteria or "").strip()
    if extra:
        extra = f"ADDITIONAL CRITERIA:\n{extra}\n\n"
    return _PROMPT_TEMPLATE.format(
        user_input=(run.user_input or "(none)")[:2000],
        assistant_text=_assistant_text(run),
        tool_call_count=count,
        tool_summary=tools,
        extra_criteria=extra,
    )


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_judge_json(content: str) -> dict[str, Any]:
    """Tolerant parse — many models wrap JSON in prose or fences."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    match = _JSON_RE.search(content)
    if not match:
        raise json.JSONDecodeError("no JSON object found", content, 0)
    return json.loads(match.group(0))


def _coerce_score(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


# Models that have already warn-logged a cost-lookup miss. One warning
# per process per model is plenty; don't spam the operator.
_UNPRICED_MODELS: set[str] = set()


def _quiet_litellm_once() -> None:
    """Suppress litellm's stderr 'Provider List' noise for cost lookups.

    Called lazily from the adapter — keeps litellm imports out of module
    load order and out of the test path (which never touches the real
    adapter via the injection seam).
    """
    import logging
    import litellm

    # `suppress_debug_info` silences the 'Provider List' banner that litellm
    # prints to stderr when it can't price a model. Safe to set repeatedly.
    if not getattr(litellm, "_nuvel_quieted", False):
        litellm.suppress_debug_info = True
        # Belt-and-suspenders: also raise the logger level for litellm itself.
        logging.getLogger("LiteLLM").setLevel(logging.ERROR)
        litellm._nuvel_quieted = True  # type: ignore[attr-defined]


def _extract_cost(response: object, model: str) -> float:
    """Best-effort cost lookup via litellm. Warn once per unpriced model."""
    import litellm

    try:
        return float(litellm.completion_cost(completion_response=response) or 0.0)
    except Exception as exc:  # noqa: BLE001 — cost is best-effort
        if model not in _UNPRICED_MODELS:
            _UNPRICED_MODELS.add(model)
            logger.warning(
                "judge cost lookup failed for %s (%s: %s) — budget guard cannot enforce limits "
                "on this model; falling back to $0.00 per call",
                model, type(exc).__name__, exc,
            )
        return 0.0


async def _call_litellm(model: str, prompt: str) -> tuple[str, float]:
    """Thin litellm adapter. Returns (content, cost_usd).

    Tries ``response_format=json_object`` first — modern models (Sonnet,
    GPT-4o, Gemini) honor it and produce cleaner output. Some OpenRouter
    providers (notably Kimi K2.5) reject the hint and return empty
    content; in that case we transparently retry once without the hint.
    Both calls' costs are summed.
    """
    import litellm

    _quiet_litellm_once()
    base_kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 400,
    }

    response = await litellm.acompletion(**base_kwargs, response_format={"type": "json_object"})
    content = (response.choices[0].message.content or "").strip()
    total_cost = _extract_cost(response, model)

    if not content:
        # response_format wasn't honored — retry plain.
        logger.info("empty content from %s with response_format; retrying without hint", model)
        response = await litellm.acompletion(**base_kwargs)
        content = (response.choices[0].message.content or "").strip()
        total_cost += _extract_cost(response, model)

    return content, total_cost


async def judge_run(
    run: Run,
    rubric: Rubric,
    *,
    model: str | None = None,
    _call: callable = _call_litellm,  # injection seam for tests
) -> JudgeResult:
    """Score one run with an LLM. Returns a JudgeResult — errors captured in .error."""
    resolved_model = model or rubric.resolved_model()
    prompt = _build_prompt(run, rubric)

    last_error: str | None = None
    total_cost = 0.0
    for attempt in (1, 2):
        try:
            content, cost = await _call(resolved_model, prompt)
            total_cost += cost
            data = _parse_judge_json(content)
            return JudgeResult(
                model=resolved_model,
                success=_coerce_score(data.get("did_solve")),
                quality=_coerce_score(data.get("quality")),
                notes=str(data.get("notes") or data.get("efficiency_note") or "")[:500],
                cost_usd=total_cost,
            )
        except json.JSONDecodeError as exc:
            last_error = f"json: {exc}"
            logger.warning("judge JSON parse failed (attempt %d): %s", attempt, exc)
        except Exception as exc:  # noqa: BLE001 — litellm error surface is wide
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("judge call failed (attempt %d): %s", attempt, last_error)

    return JudgeResult(
        model=resolved_model,
        cost_usd=total_cost,
        error=last_error or "unknown",
    )
