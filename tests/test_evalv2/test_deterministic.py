"""Tests for evalv2 deterministic judges."""
from __future__ import annotations

import json

from nuvel.evalv2 import run_deterministic_checks
from nuvel.evalv2.suite import EvalExample


def _example(expected: str | None = None) -> EvalExample:
    return EvalExample(id="ex1", input="in", expected_output=expected)


def test_exact_match_string_equality():
    ex = _example(expected="hello world")
    [res] = run_deterministic_checks("hello world", [{"type": "exact-match"}], ex)
    assert res.passed is True
    assert res.score == 1.0

    [res] = run_deterministic_checks("goodbye", [{"type": "exact-match"}], ex)
    assert res.passed is False
    assert res.score == 0.0


def test_exact_match_threshold_near_and_far():
    ex = _example(expected="the quick brown fox")
    checks = [{"type": "exact-match", "threshold": 0.8}]

    # near-miss (one char) — ratio stays above threshold
    [near] = run_deterministic_checks("the quick brown fax", checks, ex)
    assert near.passed is True
    assert near.score >= 0.8

    # far-miss — well below threshold
    [far] = run_deterministic_checks("completely different text", checks, ex)
    assert far.passed is False
    assert far.score < 0.8


def test_max_length_pass_and_fail():
    checks = [{"type": "max-length", "max_chars": 5}]
    [ok] = run_deterministic_checks("abc", checks)
    assert ok.passed is True
    [bad] = run_deterministic_checks("abcdefg", checks)
    assert bad.passed is False


def test_has_keywords_all_present_and_missing():
    checks = [{"type": "has-keywords", "keywords": ["Revenue", "growth"]}]
    [ok] = run_deterministic_checks("revenue grew, strong GROWTH", checks)
    assert ok.passed is True

    [bad] = run_deterministic_checks("revenue was flat", checks)
    assert bad.passed is False
    assert "growth" in bad.details.get("missing", [])


def test_json_schema_valid_invalid_and_missing_key():
    checks = [{"type": "json-schema", "schema": {"required": ["summary", "tone"]}}]

    valid = json.dumps({"summary": "s", "tone": "neutral"})
    [ok] = run_deterministic_checks(valid, checks)
    assert ok.passed is True

    [not_json] = run_deterministic_checks("not json at all", checks)
    assert not_json.passed is False

    missing = json.dumps({"summary": "s"})
    [miss] = run_deterministic_checks(missing, checks)
    assert miss.passed is False
    assert "tone" in miss.details.get("missing", [])


def test_unknown_check_type_does_not_crash():
    [res] = run_deterministic_checks("anything", [{"type": "bogus-check"}])
    assert res.score is None
    assert res.passed is None
    assert res.details.get("note") == "unknown check type"


def test_dotted_field_access_on_dict_output():
    output = json.dumps({"output": {"summary": "hello"}})
    checks = [{"type": "exact-match", "field": "output.summary"}]
    ex = _example(expected="hello")
    [res] = run_deterministic_checks(output, checks, ex)
    assert res.passed is True


def test_dotted_field_missing_fails_with_note():
    output = json.dumps({"output": {"summary": "hello"}})
    checks = [{"type": "max-length", "field": "output.nope", "max_chars": 10}]
    [res] = run_deterministic_checks(output, checks)
    assert res.passed is False
    assert "not found" in res.details.get("note", "")
