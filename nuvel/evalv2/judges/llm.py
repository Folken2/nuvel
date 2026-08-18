"""LLM judge — grades an output against a weighted rubric.

The judge asks a model to score each rubric dimension on a 0..1 scale and
returns a single weighted-average composite. All model access goes through a
``judge_fn`` seam: tests inject a fake that returns canned JSON, so the test
path never imports litellm or touches the network. The default seam calls
litellm lazily (imported inside the function, never at module load).

Model output is parsed tolerantly — many models wrap JSON in prose or code
fences. A parse that fails entirely yields ``score=None`` with a note rather
than raising.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..schema import EvaluatorResult
from ..suite import EvalExample


# score >= this counts as a pass for the llm-judge evaluator.
_PASS_THRESHOLD = 0.5
_DEFAULT_JUDGE_MODEL = "openai/gpt-4o-mini"

# A callable that turns a prompt into the model's raw text response.
JudgeFn = Callable[[str], str]


@dataclass
class Rubric:
    """A weighted set of scoring dimensions for the LLM judge."""

    dimensions: dict[str, float] = field(default_factory=dict)
    model: str | None = None
    max_cost: float | None = None

    @classmethod
    def from_config(cls, config: dict) -> "Rubric":
        """Build a Rubric from an evaluator config block.

        Accepts either ``{"rubric": {...}, "model": ..., "max_cost": ...}``
        or a bare dimensions mapping.
        """
        rubric = config.get("rubric") if isinstance(config, dict) else None
        dims = rubric if isinstance(rubric, dict) else (config if isinstance(config, dict) else {})
        dimensions = {str(k): float(v) for k, v in dims.items()}
        return cls(
            dimensions=dimensions,
            model=config.get("model") if isinstance(config, dict) else None,
            max_cost=config.get("max_cost") if isinstance(config, dict) else None,
        )


_PROMPT_TEMPLATE = """\
You are an evaluation judge for AI agent outputs. Score the OUTPUT below
against each rubric dimension on a 0.0..1.0 scale, where 1.0 is perfect.

USER INPUT:
{input}

EXPECTED OUTPUT (reference, may be empty):
{expected}

OUTPUT TO GRADE:
{output}

RUBRIC DIMENSIONS:
{dimensions}

Return ONE JSON object and nothing else — one numeric score per dimension:
{schema}
"""


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json(content: str) -> dict[str, Any]:
    """Tolerant parse — strips code fences / surrounding prose."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    match = _JSON_RE.search(content)
    if not match:
        raise json.JSONDecodeError("no JSON object found", content, 0)
    return json.loads(match.group(0))


def _coerce_score(value: Any) -> float | None:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _build_prompt(output: str, example: EvalExample, rubric: Rubric) -> str:
    dims = "\n".join(f"  - {name} (weight {weight})" for name, weight in rubric.dimensions.items())
    schema = "{\n" + ",\n".join(f'  "{name}": <number 0..1>' for name in rubric.dimensions) + "\n}"
    return _PROMPT_TEMPLATE.format(
        input=(example.input or "(none)"),
        expected=(example.expected_output if example.expected_output is not None else "(none)"),
        output=output,
        dimensions=dims or "  (none)",
        schema=schema,
    )


def _weighted_average(scores: dict[str, float], weights: dict[str, float]) -> float | None:
    """Weighted mean of the dimensions present in both ``scores`` and ``weights``."""
    total_weight = 0.0
    acc = 0.0
    for name, weight in weights.items():
        if name in scores:
            w = float(weight)
            acc += scores[name] * w
            total_weight += w
    if total_weight <= 0:
        return None
    return acc / total_weight


def _default_judge_fn(prompt: str, model: str) -> str:
    """Real judge: a single litellm completion. Imported lazily."""
    import litellm  # noqa: PLC0415 — kept out of module import / test path

    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=400,
    )
    return (response.choices[0].message.content or "").strip()


def judge_output(
    output: str,
    example: EvalExample,
    rubric: Rubric,
    judge_fn: JudgeFn | None = None,
) -> EvaluatorResult:
    """Grade ``output`` against ``rubric`` and return an EvaluatorResult.

    ``judge_fn(prompt) -> str`` is the model seam; when omitted the default
    litellm-backed judge is used. A missing/garbage response is captured as a
    note with ``score=None`` — never raised.
    """
    if not rubric.dimensions:
        return EvaluatorResult(
            evaluator="llm-judge",
            name="llm-judge",
            score=None,
            passed=None,
            details={"note": "rubric has no dimensions"},
        )

    prompt = _build_prompt(output, example, rubric)
    resolved_model = rubric.model or _DEFAULT_JUDGE_MODEL

    try:
        if judge_fn is not None:
            content = judge_fn(prompt)
        else:
            content = _default_judge_fn(prompt, resolved_model)
    except Exception as exc:  # noqa: BLE001 — model surface is wide
        return EvaluatorResult(
            evaluator="llm-judge",
            name="llm-judge",
            score=None,
            passed=None,
            details={"note": f"judge call failed: {type(exc).__name__}: {exc}"},
        )

    try:
        data = _parse_json(content or "")
    except json.JSONDecodeError as exc:
        return EvaluatorResult(
            evaluator="llm-judge",
            name="llm-judge",
            score=None,
            passed=None,
            details={"note": f"could not parse judge JSON: {exc}"},
        )

    dim_scores: dict[str, float] = {}
    for name in rubric.dimensions:
        coerced = _coerce_score(data.get(name))
        if coerced is not None:
            dim_scores[name] = coerced

    if not dim_scores:
        return EvaluatorResult(
            evaluator="llm-judge",
            name="llm-judge",
            score=None,
            passed=None,
            details={"note": "judge returned no usable dimension scores", "raw": dim_scores},
        )

    score = _weighted_average(dim_scores, rubric.dimensions)
    passed = score is not None and score >= _PASS_THRESHOLD
    return EvaluatorResult(
        evaluator="llm-judge",
        name="llm-judge",
        score=score,
        passed=passed,
        details={"dimensions": dim_scores, "model": resolved_model},
    )
