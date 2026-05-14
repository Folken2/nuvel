"""
Tests for the JSON-manifest-driven document-opened backend route.

The route accepts an early-context snapshot pushed by the add-in when
a Word document opens (or when the taskpane first boots). State must
land under ``word:opened_document`` so the agent's
``get_opened_document_snapshot`` tool can surface it.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Ensure the API-key middleware doesn't block tests.
import os
os.environ.pop("BACKEND_API_KEY", None)

from backend.main import app, APP_NAME, session_service  # noqa: E402
from word_king.tools.word_context import OPENED_DOCUMENT_KEY  # noqa: E402

client = TestClient(app)


def _payload(is_new: bool = False) -> dict:
    return {
        "session_id": f"test-{uuid.uuid4().hex[:8]}",
        "user_id": "test-user",
        "is_new": is_new,
        "snapshot": {
            "title": "Quarterly Report",
            "word_count": 1234,
            "paragraph_count": 42,
            "headings": [
                {"text": "Overview", "level": 1, "index": 0},
                {"text": "Results", "level": 1, "index": 10},
            ],
        },
    }


def test_document_opened_happy_path():
    body = _payload(is_new=False)
    res = client.post("/api/word/document-opened", json=body)
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "is_new": False}


def test_document_opened_persists_state():
    body = _payload(is_new=True)
    res = client.post("/api/word/document-opened", json=body)
    assert res.status_code == 200

    import asyncio

    session = asyncio.run(
        session_service.get_session(
            app_name=APP_NAME, user_id="test-user", session_id=body["session_id"]
        )
    )
    assert session is not None
    stashed = session.state.get(OPENED_DOCUMENT_KEY)
    assert stashed is not None
    assert stashed["is_new"] is True
    assert stashed["snapshot"]["title"] == "Quarterly Report"
    assert stashed["snapshot"]["word_count"] == 1234
    assert len(stashed["snapshot"]["headings"]) == 2
    assert "received_at" in stashed


def test_document_opened_rejects_missing_snapshot():
    res = client.post(
        "/api/word/document-opened",
        json={"session_id": "x", "user_id": "test-user", "is_new": False},
    )
    assert res.status_code == 422


def test_document_opened_rejects_malformed_headings():
    res = client.post(
        "/api/word/document-opened",
        json={
            "session_id": "x",
            "user_id": "test-user",
            "is_new": False,
            "snapshot": {
                "title": "x",
                "word_count": 1,
                "paragraph_count": 1,
                "headings": [{"text": "x"}],  # missing level/index
            },
        },
    )
    assert res.status_code == 422


def test_document_opened_defaults_user_id():
    body = _payload()
    body.pop("user_id")
    res = client.post("/api/word/document-opened", json=body)
    assert res.status_code == 200


def test_get_opened_document_snapshot_tool_no_snapshot():
    from word_king.tools.word_context import get_opened_document_snapshot

    class FakeCtx:
        def __init__(self):
            self.state: dict = {}

    out = get_opened_document_snapshot(FakeCtx())
    assert out["status"] == "no_snapshot"


def test_get_opened_document_snapshot_tool_returns_payload():
    from word_king.tools.word_context import get_opened_document_snapshot

    class FakeCtx:
        def __init__(self):
            self.state: dict = {
                OPENED_DOCUMENT_KEY: {
                    "is_new": True,
                    "snapshot": {"title": "x", "word_count": 0, "paragraph_count": 0, "headings": []},
                    "received_at": "2026-01-01T00:00:00+00:00",
                }
            }

    out = get_opened_document_snapshot(FakeCtx())
    assert out["status"] == "ok"
    assert out["is_new"] is True
    assert out["snapshot"]["title"] == "x"
