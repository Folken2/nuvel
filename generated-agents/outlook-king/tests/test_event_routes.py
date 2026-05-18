"""
Tests for the JSON-manifest-only event hook routes.

Covers:
  - /api/outlook/compose-opened persists the snapshot under outlook:compose_draft
  - /api/outlook/pre-send-check blocks when the body mentions an attachment
    and none is present (missing-attachment heuristic)
  - /api/outlook/pre-send-check allows happy-path sends
  - /api/outlook/report-spam logs the report and returns ack
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

# Backend lifespan reads DATABASE_URL — point it at the Neon `test` branch
# via TEST_DATABASE_URL from .env.test. The dependency override below
# bypasses upsert_user so these route tests don't write to the users table.
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
    APP_NAME,
    get_user_id,
)
from outlook_king.tools.outlook_context import COMPOSE_DRAFT_KEY, SPAM_REPORTS_KEY  # noqa: E402

# Stable test user_id. The route handlers don't care that it's not in the
# users table — they just store it on the ADK session.
TEST_USER_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def client():
    app.dependency_overrides[get_user_id] = lambda: TEST_USER_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_compose_opened_persists_draft(client: TestClient):
    session_id = "test-compose-opened"
    resp = client.post(
        "/api/outlook/compose-opened",
        json={
            "session_id": session_id,
            "compose_type": "reply",
            "compose": {
                "body": "Hi — see attached.",
                "subject": "Re: Proposal",
                "to": ["anna@example.com"],
            },
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    import asyncio
    session = asyncio.get_event_loop().run_until_complete(
        session_service.get_session(
            app_name=APP_NAME, user_id=TEST_USER_ID, session_id=session_id
        )
    )
    assert session is not None
    stored = session.state.get(COMPOSE_DRAFT_KEY)
    assert stored is not None
    assert stored["compose_type"] == "reply"
    assert stored["subject"] == "Re: Proposal"
    assert stored["to"] == ["anna@example.com"]


def test_pre_send_check_blocks_missing_attachment(client: TestClient):
    resp = client.post(
        "/api/outlook/pre-send-check",
        json={
            "session_id": "test-presend-block",
            "compose": {
                "body": "Hey — please find the report attached. Thanks!",
                "subject": "Report",
                "to": ["anna@example.com"],
                "attachments": [],
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["allow"] is False
    assert "attach" in data["message"].lower()


def test_pre_send_check_allows_when_attached(client: TestClient):
    resp = client.post(
        "/api/outlook/pre-send-check",
        json={
            "session_id": "test-presend-ok-att",
            "compose": {
                "body": "Hey — please find the report attached.",
                "subject": "Report",
                "to": ["anna@example.com"],
                "attachments": [{"name": "report.pdf", "size": 1024}],
            },
        },
    )
    assert resp.status_code == 200
    assert resp.json()["allow"] is True


def test_pre_send_check_allows_no_attachment_mention(client: TestClient):
    resp = client.post(
        "/api/outlook/pre-send-check",
        json={
            "session_id": "test-presend-ok-plain",
            "compose": {
                "body": "Quick note: meeting moved to 3pm.",
                "subject": "Reschedule",
                "to": ["anna@example.com"],
                "attachments": [],
            },
        },
    )
    assert resp.status_code == 200
    assert resp.json()["allow"] is True


def test_pre_send_check_allows_empty_body(client: TestClient):
    resp = client.post(
        "/api/outlook/pre-send-check",
        json={"session_id": "test-presend-empty", "compose": {"body": ""}},
    )
    assert resp.status_code == 200
    assert resp.json()["allow"] is True


def test_report_spam_logs_metadata(client: TestClient):
    session_id = "test-spam-report"
    resp = client.post(
        "/api/outlook/report-spam",
        json={
            "session_id": session_id,
            "message_id": "AAMk-123",
            "subject": "You won!",
            "sender": "evil@bad.example",
            "options": [0, 1],
            "free_text": "looks phishy",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    import asyncio
    session = asyncio.get_event_loop().run_until_complete(
        session_service.get_session(
            app_name=APP_NAME, user_id=TEST_USER_ID, session_id=session_id
        )
    )
    assert session is not None
    reports = session.state.get(SPAM_REPORTS_KEY) or []
    assert len(reports) == 1
    assert reports[0]["subject"] == "You won!"
    assert reports[0]["sender"] == "evil@bad.example"
