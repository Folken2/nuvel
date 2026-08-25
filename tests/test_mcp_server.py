"""Tests for the Nuvel Skills MCP server (nuvel.mcp)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nuvel.mcp.server import SkillsMCPServer
from nuvel.mcp.skills_loader import (
    SkillsError,
    SkillsLoader,
    parse_frontmatter,
    resolve_skills_dir,
)


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


def _make_multi_theme_hub(root: Path) -> Path:
    """Create a hub with 3 hr + 3 sales skills for theme-scoping tests."""
    skills = root / "skills"
    themes = {"hr": ["onboarding", "offboarding", "payroll"],
              "sales": ["prospecting", "demo", "closing"]}
    index_themes: dict[str, list[dict]] = {}
    for theme, names in themes.items():
        index_themes[theme] = []
        for name in names:
            skill_dir = skills / theme / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {name}\nversion: 1.0.0\n---\n\n{theme} / {name}.\n",
                encoding="utf-8",
            )
            index_themes[theme].append({
                "name": name,
                "description": f"{theme} skill {name}",
                "author": theme.upper(),
                "path": f"skills/{theme}/{name}/SKILL.md",
            })
    index = {"version": "1.0.0", "themes": index_themes}
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


class TestThemeScoping(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.skills = _make_multi_theme_hub(Path(self._tmp.name))

    def _server(self, theme=None):
        return SkillsMCPServer(SkillsLoader(self.skills), theme=theme)

    def _call(self, server, method, params=None, msg_id=1):
        return server.dispatch(
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
        )

    def _tool(self, server, name, arguments):
        resp = self._call(server, "tools/call", {"name": name, "arguments": arguments})
        self.assertNotIn("error", resp)
        return json.loads(resp["result"]["content"][0]["text"])

    def test_resources_list_scoped_hr(self):
        resp = self._call(self._server(theme="hr"), "resources/list")
        resources = resp["result"]["resources"]
        themes = {r["uri"].split("/")[2] for r in resources}
        self.assertEqual(len(resources), 3)
        self.assertEqual(themes, {"hr"})

    def test_resources_list_scoped_sales(self):
        resp = self._call(self._server(theme="sales"), "resources/list")
        resources = resp["result"]["resources"]
        themes = {r["uri"].split("/")[2] for r in resources}
        self.assertEqual(len(resources), 3)
        self.assertEqual(themes, {"sales"})

    def test_resources_list_unscoped_returns_all(self):
        resp = self._call(self._server(), "resources/list")
        self.assertEqual(len(resp["result"]["resources"]), 6)

    def test_search_skills_scoped(self):
        # "skill" appears in every description; scoping limits the results.
        result = self._tool(self._server(theme="hr"), "search_skills", {"query": "skill"})
        self.assertEqual(result["count"], 3)
        self.assertTrue(all(r["theme"] == "hr" for r in result["results"]))

    def test_get_skill_within_scope(self):
        result = self._tool(self._server(theme="hr"), "get_skill", {"name": "payroll"})
        self.assertEqual(result["theme"], "hr")
        self.assertIn("hr / payroll.", result["content"])

    def test_get_skill_outside_scope_not_found(self):
        resp = self._call(
            self._server(theme="hr"),
            "tools/call",
            {"name": "get_skill", "arguments": {"name": "closing"}},
        )
        self.assertIn("error", resp)

    def test_resources_read_ignores_scope(self):
        # Scoping is about discovery, not access control.
        resp = self._call(
            self._server(theme="hr"), "resources/read", {"uri": "skill://sales/closing"}
        )
        self.assertIn("sales / closing.", resp["result"]["contents"][0]["text"])

    def test_unknown_theme_returns_empty(self):
        resp = self._call(self._server(theme="nope"), "resources/list")
        self.assertEqual(resp["result"]["resources"], [])


def _make_feature_hub(root: Path) -> Path:
    """Create a hub with sections, ``requires``, and template variables."""
    skills = root / "skills"
    (skills / "sales" / "pricing").mkdir(parents=True)
    (skills / "sales" / "pricing" / "SKILL.md").write_text(
        "---\n"
        "name: pricing\n"
        "requires: [api_key, salesforce_connected]\n"
        "---\n\n"
        "# Pricing\n\n"
        "## Overview\n"
        "Summary uses {{ fiscal_year_summary }}.\n\n"
        "## Details\n"
        "Needs {{ api_key }} to run.\n",
        encoding="utf-8",
    )
    index = {
        "themes": {
            "sales": [
                {
                    "name": "pricing",
                    "description": "Pricing guidance",
                    "author": "Sales",
                    "path": "skills/sales/pricing/SKILL.md",
                },
            ]
        }
    }
    (skills / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return skills


class TestParseFrontmatterLists(unittest.TestCase):
    def test_inline_list(self):
        meta = parse_frontmatter("---\nrequires: [a, b]\n---\n")
        self.assertEqual(meta["requires"], ["a", "b"])

    def test_block_list(self):
        meta = parse_frontmatter("---\nrequires:\n  - a\n  - b\n---\n")
        self.assertEqual(meta["requires"], ["a", "b"])

    def test_scalar_still_string(self):
        meta = parse_frontmatter("---\nversion: 1.0.0\n---\n")
        self.assertEqual(meta["version"], "1.0.0")


class TestAirbyteFeatures(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.skills = _make_feature_hub(Path(self._tmp.name))
        self.server = SkillsMCPServer(SkillsLoader(self.skills))

    def _tool(self, name, arguments):
        resp = self.server.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        self.assertNotIn("error", resp)
        return json.loads(resp["result"]["content"][0]["text"])

    def test_get_skill_section(self):
        result = self._tool("get_skill", {"name": "pricing", "section": "Overview"})
        self.assertEqual(result["section"], "Overview")
        self.assertIn("fiscal_year_summary", result["content"])
        self.assertNotIn("Details", result["content"])

    def test_get_skill_unknown_section_errors(self):
        resp = self.server.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_skill", "arguments": {"name": "pricing", "section": "Nope"}},
            }
        )
        self.assertIn("error", resp)

    def test_get_skill_template_variables(self):
        result = self._tool("get_skill", {"name": "pricing"})
        self.assertEqual(
            set(result["metadata"]["template_variables"]),
            {"fiscal_year_summary", "api_key"},
        )

    def test_fence_value(self):
        result = self._tool(
            "fence_value",
            {"value": "ACV in Incremental_ACV__c", "source": "machine-discovered"},
        )
        self.assertEqual(
            result["value"],
            "[begin customer-specific data (machine-discovered, unverified)]\n"
            "ACV in Incremental_ACV__c\n"
            "[end customer-specific data]",
        )

    def test_fence_value_default_source(self):
        result = self._tool("fence_value", {"value": "x"})
        self.assertIn("machine-discovered", result["value"])

    def test_require_filter(self):
        gated = SkillsMCPServer(
            SkillsLoader(self.skills),
            require_filter=["api_key", "salesforce_connected"],
        )
        resp = gated.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}}
        )
        self.assertEqual(len(resp["result"]["resources"]), 1)

    def test_require_filter_hides_unmet(self):
        partial = SkillsMCPServer(
            SkillsLoader(self.skills),
            require_filter=["api_key", "unknown_capability"],
        )
        resp = partial.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}}
        )
        self.assertEqual(resp["result"]["resources"], [])


if __name__ == "__main__":
    unittest.main()
