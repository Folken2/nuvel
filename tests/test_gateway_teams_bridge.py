"""Smoke tests for the generated agent's Teams sidecar.

The Microsoft 365 Agents SDK is heavyweight (aiohttp, MSAL); these tests
verify only that the module *parses* and exposes the expected entry points.
Full integration with Bot Framework is exercised separately by the
operator (Agents Playground / Azure Bot Service)."""

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent


class TestTeamsBridgeParseable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        result = scaffold_agent("tm-test", output_dir=cls.tmpdir, with_teams=True)
        if result["status"] != "ok":
            raise AssertionError(result.get("message"))
        cls.bridge_path = Path(result["path"]) / "tm_test" / "gateways" / "teams_bridge.py"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_module_parses(self):
        # Compile-only — avoids importing the heavy SDK.
        source = self.bridge_path.read_text()
        compile(source, str(self.bridge_path), "exec")

    def test_module_has_main(self):
        source = self.bridge_path.read_text()
        self.assertIn("def main(", source)
        self.assertIn('if __name__ == "__main__":', source)

    def test_module_has_dual_mode(self):
        source = self.bridge_path.read_text()
        self.assertIn("_has_service_connection_config", source)
