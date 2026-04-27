"""Tests for nuvel.tools.file_tools — impl functions only (no ToolContext)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from nuvel.tools.file_tools import (
    _resolve_safe_path,
    _write_file_impl,
    _read_file_impl,
    _list_files_impl,
)


class TestResolveSafePath(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_relative_path_works(self):
        result = _resolve_safe_path("subdir/file.py", self.base)
        self.assertEqual(result, os.path.join(self.base, "subdir", "file.py"))

    def test_absolute_path_rejected(self):
        with self.assertRaises(ValueError):
            _resolve_safe_path("/etc/passwd", self.base)

    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):
            _resolve_safe_path("../outside.py", self.base)

    def test_nested_traversal_rejected(self):
        with self.assertRaises(ValueError):
            _resolve_safe_path("sub/../../outside.py", self.base)

    def test_dot_path_works(self):
        result = _resolve_safe_path(".", self.base)
        self.assertEqual(result, self.base)


class TestWriteFileImpl(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_write_creates_dirs(self):
        result = _write_file_impl("a/b/c.py", "hello", self.base)
        self.assertEqual(result["status"], "success")
        full_path = os.path.join(self.base, "a", "b", "c.py")
        self.assertTrue(os.path.isfile(full_path))
        with open(full_path) as f:
            self.assertEqual(f.read(), "hello")

    def test_write_returns_bytes(self):
        content = "abc"
        result = _write_file_impl("test.txt", content, self.base)
        self.assertEqual(result["bytes"], len(content.encode("utf-8")))

    def test_overwrite_works(self):
        _write_file_impl("test.txt", "first", self.base)
        result = _write_file_impl("test.txt", "second", self.base)
        self.assertEqual(result["status"], "success")
        with open(os.path.join(self.base, "test.txt")) as f:
            self.assertEqual(f.read(), "second")

    def test_write_rejects_traversal(self):
        result = _write_file_impl("../escape.py", "bad", self.base)
        self.assertEqual(result["status"], "error")


class TestReadFileImpl(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.base, "sub"))
        with open(os.path.join(self.base, "sub", "hello.txt"), "w") as f:
            f.write("world")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_read_existing_file(self):
        result = _read_file_impl("sub/hello.txt", self.base)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["content"], "world")
        self.assertEqual(result["bytes"], 5)

    def test_read_missing_file(self):
        result = _read_file_impl("nope.txt", self.base)
        self.assertEqual(result["status"], "error")

    def test_read_rejects_traversal(self):
        result = _read_file_impl("../escape.py", self.base)
        self.assertEqual(result["status"], "error")


class TestListFilesImpl(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.base, "pkg"))
        with open(os.path.join(self.base, "file.txt"), "w") as f:
            f.write("a")
        with open(os.path.join(self.base, "pkg", "mod.py"), "w") as f:
            f.write("b")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_list_root(self):
        result = _list_files_impl(".", self.base)
        self.assertEqual(result["status"], "success")
        self.assertIn("file.txt", result["entries"])
        self.assertIn("pkg/", result["entries"])

    def test_dirs_have_trailing_slash(self):
        result = _list_files_impl(".", self.base)
        dirs = [e for e in result["entries"] if e.endswith("/")]
        self.assertIn("pkg/", dirs)

    def test_count_matches(self):
        result = _list_files_impl(".", self.base)
        self.assertEqual(result["count"], len(result["entries"]))

    def test_list_missing_dir(self):
        result = _list_files_impl("nonexistent", self.base)
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
