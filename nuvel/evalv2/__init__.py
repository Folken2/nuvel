"""nuvel evalv2 — skill-driven, AI-native evaluation (Phase 1 core).

Phase 1 shipped the foundation: the data model (`schema`), the suite loader
(`EvalSuite`), the per-sample cache (`SampleCache`), and the error hierarchy.
Phase 2 adds the runner (`EvalRunner`) and the judges — deterministic checks,
the weighted-rubric LLM judge, and self-consistency. Phase 3 adds the baseline
comparison engine (`compare_results`) and the `nuvel evalv2` CLI.

The existing ``nuvel.eval`` package is untouched — evalv2 is a clean break.
"""
from __future__ import annotations

from .cache import SampleCache
from .compare import ComparisonReport, compare_results
from .exceptions import (
    CacheError,
    EvalError,
    ExampleError,
    SchemaVersionError,
    SuiteError,
)
from .judges import Rubric, judge_output, run_consistency, run_deterministic_checks
from .runner import EvalRunConfig, EvalRunner, LLMExecutor
from .schema import (
    SCHEMA_VERSION,
    EvalSummary,
    EvalSuiteResult,
    EvaluatorResult,
    ScoredExample,
)
from .suite import EvalExample, EvalSuite

__all__ = [
    "SCHEMA_VERSION",
    "SampleCache",
    "EvalSuite",
    "EvalExample",
    "EvalSuiteResult",
    "EvalSummary",
    "EvaluatorResult",
    "ScoredExample",
    "EvalError",
    "SuiteError",
    "ExampleError",
    "CacheError",
    "SchemaVersionError",
    # Phase 2: runner + judges
    "EvalRunner",
    "EvalRunConfig",
    "LLMExecutor",
    "Rubric",
    "judge_output",
    "run_deterministic_checks",
    "run_consistency",
    # Phase 3: comparison
    "ComparisonReport",
    "compare_results",
]
