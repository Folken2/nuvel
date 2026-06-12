"""
Tests for POST /api/outlook/attachment-content.

Follows the test_event_routes.py pattern: dependency-override the user
resolver, exercise the route with TestClient, then inspect the in-memory
session state + artifact service directly.
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_ENV_TEST = ROOT / ".env.test"
if _ENV_TEST.is_file():
    load_dotenv(_ENV_TEST, override=False)
if "DATABASE_URL" not in os.environ and "TEST_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

from backend.main import (  # noqa: E402
    app,
    session_service,
    artifact_service,
    APP_NAME,
    get_user_id,
)
from outlook_king.tools.attachment_tools import FETCHED_ATTACHMENTS_KEY  # noqa: E402

TEST_USER_ID = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def client():
    app.dependency_overrides[get_user_id] = lambda: TEST_USER_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_attachment_content_stores_artifact_and_index(client: TestClient):
    session_id = "test-attach-csv"
    payload = base64.b64encode(b"item,cost\nserver,120\n").decode()
    resp = client.post(
        "/api/outlook/attachment-content",
        json={
            "session_id": session_id,
            "attachment_id": "att-1",
            "name": "budget.csv",
            "content_type": "text/csv",
            "format": "base64",
            "content": payload,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["kind"] == "text"
    assert data["text_chars"] > 0

    session = _run(
        session_service.get_session(
            app_name=APP_NAME, user_id=TEST_USER_ID, session_id=session_id
        )
    )
    fetched = session.state.get(FETCHED_ATTACHMENTS_KEY) or {}
    assert "budget.csv" in fetched
    entry = fetched["budget.csv"]
    assert entry["kind"] == "text"
    assert entry["artifact"] == "attachment:budget.csv"
    assert entry["text_artifact"] == "attachment_text:budget.csv"

    text_part = _run(
        artifact_service.load_artifact(
            app_name=APP_NAME,
            user_id=TEST_USER_ID,
            session_id=session_id,
            filename="attachment_text:budget.csv",
        )
    )
    assert text_part is not None
    assert "server,120" in text_part.text

    raw_part = _run(
        artifact_service.load_artifact(
            app_name=APP_NAME,
            user_id=TEST_USER_ID,
            session_id=session_id,
            filename="attachment:budget.csv",
        )
    )
    assert raw_part is not None


def test_attachment_content_image_skips_text_extraction(client: TestClient):
    session_id = "test-attach-img"
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\nfakepixels").decode()
    resp = client.post(
        "/api/outlook/attachment-content",
        json={
            "session_id": session_id,
            "attachment_id": "att-2",
            "name": "screenshot.png",
            "content_type": "image/png",
            "format": "base64",
            "content": payload,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "image"
    assert data["text_chars"] == 0

    session = _run(
        session_service.get_session(
            app_name=APP_NAME, user_id=TEST_USER_ID, session_id=session_id
        )
    )
    entry = (session.state.get(FETCHED_ATTACHMENTS_KEY) or {})["screenshot.png"]
    assert entry["text_artifact"] is None


def test_attachment_content_rejects_cloud_url(client: TestClient):
    resp = client.post(
        "/api/outlook/attachment-content",
        json={
            "session_id": "test-attach-url",
            "attachment_id": "att-3",
            "name": "doc.docx",
            "format": "url",
            "content": "https://contoso-my.sharepoint.com/doc.docx",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "cloud_attachment"


def test_attachment_content_rejects_invalid_base64(client: TestClient):
    resp = client.post(
        "/api/outlook/attachment-content",
        json={
            "session_id": "test-attach-bad",
            "attachment_id": "att-4",
            "name": "x.pdf",
            "format": "base64",
            "content": "%%% not base64 %%%",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "bad_content"
