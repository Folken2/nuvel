"""Tests for path_guard — file-tool path normalization."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from nuvel.callbacks.path_guard import _normalize_path, path_guard


class TestNormalizePath(unittest.TestCase):
    KEBAB = "my-agent"
    SNAKE = "my_agent"

    def test_no_op_on_correct_package_path(self):
        new, note = _normalize_path("my_agent/tools/foo.py", self.KEBAB, self.SNAKE)
        self.assertEqual(new, "my_agent/tools/foo.py")
        self.assertIsNone(note)

    def test_no_op_on_root_dotfile(self):
        new, note = _normalize_path(".env.example", self.KEBAB, self.SNAKE)
        self.assertEqual(new, ".env.example")
        self.assertIsNone(note)

    def test_strip_generated_agents_prefix(self):
        new, note = _normalize_path(
            "generated-agents/my-agent/my_agent/tools/foo.py", self.KEBAB, self.SNAKE
        )
        self.assertEqual(new, "my_agent/tools/foo.py")
        self.assertIsNotNone(note)

    def test_strip_kebab_wrapper_with_snake_package(self):
        """Case (a): wrapper + package — strip the wrapper."""
        new, note = _normalize_path(
            "my-agent/my_agent/tools/foo.py", self.KEBAB, self.SNAKE
        )
        self.assertEqual(new, "my_agent/tools/foo.py")
        self.assertIsNotNone(note)

    def test_rewrite_kebab_confusion_to_snake_package(self):
        """Case (b): kebab-as-package confusion — rewrite to snake."""
        new, note = _normalize_path(
            "my-agent/tools/foo.py", self.KEBAB, self.SNAKE
        )
        self.assertEqual(new, "my_agent/tools/foo.py")
        self.assertIsNotNone(note)

    def test_rewrite_kebab_confusion_single_segment(self):
        new, note = _normalize_path("my-agent/agent.py", self.KEBAB, self.SNAKE)
        self.assertEqual(new, "my_agent/agent.py")
        self.assertIsNotNone(note)

    def test_strip_leading_dot_slash(self):
        new, note = _normalize_path("./my_agent/tools/foo.py", self.KEBAB, self.SNAKE)
        self.assertEqual(new, "my_agent/tools/foo.py")
        self.assertIsNotNone(note)

    def test_windows_separators_normalized(self):
        new, note = _normalize_path(
            "my_agent\\tools\\foo.py", self.KEBAB, self.SNAKE
        )
        self.assertEqual(new, "my_agent/tools/foo.py")
        self.assertIsNotNone(note)

    def test_empty_path_passthrough(self):
        new, note = _normalize_path("", self.KEBAB, self.SNAKE)
        self.assertEqual(new, "")
        self.assertIsNone(note)

    def test_bare_kebab_name_passthrough(self):
        new, note = _normalize_path("my-agent", self.KEBAB, self.SNAKE)
        self.assertEqual(new, "my-agent")
        self.assertIsNone(note)

    def test_no_state_no_rewrite(self):
        """When kebab/package unknown, only the generic strips apply."""
        new, note = _normalize_path("my-agent/tools/foo.py", "", "")
        self.assertEqual(new, "my-agent/tools/foo.py")
        self.assertIsNone(note)


class TestPathGuardCallback(unittest.TestCase):
    def _context(self, kebab="my-agent", snake="my_agent"):
        return SimpleNamespace(
            state={
                "current_agent_name": kebab,
                "current_agent_package": snake,
            }
        )

    def _tool(self, name="write_file"):
        return SimpleNamespace(name=name)

    def test_ignores_non_path_tools(self):
        args = {"path": "my-agent/foo"}
        result = path_guard(self._tool("scaffold_agent"), args, self._context())
        self.assertIsNone(result)
        self.assertEqual(args["path"], "my-agent/foo")

    def test_absolute_paths_rejected(self):
        args = {"path": "/etc/passwd"}
        result = path_guard(self._tool(), args, self._context())
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "error")

    def test_rewrites_kebab_confusion_in_place(self):
        args = {"path": "my-agent/tools/foo.py", "content": "x"}
        result = path_guard(self._tool(), args, self._context())
        self.assertIsNone(result)  # tool is allowed to run
        self.assertEqual(args["path"], "my_agent/tools/foo.py")

    def test_strips_wrapper_in_place(self):
        args = {"path": "my-agent/my_agent/tools/foo.py", "content": "x"}
        result = path_guard(self._tool(), args, self._context())
        self.assertIsNone(result)
        self.assertEqual(args["path"], "my_agent/tools/foo.py")

    def test_non_string_path_ignored(self):
        args = {"path": 123}
        result = path_guard(self._tool(), args, self._context())
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
