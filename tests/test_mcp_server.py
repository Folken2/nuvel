"""Tests for the Nuvel Skills MCP server (nuvel.mcp)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nuvel.mcp.server import SkillsMCPServer
from nuvel.mcp.skills_loader import SkillsError, SkillsLoader, resolve_skills_dir


def _make_hub(root: Path) -> Path:
    """Create a minimal skills-hub layout under ``root/skills`` and return it."""
    skills = root / "skills"
    (skills / "hr" / "onboarding").mkdir(parents=True)
    (skills / "hr" / "onboarding" / "SKILL.md").write_text(
        "---\nname: onboarding\nversion: 1.0.0\n---\n\nWelcome aboard.\n",
        encoding="utf-8",
    )
    index = {
        "version": "1.0.0",
        "themes": {
            "hr": [
                {
                    "name": "onboarding",
                    "description": "Employee onboarding checklist",
                    "author": "HR",
                    "path": "skills/hr/onboarding/SKILL.md",
                },
                # A dependency entry whose SKILL.md is not vendored locally.
                {
                    "name": "offboarding",
                    "description": "Employee offboarding",
                    "dependency": True,
                    "path": "skills/hr/offboarding/SKILL.md",
                },
            ]
        },
    }
    (skills / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return skills


class TestResolveSkillsDir(unittest.TestCase):
    def test_finds_skills_subdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = _make_hub(root)
            self.assertEqual(resolve_skills_dir(root), skills.resolve())

    def test_accepts_skills_dir_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills = _make_hub(Path(tmp))
            self.assertEqual(resolve_skills_dir(skills), skills.resolve())

    def test_missing_index_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SkillsError):
                resolve_skills_dir(tmp)


class TestServerMethods(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.skills = _make_hub(Path(self._tmp.name))
        self.server = SkillsMCPServer(SkillsLoader(self.skills))

    def _call(self, method, params=None, msg_id=1):
        return self.server.dispatch(
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
        )

    def _tool(self, name, arguments):
        resp = self._call("tools/call", {"name": name, "arguments": arguments})
        self.assertNotIn("error", resp)
        return json.loads(resp["result"]["content"][0]["text"])

    def test_initialize(self):
        resp = self._call("initialize")
        self.assertEqual(resp["result"]["serverInfo"]["name"], "nuvel-skills")

    def test_resources_list(self):
        resp = self._call("resources/list")
        uris = {r["uri"] for r in resp["result"]["resources"]}
        self.assertIn("skill://hr/onboarding", uris)
        self.assertIn("skill://hr/offboarding", uris)

    def test_resources_read_strips_skills_prefix(self):
        # Regression: repo-root-relative path must not double the "skills/" prefix.
        resp = self._call("resources/read", {"uri": "skill://hr/onboarding"})
        text = resp["result"]["contents"][0]["text"]
        self.assertIn("Welcome aboard.", text)

    def test_resources_read_missing_file(self):
        resp = self._call("resources/read", {"uri": "skill://hr/offboarding"})
        self.assertIn("error", resp)

    def test_resources_read_unknown_skill(self):
        resp = self._call("resources/read", {"uri": "skill://hr/nope"})
        self.assertIn("error", resp)

    def test_search_skills(self):
        result = self._tool("search_skills", {"query": "onboarding"})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["name"], "onboarding")

    def test_get_skill_includes_frontmatter(self):
        result = self._tool("get_skill", {"name": "onboarding"})
        self.assertEqual(result["metadata"]["version"], "1.0.0")
        self.assertIn("Welcome aboard.", result["content"])

    def test_get_skill_theme_qualified(self):
        result = self._tool("get_skill", {"name": "hr/onboarding"})
        self.assertEqual(result["theme"], "hr")

    def test_propose_improvement_without_token_logs(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GITHUB_TOKEN", None)
            result = self._tool(
                "propose_improvement",
                {
                    "skill_name": "onboarding",
                    "current_version": "1.0.0",
                    "issue": "step 2 is stale",
                    "suggested_fix": "update the link",
                    "harness": "unittest",
                },
            )
        self.assertEqual(result["status"], "logged")
        self.assertIn("onboarding", result["title"])

    def test_propose_improvement_missing_args(self):
        resp = self._call(
            "tools/call",
            {"name": "propose_improvement", "arguments": {"skill_name": "x"}},
        )
        self.assertIn("error", resp)

    def test_unknown_method(self):
        resp = self._call("does/not/exist")
        self.assertEqual(resp["error"]["code"], -32601)

    def test_notification_gets_no_response(self):
        # A message without an id (notification) must not produce a response.
        self.assertIsNone(
            self.server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )


if __name__ == "__main__":
    unittest.main()
