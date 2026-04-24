"""Tests for meta_agent.config — skill/tool env-var filtering."""

from __future__ import annotations

import os
import pathlib
import unittest
from unittest import mock

from meta_agent.config import (
    get_skills_dir,
    is_skill_enabled,
    is_tool_disabled,
)


class TestIsSkillEnabled(unittest.TestCase):
    def test_default_all_enabled(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("META_AGENT_SKILLS", None)
            self.assertTrue(is_skill_enabled("adk-streaming"))

    def test_wildcard_enables_all(self):
        with mock.patch.dict(os.environ, {"META_AGENT_SKILLS": "*"}):
            self.assertTrue(is_skill_enabled("anything"))

    def test_empty_string_enables_all(self):
        with mock.patch.dict(os.environ, {"META_AGENT_SKILLS": ""}):
            self.assertTrue(is_skill_enabled("anything"))

    def test_allowlist_includes(self):
        with mock.patch.dict(
            os.environ, {"META_AGENT_SKILLS": "adk-streaming,adk-agent-patterns"}
        ):
            self.assertTrue(is_skill_enabled("adk-streaming"))
            self.assertTrue(is_skill_enabled("adk-agent-patterns"))

    def test_allowlist_excludes(self):
        with mock.patch.dict(os.environ, {"META_AGENT_SKILLS": "adk-streaming"}):
            self.assertFalse(is_skill_enabled("adk-agent-patterns"))

    def test_allowlist_trims_whitespace(self):
        with mock.patch.dict(
            os.environ, {"META_AGENT_SKILLS": "  adk-streaming , adk-tool-creation  "}
        ):
            self.assertTrue(is_skill_enabled("adk-streaming"))
            self.assertTrue(is_skill_enabled("adk-tool-creation"))


class TestIsToolDisabled(unittest.TestCase):
    def test_default_none_disabled(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("META_AGENT_DISABLED_TOOLS", None)
            self.assertFalse(is_tool_disabled("scaffold_agent_tool"))

    def test_denylist_disables(self):
        with mock.patch.dict(
            os.environ,
            {"META_AGENT_DISABLED_TOOLS": "list_composio_toolkits_tool,install_skill_tool"},
        ):
            self.assertTrue(is_tool_disabled("list_composio_toolkits_tool"))
            self.assertTrue(is_tool_disabled("install_skill_tool"))
            self.assertFalse(is_tool_disabled("scaffold_agent_tool"))

    def test_empty_string_disables_nothing(self):
        with mock.patch.dict(os.environ, {"META_AGENT_DISABLED_TOOLS": ""}):
            self.assertFalse(is_tool_disabled("anything"))


class TestGetSkillsDir(unittest.TestCase):
    def test_default_used_when_unset(self):
        default = pathlib.Path("/tmp/default-skills")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("META_AGENT_SKILLS_DIR", None)
            self.assertEqual(get_skills_dir(default), default)

    def test_override_resolves_absolute(self):
        with mock.patch.dict(os.environ, {"META_AGENT_SKILLS_DIR": "/tmp/custom"}):
            result = get_skills_dir(pathlib.Path("/tmp/default"))
            self.assertEqual(result, pathlib.Path("/tmp/custom").resolve())

    def test_empty_override_falls_back_to_default(self):
        default = pathlib.Path("/tmp/default-skills")
        with mock.patch.dict(os.environ, {"META_AGENT_SKILLS_DIR": "   "}):
            self.assertEqual(get_skills_dir(default), default)


class TestToolFiltering(unittest.TestCase):
    """Integration: get_tools() respects META_AGENT_DISABLED_TOOLS."""

    def test_all_tools_by_default(self):
        from meta_agent.tools import _ALL_TOOLS, get_tools

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("META_AGENT_DISABLED_TOOLS", None)
            self.assertEqual(len(get_tools()), len(_ALL_TOOLS))

    def test_disabled_tools_removed(self):
        from meta_agent.tools import _ALL_TOOLS, get_tools

        with mock.patch.dict(
            os.environ, {"META_AGENT_DISABLED_TOOLS": "list_composio_toolkits_tool"}
        ):
            tools = get_tools()
            self.assertEqual(len(tools), len(_ALL_TOOLS) - 1)


if __name__ == "__main__":
    unittest.main()
