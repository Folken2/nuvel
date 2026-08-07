"""EvalRunner — executes a suite's examples and scores their outputs.

The runner is decoupled from any agent harness by an **executor** seam: a
``Callable[[EvalSuite, EvalExample], str]`` that turns an example into the
skill's output text. A default ``LLMExecutor`` runs the skill's ``SKILL.md``
against the example via litellm (lazy import), but tests inject fakes so the
core never hits the network.

For each example the runner:

1. Derives a deterministic cache key from ``(skill, model, sha256(input))``.
2. On a cache hit, reuses the stored ``ScoredExample``.
3. Otherwise runs the executor, applies the configured evaluators
   (deterministic checks, an LLM judge, self-consistency), composites their
   scores, and stores the result.

Results aggregate into an ``EvalSuiteResult`` with pass/warn/fail/unscored
counts and any flags (e.g. self-consistency disagreement). Phase 2 is
strictly sequential — no asyncio.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .cache import SampleCache
from .judges import Executor, judge_output, run_consistency, run_deterministic_checks
from .judges.llm import JudgeFn, Rubric
from .schema import (
    SCHEMA_VERSION,
    EvalSuiteResult,
    EvalSummary,
    EvaluatorResult,
    ScoredExample,
)
from .suite import EvalExample, EvalSuite


_DEFAULT_MODEL = "default"
_DEFAULT_PASS = 0.8
_DEFAULT_WARN = 0.6


class LLMExecutor:
    """Default executor: run a skill's SKILL.md against an example via litellm.

    The skill lives one directory above the eval suite (``suite.root.parent /
    "SKILL.md"``). ``complete_fn`` is an injection seam mirroring the judge —
    when provided it is called instead of litellm, keeping the network out of
    the test path.
    """

    def __init__(
        self,
        model: str | None = None,
        complete_fn: Callable[[str, str, str], str] | None = None,
    ):
        self.model = model
        self._complete_fn = complete_fn

    def _skill_instructions(self, suite: EvalSuite) -> str:
        if suite.root is None:
            return ""
        skill_md = Path(suite.root).parent / "SKILL.md"
        if skill_md.is_file():
            return skill_md.read_text(encoding="utf-8")
        return ""

    def __call__(self, suite: EvalSuite, example: EvalExample) -> str:
        instructions = self._skill_instructions(suite)
        model = self.model or _DEFAULT_MODEL
        if self._complete_fn is not None:
            return self._complete_fn(model, instructions, example.input)
        return self._run_litellm(model, instructions, example.input)

    @staticmethod
    def _run_litellm(model: str, instructions: str, user_input: str) -> str:
        import litellm  # noqa: PLC0415 — lazy, keeps import off the test path

        messages = []
        if instructions:
            messages.append({"role": "system", "content": instructions})
        messages.append({"role": "user", "content": user_input})
        response = litellm.completion(model=model, messages=messages, temperature=0.0)
        return (response.choices[0].message.content or "").strip()


@dataclass
class EvalRunConfig:
    """Options for one suite run."""

    model: str | None = None
    cache: SampleCache | None = None
    executor: Executor | None = None
    judge_fn: JudgeFn | None = None
    force: bool = False
    save_baseline: bool = False  # Phase 3 placeholder
    max_cost: float | None = None  # soft budget; tracked, not enforced in Phase 2


@dataclass
class _EvaluatorPlan:
    """Parsed view of a suite's ``evaluators`` block."""

    deterministic: list[dict] = field(default_factory=list)
    rubric: Rubric | None = None
    consistency: dict | None = None


def _parse_evaluators(suite: EvalSuite) -> _EvaluatorPlan:
    """Fold the suite's evaluator list into a typed plan."""
    plan = _EvaluatorPlan()
    for entry in suite.evaluators:
        if not isinstance(entry, dict):
            continue
        for kind, config in entry.items():
            if kind == "deterministic":
                if isinstance(config, list):
                    plan.deterministic.extend(c for c in config if isinstance(c, dict))
            elif kind == "llm-judge":
                plan.rubric = Rubric.from_config(config if isinstance(config, dict) else {})
            elif kind == "self-consistency":
                plan.consistency = config if isinstance(config, dict) else {}
    return plan


def _input_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _composite(results: list[EvaluatorResult]) -> float | None:
    """Equal-weighted mean of the evaluator scores that applied."""
    scores = [r.score for r in results if r.score is not None]
    if not scores:
        return None
    return sum(scores) / len(scores)


class EvalRunner:
    """Runs one `EvalSuite` and produces an `EvalSuiteResult`."""

    def __init__(self, suite: EvalSuite, config: EvalRunConfig | None = None):
        self.suite = suite
        self.config = config or EvalRunConfig()
        self.plan = _parse_evaluators(suite)
        self.executor: Executor = self.config.executor or LLMExecutor(self.config.model)

    def _resolved_model(self) -> str:
        return self.config.model or _DEFAULT_MODEL

    def _thresholds(self) -> tuple[float, float]:
        thresholds = self.suite.thresholds or {}
        pass_at = float(thresholds.get("pass", _DEFAULT_PASS))
        warn_at = float(thresholds.get("warn", _DEFAULT_WARN))
        return pass_at, warn_at

    def _score_example(self, example: EvalExample, flags: list[dict]) -> ScoredExample:
        """Run the executor + evaluators for one example (no cache)."""
        results: list[EvaluatorResult] = []
        notes: list[str] = []

        # Self-consistency owns the executor calls when configured — its
        # outputs also feed the other evaluators (first output is primary).
        if self.plan.consistency is not None:
            cfg = self.plan.consistency
            result, outputs = run_consistency(
                self.executor,
                self.suite,
                example,
                runs=int(cfg.get("runs", 3)),
                threshold=float(cfg.get("threshold", 0.9)),
                max_cost=cfg.get("max_cost"),
            )
            results.append(result)
            output = outputs[0] if outputs else ""
            if result.passed is False:
                flags.append(
                    {
                        "type": "judge-disagreement",
                        "example": example.id,
                        "agreement": result.details.get("agreement"),
                        "note": result.details.get("note", "self-consistency below threshold"),
                    }
                )
        else:
            output = self.executor(self.suite, example)

        if self.plan.deterministic:
            results.extend(run_deterministic_checks(output, self.plan.deterministic, example))

        if self.plan.rubric is not None:
            results.append(
                judge_output(output, example, self.plan.rubric, judge_fn=self.config.judge_fn)
            )

        score = _composite(results)
        scored = ScoredExample(
            id=example.id,
            input=example.input,
            score=score,
            evaluator_results=results,
            notes=notes,
        )
        self._apply_verdict(scored)
        return scored

    def _apply_verdict(self, scored: ScoredExample) -> None:
        pass_at, warn_at = self._thresholds()
        if scored.score is None:
            scored.passed = None
        elif scored.score >= pass_at:
            scored.passed = True
        elif scored.score >= warn_at:
            scored.passed = False
            scored.notes.append(f"warn: score {scored.score:.2f} below pass {pass_at:.2f}")
        else:
            scored.passed = False

    def run(self, progress: Callable[[str], None] | None = None) -> EvalSuiteResult:
        """Execute every example and return the aggregated suite result."""
        model = self._resolved_model()
        cache = self.config.cache
        pass_at, warn_at = self._thresholds()

        examples: list[ScoredExample] = []
        flags: list[dict] = []

        for example in self.suite.examples:
            in_hash = _input_hash(example.input)
            cached = None
            if cache is not None and not self.config.force:
                cached = cache.get(self.suite.skill, model, in_hash)

            if cached is not None:
                cached.cache_hit = True
                examples.append(cached)
                if progress is not None:
                    progress(f"{example.id}: cache hit (score={cached.score})")
                continue

            scored = self._score_example(example, flags)
            scored.cache_hit = False
            if cache is not None:
                cache.put(self.suite.skill, model, in_hash, scored)
            examples.append(scored)
            if progress is not None:
                progress(f"{example.id}: scored={scored.score} passed={scored.passed}")

        summary = self._summarize(examples, pass_at, warn_at)
        return EvalSuiteResult(
            schema_version=SCHEMA_VERSION,
            skill=self.suite.skill,
            suite=self.suite.name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            model=self.config.model,
            summary=summary,
            examples=examples,
            flags=flags,
        )

    def _summarize(
        self, examples: list[ScoredExample], pass_at: float, warn_at: float
    ) -> EvalSummary:
        summary = EvalSummary(total=len(examples))
        scored_values: list[float] = []
        for ex in examples:
            if ex.score is None:
                summary.unscored += 1
                continue
            scored_values.append(ex.score)
            if ex.score >= pass_at:
                summary.passed += 1
            elif ex.score >= warn_at:
                summary.warn += 1
            else:
                summary.failed += 1
        summary.overall = sum(scored_values) / len(scored_values) if scored_values else None
        return summary
