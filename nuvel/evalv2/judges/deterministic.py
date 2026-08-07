"""Deterministic evaluators — pure functions, no LLM.

Each check inspects a skill's output against a simple, reproducible rule:
exact / fuzzy string match, a length ceiling, required keywords, or a
minimal JSON-schema (required-keys) contract. Every check yields an
`EvaluatorResult` with ``evaluator="deterministic"``; the check ``type`` is
carried in ``name``.

A check may target the whole output string or a dotted ``field`` path into
an output that parses as a JSON object (``output.summary`` → the ``summary``
key of the ``output`` object). A field that can't be resolved fails the
check with an explanatory note rather than raising.
"""
from __future__ import annotations

import difflib
import json
from typing import Any

from ..schema import EvaluatorResult
from ..suite import EvalExample


def _resolve_field(output: str, field: str | None) -> tuple[Any, str | None]:
    """Return ``(value, error)`` for a dotted field path into the output.

    ``field=None`` yields the whole output string. Anything else parses the
    output as JSON and walks the dotted path; a parse failure or a missing
    key produces an error message and a ``None`` value.
    """
    if field is None:
        return output, None
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None, f"output is not valid JSON; cannot access field '{field}'"
    cur: Any = data
    for part in field.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None, f"field '{field}' not found in output"
    return cur, None


def _as_text(value: Any) -> str:
    """Render a resolved field value as text for string comparisons."""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)


def _result(name: str, *, score: float | None, passed: bool | None, **details: Any) -> EvaluatorResult:
    return EvaluatorResult(
        evaluator="deterministic",
        name=name,
        score=score,
        passed=passed,
        details={k: v for k, v in details.items() if v is not None},
    )


def _check_exact_match(check: dict, value: str, example: EvalExample, name: str) -> EvaluatorResult:
    expected = example.expected_output
    if expected is None:
        return _result(name, score=None, passed=None, note="exact-match requires expected_output")
    threshold = check.get("threshold")
    if threshold is None:
        passed = value == expected
        return _result(name, score=1.0 if passed else 0.0, passed=passed, expected=expected)
    ratio = difflib.SequenceMatcher(None, value, expected).ratio()
    passed = ratio >= float(threshold)
    return _result(name, score=ratio, passed=passed, threshold=threshold, ratio=round(ratio, 4))


def _check_max_length(check: dict, value: str, name: str) -> EvaluatorResult:
    max_chars = check.get("max_chars")
    if max_chars is None:
        return _result(name, score=None, passed=None, note="max-length requires max_chars")
    passed = len(value) <= int(max_chars)
    return _result(name, score=1.0 if passed else 0.0, passed=passed, length=len(value), max_chars=max_chars)


def _check_has_keywords(check: dict, value: str, name: str) -> EvaluatorResult:
    keywords = check.get("keywords") or []
    if not keywords:
        return _result(name, score=None, passed=None, note="has-keywords requires keywords")
    haystack = value.lower()
    missing = [kw for kw in keywords if str(kw).lower() not in haystack]
    passed = not missing
    return _result(name, score=1.0 if passed else 0.0, passed=passed, missing=missing or None)


def _check_json_schema(check: dict, output: str, field: str | None, raw_value: Any, name: str) -> EvaluatorResult:
    schema = check.get("schema") or {}
    required = schema.get("required") or []
    # The target object is either the resolved field value (already parsed)
    # or the whole output parsed as JSON.
    if field is None:
        try:
            obj = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return _result(name, score=0.0, passed=False, note="output is not valid JSON")
    else:
        obj = raw_value
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
            except (json.JSONDecodeError, TypeError):
                return _result(name, score=0.0, passed=False, note=f"field '{field}' is not valid JSON")
    if not isinstance(obj, dict):
        return _result(name, score=0.0, passed=False, note="target is not a JSON object")
    missing = [key for key in required if key not in obj]
    passed = not missing
    return _result(name, score=1.0 if passed else 0.0, passed=passed, missing=missing or None)


def run_deterministic_checks(
    output: str,
    checks: list[dict],
    example: EvalExample | None = None,
) -> list[EvaluatorResult]:
    """Run every deterministic ``check`` against ``output``.

    ``example`` supplies ``expected_output`` for match checks. Unknown check
    types yield a non-crashing ``EvaluatorResult`` with ``score=None`` and an
    explanatory note.
    """
    results: list[EvaluatorResult] = []
    for check in checks:
        ctype = str(check.get("type") or "").strip()
        name = ctype or "unknown"
        field = check.get("field")
        value, field_error = _resolve_field(output, field)
        if field_error is not None:
            results.append(_result(name, score=0.0, passed=False, note=field_error))
            continue
        text = _as_text(value)

        if ctype == "exact-match":
            ex = example or EvalExample(id="", input="")
            results.append(_check_exact_match(check, text, ex, name))
        elif ctype == "max-length":
            results.append(_check_max_length(check, text, name))
        elif ctype == "has-keywords":
            results.append(_check_has_keywords(check, text, name))
        elif ctype == "json-schema":
            results.append(_check_json_schema(check, output, field, value, name))
        else:
            results.append(_result(name, score=None, passed=None, note="unknown check type"))
    return results
