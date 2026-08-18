"""Wire formats for evalv2 results.

These dataclasses are the contract between the runner (Phase 2), the cache,
and the primary consumer — an AI agent parsing structured JSON. Everything
here round-trips through `to_dict()` / `from_dict()` so a `ScoredExample`
can be cached and rehydrated without loss.

`SCHEMA_VERSION` is bumped deliberately: a change signals that previously
written payloads may not match the current shape.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = "2.0"


@dataclass
class EvaluatorResult:
    """Output of one evaluator applied to one example.

    ``score`` is a normalized 0..1 value, or ``None`` when the evaluator did
    not apply. ``passed`` lets an evaluator override a raw score with an
    explicit verdict (e.g. a deterministic check that either matches or not).
    """

    evaluator: str  # "llm-judge" | "deterministic" | "self-consistency" | "custom"
    name: str  # specific evaluator name
    score: float | None = None
    passed: bool | None = None
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvaluatorResult":
        return cls(
            evaluator=data["evaluator"],
            name=data["name"],
            score=data.get("score"),
            passed=data.get("passed"),
            details=dict(data.get("details") or {}),
        )


@dataclass
class ScoredExample:
    """One example after evaluation.

    ``score`` is the weighted composite across ``evaluator_results`` (or
    ``None`` if the example was never scored). ``cache_hit`` records whether
    this result came from the cache rather than a fresh run.
    """

    id: str
    input: str
    score: float | None = None
    passed: bool | None = None
    evaluator_results: list[EvaluatorResult] = field(default_factory=list)
    cache_hit: bool = False
    cost: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "input": self.input,
            "score": self.score,
            "passed": self.passed,
            "evaluator_results": [r.to_dict() for r in self.evaluator_results],
            "cache_hit": self.cache_hit,
            "cost": self.cost,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScoredExample":
        return cls(
            id=data["id"],
            input=data["input"],
            score=data.get("score"),
            passed=data.get("passed"),
            evaluator_results=[
                EvaluatorResult.from_dict(r) for r in (data.get("evaluator_results") or [])
            ],
            cache_hit=bool(data.get("cache_hit", False)),
            cost=float(data.get("cost", 0.0)),
            notes=list(data.get("notes") or []),
        )


@dataclass
class EvalSummary:
    """Aggregate counts and scores across a suite run.

    ``overall`` is the mean composite score; ``baseline_overall`` / ``delta``
    are populated when a run is compared against a stored baseline, and
    ``regression`` is set when ``delta`` breaches the suite's regression
    threshold.
    """

    total: int = 0
    passed: int = 0
    warn: int = 0
    failed: int = 0
    unscored: int = 0
    overall: float | None = None
    baseline_overall: float | None = None
    delta: float | None = None
    regression: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvalSummary":
        return cls(
            total=int(data.get("total", 0)),
            passed=int(data.get("passed", 0)),
            warn=int(data.get("warn", 0)),
            failed=int(data.get("failed", 0)),
            unscored=int(data.get("unscored", 0)),
            overall=data.get("overall"),
            baseline_overall=data.get("baseline_overall"),
            delta=data.get("delta"),
            regression=bool(data.get("regression", False)),
        )


@dataclass
class EvalSuiteResult:
    """The full result of running one suite — the top-level wire object.

    This is what gets serialized to JSON for the AI consumer and for
    ``nuvel eval`` output.
    """

    schema_version: str
    skill: str
    suite: str
    timestamp: str
    model: str | None = None
    summary: EvalSummary = field(default_factory=EvalSummary)
    examples: list[ScoredExample] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    cost: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "skill": self.skill,
            "suite": self.suite,
            "timestamp": self.timestamp,
            "model": self.model,
            "summary": self.summary.to_dict(),
            "examples": [e.to_dict() for e in self.examples],
            "flags": [dict(f) for f in self.flags],
            "cost": dict(self.cost),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvalSuiteResult":
        version = data.get("schema_version")
        return cls(
            schema_version=version,
            skill=data["skill"],
            suite=data["suite"],
            timestamp=data["timestamp"],
            model=data.get("model"),
            summary=EvalSummary.from_dict(data.get("summary") or {}),
            examples=[ScoredExample.from_dict(e) for e in (data.get("examples") or [])],
            flags=[dict(f) for f in (data.get("flags") or [])],
            cost=dict(data.get("cost") or {}),
        )
