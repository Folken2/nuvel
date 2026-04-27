"""Tests for install_skill and read_skill_context tools."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from nuvel.tools.skills_tools import (
    _check_installs,
    install_skill,
    read_skill_context,
)


# ── TestCheckInstalls ───────────────────────────────────────────────


class TestCheckInstalls(unittest.TestCase):
    """Tests for _check_installs (mock _fetch_search_api)."""

    @patch("nuvel.tools.skills_tools._fetch_search_api")
    def test_above_threshold(self, mock_fetch):
        mock_fetch.return_value = {
            "skills": [
                {
                    "skillId": "my-skill",
                    "name": "my-skill",
                    "installs": 5000,
                    "source": "owner/repo",
                }
            ]
        }
        self.assertTrue(_check_installs("owner/repo@my-skill"))

    @patch("nuvel.tools.skills_tools._fetch_search_api")
    def test_below_threshold(self, mock_fetch):
        mock_fetch.return_value = {
            "skills": [
                {
                    "skillId": "my-skill",
                    "name": "my-skill",
                    "installs": 500,
                    "source": "owner/repo",
                }
            ]
        }
        self.assertFalse(_check_installs("owner/repo@my-skill"))

    @patch("nuvel.tools.skills_tools._fetch_search_api")
    def test_not_found(self, mock_fetch):
        mock_fetch.return_value = {"skills": []}
        self.assertFalse(_check_installs("owner/repo@my-skill"))


# ── TestInstallSkill ────────────────────────────────────────────────


class TestInstallSkill(unittest.TestCase):
    """Tests for install_skill."""

    def setUp(self):
        self._dirs: list[str] = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _make_skill_dir(self) -> str:
        """Create a temporary skill directory with SKILL.md."""
        base = tempfile.mkdtemp()
        self._dirs.append(base)
        skill_dir = os.path.join(base, "my-skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write(
                "---\nname: my-skill\ndescription: A test skill\n---\n"
                "Instructions here."
            )
        return skill_dir

    @patch("nuvel.tools.skills_tools._check_installs")
    def test_rejects_below_threshold(self, mock_check):
        mock_check.return_value = False
        result = install_skill(package="owner/repo@my-skill", agent_name="test-agent")
        self.assertEqual(result["status"], "error")
        self.assertIn("1,000", result["message"])

    @patch("nuvel.tools.skills_tools._download_skill")
    @patch("nuvel.tools.skills_tools._check_installs")
    def test_install_success(self, mock_check, mock_download):
        mock_check.return_value = True
        skill_dir = self._make_skill_dir()
        mock_download.return_value = skill_dir

        # Create a temp output directory
        output_dir = tempfile.mkdtemp()
        self._dirs.append(output_dir)

        tool_context = MagicMock()
        tool_context.state = {
            "agent_output_dir": output_dir,
            "agent_package": "test_agent",
        }

        result = install_skill(
            package="owner/repo@my-skill",
            agent_name="test-agent",
            tool_context=tool_context,
        )

        self.assertEqual(result["status"], "ok")
        # Verify skill was copied to the right place
        expected_path = os.path.join(
            output_dir, "test-agent", "test_agent", "skills", "my-skill"
        )
        self.assertTrue(os.path.isdir(expected_path))
        self.assertTrue(
            os.path.isfile(os.path.join(expected_path, "SKILL.md"))
        )


# ── TestReadSkillContext ────────────────────────────────────────────


class TestReadSkillContext(unittest.TestCase):
    """Tests for read_skill_context."""

    def setUp(self):
        self._dirs: list[str] = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    @patch("nuvel.tools.skills_tools._check_installs")
    def test_rejects_below_threshold(self, mock_check):
        mock_check.return_value = False
        result = read_skill_context(package="owner/repo@my-skill")
        self.assertEqual(result["status"], "error")
        self.assertIn("1,000", result["message"])

    @patch("nuvel.tools.skills_tools._download_skill")
    @patch("nuvel.tools.skills_tools._check_installs")
    def test_returns_content(self, mock_check, mock_download):
        mock_check.return_value = True

        # Create a temp skill dir with SKILL.md and references/guide.md
        base = tempfile.mkdtemp()
        self._dirs.append(base)
        skill_dir = os.path.join(base, "my-skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: my-skill\ndescription: test\n---\nMain content.")
        refs_dir = os.path.join(skill_dir, "references")
        os.makedirs(refs_dir)
        with open(os.path.join(refs_dir, "guide.md"), "w") as f:
            f.write("Guide content here.")

        mock_download.return_value = skill_dir

        result = read_skill_context(package="owner/repo@my-skill")
        self.assertEqual(result["status"], "ok")
        self.assertIn("Main content", result["skill_md"])
        self.assertIn("guide.md", result["references"])
        self.assertEqual(result["references"]["guide.md"], "Guide content here.")
        self.assertEqual(result["package"], "owner/repo@my-skill")


if __name__ == "__main__":
    unittest.main()
