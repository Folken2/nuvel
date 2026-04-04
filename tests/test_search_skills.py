"""Tests for the search_skills tool and its helpers."""

from __future__ import annotations

from unittest.mock import patch
from urllib.error import URLError

import pytest

from meta_agent.tools.skills_tools import (
    _parse_search_response,
    search_skills,
)

SAMPLE_API_RESPONSE = {
    "query": "kubernetes",
    "searchType": "fuzzy",
    "skills": [
        {
            "id": "microsoft/azure-skills/azure-kubernetes",
            "skillId": "azure-kubernetes",
            "name": "azure-kubernetes",
            "installs": 17541,
            "source": "microsoft/azure-skills",
        },
        {
            "id": "jeffallan/claude-skills/kubernetes-specialist",
            "skillId": "kubernetes-specialist",
            "name": "kubernetes-specialist",
            "installs": 5117,
            "source": "jeffallan/claude-skills",
        },
        {
            "id": "sickn33/skills/kubernetes-architect",
            "skillId": "kubernetes-architect",
            "name": "kubernetes-architect",
            "installs": 385,
            "source": "sickn33/skills",
        },
        {
            "id": "tiny/skills/k8s",
            "skillId": "k8s",
            "name": "k8s",
            "installs": 50,
            "source": "tiny/skills",
        },
    ],
    "count": 4,
    "duration_ms": 34,
}


# ── TestParseSearchResponse ─────────────────────────────────────────


class TestParseSearchResponse:
    def test_filters_by_min_installs(self):
        results = _parse_search_response(SAMPLE_API_RESPONSE)
        assert len(results) == 2

    def test_includes_package_field(self):
        results = _parse_search_response(SAMPLE_API_RESPONSE)
        assert results[0]["package"] == "microsoft/azure-skills@azure-kubernetes"

    def test_sorted_by_installs_desc(self):
        results = _parse_search_response(SAMPLE_API_RESPONSE)
        assert results[0]["installs"] > results[1]["installs"]

    def test_empty_response(self):
        data = {"query": "nonexistent", "skills": [], "count": 0}
        results = _parse_search_response(data)
        assert results == []


# ── TestSearchSkills ─────────────────────────────────────────────────


class TestSearchSkills:
    @patch("meta_agent.tools.skills_tools._fetch_search_api")
    def test_returns_filtered_results(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_API_RESPONSE
        result = search_skills(query="kubernetes")
        assert result["status"] == "ok"
        assert len(result["skills"]) == 2
        assert result["query"] == "kubernetes"

    @patch("meta_agent.tools.skills_tools._fetch_search_api")
    def test_no_results(self, mock_fetch):
        mock_fetch.return_value = {"query": "nonexistent", "skills": [], "count": 0}
        result = search_skills(query="nonexistent")
        assert result["status"] == "ok"
        assert len(result["skills"]) == 0
        assert "no skills found" in result["message"].lower()

    @patch("meta_agent.tools.skills_tools._fetch_search_api")
    def test_api_error(self, mock_fetch):
        mock_fetch.side_effect = URLError("Connection refused")
        result = search_skills(query="kubernetes")
        assert result["status"] == "error"
        assert "skills.sh API error" in result["message"]
