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

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.main import (
    app,
    session_service,
    APP_NAME,
    DEFAULT_USER_ID,
)
from outlook_king.tools.outlook_context import COMPOSE_DRAFT_KEY, SPAM_REPORTS_KEY


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


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
            app_name=APP_NAME, user_id=DEFAULT_USER_ID, session_id=session_id
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
            app_name=APP_NAME, user_id=DEFAULT_USER_ID, session_id=session_id
        )
    )
    assert session is not None
    reports = session.state.get(SPAM_REPORTS_KEY) or []
    assert len(reports) == 1
    assert reports[0]["subject"] == "You won!"
    assert reports[0]["sender"] == "evil@bad.example"
