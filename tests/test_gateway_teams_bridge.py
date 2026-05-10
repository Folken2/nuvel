"""Smoke tests for the generated agent's Teams sidecar.

The Microsoft 365 Agents SDK is heavyweight (aiohttp, MSAL); these tests
verify only that the module *parses* and exposes the expected entry points.
Full integration with Bot Framework is exercised separately by the
operator (Agents Playground / Azure Bot Service)."""

import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nuvel.backends.adk.scaffold import scaffold_agent


def _load_bridge_with_mocks(bridge_path: Path) -> types.ModuleType:
    """Load teams_bridge.py with heavy SDK dependencies mocked out."""
    # Build stub modules for every SDK import the bridge needs.
    mock_mods = {
        "httpx": MagicMock(),
        "pypdf": MagicMock(),
        "aiohttp": MagicMock(),
        "aiohttp.web": MagicMock(),
        "dotenv": MagicMock(),
        "microsoft_agents": MagicMock(),
        "microsoft_agents.activity": MagicMock(),
        "microsoft_agents.authentication": MagicMock(),
        "microsoft_agents.authentication.msal": MagicMock(),
        "microsoft_agents.hosting": MagicMock(),
        "microsoft_agents.hosting.aiohttp": MagicMock(),
        "microsoft_agents.hosting.core": MagicMock(),
    }

    # The bridge imports `from tm_test.gateways.commands import ...`. Resolve
    # that to the actual generated module so the registry is real, but stub
    # the parent packages so importlib doesn't try to load __init__.py
    # (which would in turn pull in google.adk and friends).
    pkg_root = bridge_path.parent.parent.parent  # .../tm-test/
    pkg_name = bridge_path.parent.parent.name    # 'tm_test'
    commands_path = bridge_path.parent / "commands.py"

    cmd_spec = importlib.util.spec_from_file_location(f"{pkg_name}.gateways.commands", commands_path)
    cmd_mod = importlib.util.module_from_spec(cmd_spec)

    pkg_stub = types.ModuleType(pkg_name)
    pkg_stub.__path__ = [str(pkg_root / pkg_name)]
    gw_stub = types.ModuleType(f"{pkg_name}.gateways")
    gw_stub.__path__ = [str(pkg_root / pkg_name / "gateways")]

    mock_mods[pkg_name] = pkg_stub
    mock_mods[f"{pkg_name}.gateways"] = gw_stub
    mock_mods[f"{pkg_name}.gateways.commands"] = cmd_mod

    with patch.dict(sys.modules, mock_mods):
        cmd_spec.loader.exec_module(cmd_mod)
        spec = importlib.util.spec_from_file_location("teams_bridge_test_mod", bridge_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


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

    def test_module_has_first_env_helper(self):
        source = self.bridge_path.read_text()
        self.assertIn("def _first_env(", source)

    def test_agent_client_honors_gateway_max_count_env_alias(self):
        bridge = _load_bridge_with_mocks(self.bridge_path)
        env_patch = {"GATEWAY_MAX_ATTACHMENT_COUNT": "3"}
        with patch.dict("os.environ", env_patch, clear=False):
            os.environ.pop("TEAMS_MAX_ATTACHMENT_COUNT", None)
            client = bridge.AgentClient()
            self.assertEqual(client.max_attachment_count, 3)

    def test_teams_specific_env_takes_precedence_over_gateway_alias(self):
        bridge = _load_bridge_with_mocks(self.bridge_path)
        env_patch = {
            "GATEWAY_MAX_ATTACHMENT_COUNT": "3",
            "TEAMS_MAX_ATTACHMENT_COUNT": "7",
        }
        with patch.dict("os.environ", env_patch, clear=False):
            client = bridge.AgentClient()
            self.assertEqual(client.max_attachment_count, 7)
