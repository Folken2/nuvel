"""Tests for nuvel.tools.composio_tools."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from nuvel.tools.composio_tools import _list_toolkits_impl, list_composio_toolkits


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_toolkit_item(slug: str, name: str, description: str) -> MagicMock:
    item = MagicMock()
    item.slug = slug
    item.name = name
    item.description = description
    return item


# ── TestListToolkitsImpl ─────────────────────────────────────────────────


class TestListToolkitsImpl:
    """Unit tests for _list_toolkits_impl (no env vars needed)."""

    @patch("nuvel.tools.composio_tools.Composio")
    def test_returns_toolkits(self, MockComposio):
        items = [
            _make_toolkit_item("github", "GitHub", "GitHub integration"),
            _make_toolkit_item("slack", "Slack", "Slack integration"),
        ]
        mock_response = MagicMock()
        mock_response.items = items
        MockComposio.return_value.toolkits.list.return_value = mock_response

        result = _list_toolkits_impl(api_key="test-key")

        assert result["status"] == "ok"
        assert result["count"] == 2
        assert len(result["toolkits"]) == 2
        assert result["toolkits"][0]["slug"] == "github"
        assert result["toolkits"][1]["slug"] == "slack"

    @patch("nuvel.tools.composio_tools.Composio")
    def test_filters_by_query(self, MockComposio):
        items = [
            _make_toolkit_item("github", "GitHub", "GitHub integration"),
            _make_toolkit_item("slack", "Slack", "Slack integration"),
        ]
        mock_response = MagicMock()
        mock_response.items = items
        MockComposio.return_value.toolkits.list.return_value = mock_response

        result = _list_toolkits_impl(api_key="test-key", query="github")

        assert result["status"] == "ok"
        assert result["count"] == 1
        assert result["toolkits"][0]["slug"] == "github"

    @patch("nuvel.tools.composio_tools.Composio")
    def test_passes_category(self, MockComposio):
        mock_response = MagicMock()
        mock_response.items = []
        MockComposio.return_value.toolkits.list.return_value = mock_response

        _list_toolkits_impl(api_key="test-key", category="communication")

        MockComposio.return_value.toolkits.list.assert_called_once_with(category="communication")

    @patch("nuvel.tools.composio_tools.Composio")
    def test_no_results(self, MockComposio):
        mock_response = MagicMock()
        mock_response.items = []
        MockComposio.return_value.toolkits.list.return_value = mock_response

        result = _list_toolkits_impl(api_key="test-key", query="nonexistent")

        assert result["status"] == "ok"
        assert result["count"] == 0
        assert result["toolkits"] == []

    @patch("nuvel.tools.composio_tools.Composio")
    def test_sdk_error(self, MockComposio):
        MockComposio.side_effect = Exception("connection failed")

        result = _list_toolkits_impl(api_key="test-key")

        assert result["status"] == "error"
        assert "Composio SDK error" in result["message"]


# ── TestListComposioToolkits ─────────────────────────────────────────────


class TestListComposioToolkits:
    """Tests for the wrapper that reads COMPOSIO_API_KEY from env."""

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
        result = list_composio_toolkits()
        assert result["status"] == "error"
        assert "COMPOSIO_API_KEY" in result["message"]

    @patch("nuvel.tools.composio_tools._list_toolkits_impl")
    def test_delegates_to_impl(self, mock_impl, monkeypatch):
        monkeypatch.setenv("COMPOSIO_API_KEY", "my-key")
        mock_impl.return_value = {"status": "ok", "toolkits": [], "count": 0}

        result = list_composio_toolkits(query="slack", category="communication")

        mock_impl.assert_called_once_with(api_key="my-key", query="slack", category="communication")
        assert result["status"] == "ok"
