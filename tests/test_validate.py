"""Tests for meta_agent.tools.validate_tool — impl function only."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

# Ensure project root is on path for scaffold import
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scaffold import scaffold_agent
from meta_agent.tools.validate_tool import _validate_agent_impl


class TestValidateAgentImpl(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Scaffold a valid agent
        result = scaffold_agent("test-agent", output_dir=self.tmpdir)
        self.assertEqual(result["status"], "ok")
        self.agent_dir = result["path"]

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_scaffold_passes(self):
        result = _validate_agent_impl(self.agent_dir)
        self.assertEqual(result["status"], "ok", f"Errors: {result.get('errors')}")
        self.assertEqual(len(result["errors"]), 0)

    def test_missing_agent_py_fails(self):
        agent_py = os.path.join(self.agent_dir, "test_agent", "agent.py")
        self.assertTrue(os.path.isfile(agent_py))
        os.remove(agent_py)
        result = _validate_agent_impl(self.agent_dir)
        self.assertEqual(result["status"], "error")
        self.assertTrue(any("agent.py" in e for e in result["errors"]))

    def test_missing_directory_fails(self):
        result = _validate_agent_impl("/tmp/nonexistent-agent-dir-xyz")
        self.assertEqual(result["status"], "error")
        self.assertTrue(any("not found" in e.lower() or "does not exist" in e.lower()
                            for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
