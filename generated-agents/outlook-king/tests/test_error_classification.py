"""
Tests for backend error classification.

Raw exception strings must never reach the add-in. ``classify_agent_error``
maps provider/db failures to stable codes + user-safe messages, and
``_agent_error_detail`` is the single place chat/stream endpoints get
their error payload from.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.main import classify_agent_error, _agent_error_detail  # noqa: E402


class RateLimitError(Exception):
    """Mimics litellm/openai RateLimitError by class name."""


class APIConnectionError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class FakePsycopgError(Exception):
    pass


FakePsycopgError.__module__ = "psycopg.errors"


def _chained(inner: Exception) -> Exception:
    try:
        try:
            raise inner
        except Exception as e:
            raise RuntimeError("agent run failed") from e
    except Exception as e:
        return e


def test_rate_limit_maps_to_llm_rate_limited():
    status, code, _ = classify_agent_error(RateLimitError("429 rate limit exceeded"))
    assert (status, code) == (503, "llm_rate_limited")


def test_cause_chain_is_walked():
    status, code, _ = classify_agent_error(_chained(RateLimitError("rate limit")))
    assert (status, code) == (503, "llm_rate_limited")


def test_auth_failure_maps_to_llm_auth_failed():
    status, code, _ = classify_agent_error(AuthenticationError("invalid api key"))
    assert (status, code) == (502, "llm_auth_failed")


def test_timeout_maps_to_upstream_timeout():
    status, code, _ = classify_agent_error(TimeoutError("read timed out"))
    assert (status, code) == (504, "upstream_timeout")


def test_connection_failure_maps_to_upstream_unavailable():
    status, code, _ = classify_agent_error(APIConnectionError("connection refused"))
    assert (status, code) == (503, "upstream_unavailable")


def test_psycopg_errors_map_to_memory_unavailable():
    status, code, _ = classify_agent_error(
        _chained(FakePsycopgError("connection to server failed"))
    )
    assert (status, code) == (503, "memory_unavailable")


def test_unknown_error_message_does_not_leak():
    exc = ValueError("postgresql://user:hunter2@db.internal/prod exploded")
    status, code, message = classify_agent_error(exc)
    assert (status, code) == (500, "internal_error")
    assert "hunter2" not in message
    assert "postgresql" not in message


def test_agent_error_detail_passes_structured_http_exception_through():
    exc = HTTPException(503, {"code": "session_unavailable", "message": "Try again."})
    status, detail = _agent_error_detail(exc)
    assert status == 503
    assert detail == {"code": "session_unavailable", "message": "Try again."}


def test_agent_error_detail_wraps_plain_http_exception():
    status, detail = _agent_error_detail(HTTPException(400, "Empty prompt."))
    assert status == 400
    assert detail["code"] == "internal_error"
    assert detail["message"] == "Empty prompt."


def test_agent_error_detail_classifies_plain_exceptions():
    status, detail = _agent_error_detail(RateLimitError("rate limit"))
    assert status == 503
    assert detail["code"] == "llm_rate_limited"
    assert detail["message"]  # user-safe message, never the raw exception
    assert "RateLimitError" not in detail["message"]
