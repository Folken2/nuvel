"""Tests for the runtime ``/personality`` overlay command.

Personality files normally live at ``~/.nuvel/personalities/``; we redirect
``commands.PERSONALITIES_DIR`` to a tmpdir per class to keep the test
hermetic.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent


def _scaffold_with(tmpdir, **flags):
    result = scaffold_agent("agent-test", output_dir=tmpdir, **flags)
    if result["status"] != "ok":
        raise AssertionError(result.get("message"))
    return Path(result["path"]) / "agent_test"


def _import_module(pkg_dir: Path, dotted: str, *, fresh_name: str | None = None):
    file_path = pkg_dir / Path(*dotted.split(".")).with_suffix(".py")
    name = fresh_name or f"_gw_{dotted.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _PersonalitiesTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.pkg = _scaffold_with(cls.tmpdir, with_telegram=True)
        cls.commands = _import_module(
            cls.pkg, "gateways.commands", fresh_name=f"_pcmds_{cls.__name__}"
        )
        cls.personalities_dir = Path(cls.tmpdir) / "personalities"
        cls.personalities_dir.mkdir(parents=True, exist_ok=True)
        cls.commands.PERSONALITIES_DIR = cls.personalities_dir
        # Reset state between classes.
        cls.commands._ACTIVE_PERSONALITIES.clear()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        self.commands._ACTIVE_PERSONALITIES.clear()
        # Wipe directory between tests too.
        for p in self.personalities_dir.glob("*.md"):
            p.unlink()

    def _dispatch(self, text, session_id="s1"):
        ctx = self.commands.CommandContext(
            user_id="u", channel="c", session_id=session_id, text=text,
        )
        return asyncio.run(self.commands.try_dispatch(text, ctx))


class TestListing(_PersonalitiesTestBase):
    def test_list_when_empty(self):
        result = self._dispatch("/personality")
        self.assertTrue(result.handled)
        self.assertEqual(len(result.replies), 1)
        self.assertIn("No personalities found", result.replies[0])

    def test_list_when_populated(self):
        (self.personalities_dir / "concise.md").write_text(
            "---\ndescription: Short answers.\n---\nBe concise.\n", encoding="utf-8",
        )
        (self.personalities_dir / "socratic.md").write_text(
            "Ask questions first.\n", encoding="utf-8",
        )
        result = self._dispatch("/personality")
        self.assertTrue(result.handled)
        body = result.replies[0]
        self.assertIn("concise", body)
        self.assertIn("socratic", body)
        self.assertIn("Short answers", body)


class TestSetGetClear(_PersonalitiesTestBase):
    def test_set_and_get_active(self):
        (self.personalities_dir / "concise.md").write_text(
            "Be concise.\n", encoding="utf-8",
        )
        result = self._dispatch("/personality concise", session_id="s-set")
        self.assertTrue(result.handled)
        self.assertIn("concise", result.replies[0])
        body = self.commands.get_active_personality("s-set")
        self.assertEqual(body, "Be concise.")

    def test_list_marks_active(self):
        (self.personalities_dir / "concise.md").write_text("Body.\n", encoding="utf-8")
        self._dispatch("/personality concise", session_id="s-mark")
        result = self._dispatch("/personality", session_id="s-mark")
        self.assertIn("(active)", result.replies[0])

    def test_clear_with_off(self):
        (self.personalities_dir / "concise.md").write_text("Body.\n", encoding="utf-8")
        self._dispatch("/personality concise", session_id="s-off")
        self.assertIsNotNone(self.commands.get_active_personality("s-off"))
        result = self._dispatch("/personality off", session_id="s-off")
        self.assertTrue(result.handled)
        self.assertIn("cleared", result.replies[0].lower())
        self.assertIsNone(self.commands.get_active_personality("s-off"))

    def test_clear_with_reset_alias(self):
        (self.personalities_dir / "concise.md").write_text("Body.\n", encoding="utf-8")
        self._dispatch("/personality concise", session_id="s-reset")
        result = self._dispatch("/personality reset", session_id="s-reset")
        self.assertTrue(result.handled)
        self.assertIsNone(self.commands.get_active_personality("s-reset"))


class TestErrors(_PersonalitiesTestBase):
    def test_missing_personality(self):
        result = self._dispatch("/personality nonexistent")
        self.assertTrue(result.handled)
        self.assertIn("No personality named", result.replies[0])

    def test_get_active_when_file_deleted(self):
        path = self.personalities_dir / "ghost.md"
        path.write_text("hello\n", encoding="utf-8")
        self._dispatch("/personality ghost", session_id="s-ghost")
        path.unlink()
        # File-not-found should silently clear and return None, not crash.
        self.assertIsNone(self.commands.get_active_personality("s-ghost"))

    def test_malformed_yaml_does_not_crash(self):
        (self.personalities_dir / "bad.md").write_text(
            "---\nname: : : bad\n---\nReal body.\n", encoding="utf-8",
        )
        self._dispatch("/personality bad", session_id="s-bad")
        body = self.commands.get_active_personality("s-bad")
        # Body should still be reachable even if frontmatter parsing failed.
        self.assertIsNotNone(body)
        self.assertIn("Real body", body)


class TestMultiSessionIsolation(_PersonalitiesTestBase):
    def test_two_sessions_independent(self):
        (self.personalities_dir / "concise.md").write_text("Be concise.\n", encoding="utf-8")
        (self.personalities_dir / "socratic.md").write_text("Ask questions.\n", encoding="utf-8")
        self._dispatch("/personality concise", session_id="alice")
        self._dispatch("/personality socratic", session_id="bob")
        self.assertEqual(self.commands.get_active_personality("alice"), "Be concise.")
        self.assertEqual(self.commands.get_active_personality("bob"), "Ask questions.")
        # Clearing one does not affect the other.
        self._dispatch("/personality off", session_id="alice")
        self.assertIsNone(self.commands.get_active_personality("alice"))
        self.assertEqual(self.commands.get_active_personality("bob"), "Ask questions.")


class TestPersonaAlias(_PersonalitiesTestBase):
    def test_persona_alias_works(self):
        self.assertTrue(self.commands.is_command("/persona"))
        self.assertTrue(self.commands.is_command("/personality"))


if __name__ == "__main__":
    unittest.main()
