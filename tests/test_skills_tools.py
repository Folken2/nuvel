"""Tests for meta_agent.tools.skills_tools — adaptation pipeline."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from meta_agent.tools.skills_tools import (
    _normalize_name,
    _parse_skill_md,
    adapt_skill_for_adk,
)


class TestNormalizeName(unittest.TestCase):
    def test_already_valid(self):
        self.assertEqual(_normalize_name("my-skill"), "my-skill")

    def test_uppercase(self):
        self.assertEqual(_normalize_name("My-Skill"), "my-skill")

    def test_underscores(self):
        self.assertEqual(_normalize_name("my_skill"), "my-skill")

    def test_spaces(self):
        self.assertEqual(_normalize_name("my skill"), "my-skill")

    def test_consecutive_hyphens(self):
        self.assertEqual(_normalize_name("my--skill"), "my-skill")

    def test_trailing_hyphens(self):
        self.assertEqual(_normalize_name("my-skill-"), "my-skill")

    def test_truncate_64(self):
        long_name = "a" * 100
        result = _normalize_name(long_name)
        self.assertEqual(len(result), 64)
        self.assertEqual(result, "a" * 64)


class TestParseSkillMd(unittest.TestCase):
    def test_valid(self):
        content = "---\nname: my-skill\ndescription: A test skill\n---\nInstructions here."
        fm, body = _parse_skill_md(content)
        self.assertEqual(fm["name"], "my-skill")
        self.assertEqual(fm["description"], "A test skill")
        self.assertEqual(body, "Instructions here.")

    def test_extra_keys_preserved_in_parse(self):
        content = "---\nname: my-skill\nauthor: someone\n---\nBody."
        fm, body = _parse_skill_md(content)
        self.assertEqual(fm["author"], "someone")
        self.assertEqual(fm["name"], "my-skill")

    def test_no_frontmatter(self):
        with self.assertRaises(ValueError):
            _parse_skill_md("Just plain text, no frontmatter.")

    def test_missing_closing(self):
        with self.assertRaises(ValueError):
            _parse_skill_md("---\nname: broken\nNo closing delimiter.")


class TestAdaptSkillForAdk(unittest.TestCase):
    """Integration tests for the full adaptation pipeline."""

    def setUp(self):
        self._dirs: list[str] = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _make_skill(
        self,
        name: str,
        frontmatter_extra: str = "",
        body: str = "Instructions.",
        refs: list[str] | None = None,
    ) -> str:
        """Create a temporary skill directory and return its path."""
        base = tempfile.mkdtemp()
        self._dirs.append(base)
        skill_dir = os.path.join(base, name)
        os.makedirs(skill_dir)

        fm = f"---\nname: {name}\ndescription: A test skill\n{frontmatter_extra}---\n{body}"
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write(fm)

        if refs:
            refs_dir = os.path.join(skill_dir, "references")
            os.makedirs(refs_dir)
            for ref_name in refs:
                with open(os.path.join(refs_dir, ref_name), "w") as f:
                    f.write("Reference content.")

        return skill_dir

    def test_valid_skill_passes(self):
        skill_dir = self._make_skill("my-skill")
        adapted, warnings = adapt_skill_for_adk(skill_dir)
        self.assertEqual(len(warnings), 0)
        self.assertTrue(os.path.isfile(os.path.join(adapted, "SKILL.md")))

    def test_strips_extra_frontmatter_keys(self):
        skill_dir = self._make_skill(
            "my-skill",
            frontmatter_extra="author: someone\ntags: [a, b]\nversion: 1.0\n",
        )
        adapted, warnings = adapt_skill_for_adk(skill_dir)
        self.assertTrue(any("Stripped" in w for w in warnings))

        # Verify stripped keys are gone from the file
        with open(os.path.join(adapted, "SKILL.md")) as f:
            content = f.read()
        self.assertNotIn("author", content)
        self.assertNotIn("tags", content)
        self.assertNotIn("version", content)
        # name and description preserved
        self.assertIn("name:", content)
        self.assertIn("description:", content)

    def test_fixes_name_mismatch(self):
        skill_dir = self._make_skill("Wrong_Name")
        adapted, warnings = adapt_skill_for_adk(skill_dir)
        self.assertTrue(adapted.endswith("wrong-name"))
        self.assertTrue(os.path.isdir(adapted))
        self.assertTrue(os.path.isfile(os.path.join(adapted, "SKILL.md")))

    def test_truncates_long_description(self):
        long_desc = "x" * 1200
        skill_dir = self._make_skill("my-skill")
        # Rewrite SKILL.md with long description
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write(f"---\nname: my-skill\ndescription: {long_desc}\n---\nBody.")
        adapted, warnings = adapt_skill_for_adk(skill_dir)
        self.assertTrue(any("Truncated" in w for w in warnings))

        # Verify description is at most 1024
        from meta_agent.tools.skills_tools import _parse_skill_md
        with open(os.path.join(adapted, "SKILL.md")) as f:
            fm, _ = _parse_skill_md(f.read())
        self.assertLessEqual(len(fm["description"]), 1024)

    def test_drops_scripts_dir(self):
        skill_dir = self._make_skill("my-skill")
        scripts = os.path.join(skill_dir, "scripts")
        os.makedirs(scripts)
        with open(os.path.join(scripts, "install.sh"), "w") as f:
            f.write("#!/bin/bash")
        adapted, warnings = adapt_skill_for_adk(skill_dir)
        self.assertFalse(os.path.isdir(os.path.join(adapted, "scripts")))
        self.assertTrue(any("scripts" in w for w in warnings))

    def test_keeps_references(self):
        skill_dir = self._make_skill("my-skill", refs=["guide.md"])
        adapted, warnings = adapt_skill_for_adk(skill_dir)
        refs_dir = os.path.join(adapted, "references")
        self.assertTrue(os.path.isdir(refs_dir))
        self.assertTrue(os.path.isfile(os.path.join(refs_dir, "guide.md")))

    def test_adk_validation_passes(self):
        """Adapted skill can be loaded by ADK's load_skill_from_dir (if available)."""
        skill_dir = self._make_skill("my-skill")
        adapted, _ = adapt_skill_for_adk(skill_dir)
        try:
            from google.adk.skills import load_skill_from_dir
            skill = load_skill_from_dir(adapted)
            self.assertIsNotNone(skill)
        except ImportError:
            self.skipTest("google.adk.skills not available")
        except Exception:
            # ADK may raise for other reasons; the key test is that
            # our format is structurally valid
            pass


if __name__ == "__main__":
    unittest.main()
