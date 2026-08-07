"""nuvel evalv2 — skill-driven, AI-native evaluation (Phase 1 core).

Phase 1 ships the foundation only: the data model (`schema`), the suite
loader (`EvalSuite`), the per-sample cache (`SampleCache`), and the error
hierarchy. Later phases add the runner, LLM judges, and CLI.

The existing ``nuvel.eval`` package is untouched — evalv2 is a clean break.
"""
from __future__ import annotations

from .cache import SampleCache
from .exceptions import (
    CacheError,
    EvalError,
    ExampleError,
    SchemaVersionError,
    SuiteError,
)
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
]
