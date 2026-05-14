"""
Tests for the early-open snapshot route ``POST /api/ppt/presentation-opened``.

This route is the taskpane's stand-in for a PowerPoint ``OnDocumentOpened``
event — the unified JSON manifest lists that event as "Not yet supported"
for PowerPoint, so the taskpane fires this from a useEffect on first mount.

Tests cover:
  - happy path: payload writes through to ADK session state under
    ``ppt:opened_presentation`` and is readable by
    ``get_opened_presentation_snapshot``.
  - malformed payload: empty title + zero slides + no titles is rejected.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.main import (  # noqa: E402
    APP_NAME,
    DEFAULT_USER_ID,
    app,
    session_service,
)
from ppt_king.tools.ppt_context import (  # noqa: E402
    OPENED_PRESENTATION_KEY,
    get_opened_presentation_snapshot,
)


class _FakeCtx:
    def __init__(self, state: dict) -> None:
        self.state = state


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _get_state(session_id: str) -> dict:
    """Pull session state via the real async session service from sync test."""
    import asyncio

    async def _load() -> dict:
        session = await session_service.get_session(
            app_name=APP_NAME, user_id=DEFAULT_USER_ID, session_id=session_id
        )
        return dict(session.state) if session else {}

    return asyncio.get_event_loop().run_until_complete(_load())


def test_presentation_opened_happy_path(client: TestClient) -> None:
    session_id = "test-open-happy"
    res = client.post(
        "/api/ppt/presentation-opened",
        json={
            "session_id": session_id,
            "title": "Q3 review",
            "slide_count": 4,
            "slide_titles": ["Agenda", "Wins", "Risks", "Ask"],
            "is_new": False,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert body["snapshot"]["title"] == "Q3 review"
    assert body["snapshot"]["slide_count"] == 4
    assert body["snapshot"]["slide_titles"] == ["Agenda", "Wins", "Risks", "Ask"]
    assert body["snapshot"]["is_new"] is False
    assert body["snapshot"]["opened_at"].endswith("Z")

    state = _get_state(session_id)
    payload = state.get(OPENED_PRESENTATION_KEY)
    assert payload is not None
    assert payload["title"] == "Q3 review"
    assert payload["slide_count"] == 4

    # And the agent-side tool surfaces it cleanly.
    tool_res = get_opened_presentation_snapshot(_FakeCtx(state))
    assert tool_res["status"] == "ok"
    assert tool_res["title"] == "Q3 review"
    assert tool_res["slide_count"] == 4


def test_presentation_opened_new_blank_deck(client: TestClient) -> None:
    session_id = "test-open-new"
    res = client.post(
        "/api/ppt/presentation-opened",
        json={
            "session_id": session_id,
            "title": "Untitled deck",
            "slide_count": 1,
            "slide_titles": [""],
            "is_new": True,
        },
    )
    assert res.status_code == 200
    assert res.json()["snapshot"]["is_new"] is True


def test_presentation_opened_rejects_empty_payload(client: TestClient) -> None:
    res = client.post(
        "/api/ppt/presentation-opened",
        json={
            "session_id": "test-open-empty",
            "title": "   ",
            "slide_count": 0,
            "slide_titles": [],
        },
    )
    assert res.status_code == 400


def test_presentation_opened_rejects_missing_session_id(client: TestClient) -> None:
    res = client.post(
        "/api/ppt/presentation-opened",
        json={"title": "Deck", "slide_count": 2, "slide_titles": ["a", "b"]},
    )
    assert res.status_code == 422


def test_get_opened_presentation_snapshot_no_state() -> None:
    res = get_opened_presentation_snapshot(_FakeCtx({}))
    assert res["status"] == "no_snapshot"
