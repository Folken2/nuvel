"""Tests for AgentHarness scaffolding: harness.py.tmpl generation and wiring."""

import shutil
import tempfile
import unittest
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent as adk_scaffold


class TestHarnessFileGenerated(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold("agent-hn", output_dir=self.tmpdir, with_telegram=True)
        self.assertEqual(result["status"], "ok")
        self.agent_dir = Path(result["path"])
        self.harness_path = self.agent_dir / "agent_hn" / "harness.py"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_harness_file_exists(self):
        self.assertTrue(self.harness_path.is_file())

    def test_no_leftover_tmpl_file(self):
        self.assertFalse((self.agent_dir / "agent_hn" / "harness.py.tmpl").exists())

    def test_harness_has_no_unrendered_placeholders(self):
        content = self.harness_path.read_text()
        self.assertNotIn("{{", content)

    def test_harness_contains_agent_harness_class(self):
        content = self.harness_path.read_text()
        self.assertIn("class AgentHarness", content)

    def test_harness_contains_artifact_methods(self):
        content = self.harness_path.read_text()
        for method in (
            "async def save_artifact",
            "async def load_artifact",
            "async def list_artifact_versions",
            "async def delete_artifact",
        ):
            self.assertIn(method, content, f"Missing {method} in harness.py")

    def test_harness_imports_from_scaffolded_package(self):
        content = self.harness_path.read_text()
        self.assertIn("from agent_hn.plugins import", content)


class TestEnvExampleHasArtifactVars(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold("agent-hn2", output_dir=self.tmpdir)
        self.assertEqual(result["status"], "ok")
        self.agent_dir = Path(result["path"])

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_env_example_has_artifact_service_uri(self):
        env = (self.agent_dir / ".env.example").read_text()
        self.assertIn("ARTIFACT_SERVICE_URI", env)

    def test_env_example_has_artifact_storage_dir(self):
        env = (self.agent_dir / ".env.example").read_text()
        self.assertIn("ARTIFACT_STORAGE_DIR", env)


class TestRunAdkGatewayUsesAgentHarness(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold("agent-hn3", output_dir=self.tmpdir, with_telegram=True)
        self.assertEqual(result["status"], "ok")
        self.agent_dir = Path(result["path"])
        self.run_adk = (self.agent_dir / "run_adk.py").read_text()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_gateway_state_injection_uses_agent_harness(self):
        self.assertIn("from agent_hn3.harness import AgentHarness", self.run_adk)
        self.assertIn("AgentHarness.get(app.state.app_name)", self.run_adk)
        self.assertIn("app.state.runner = _harness.build_runner(agent=_root)", self.run_adk)

    def test_gateway_injection_does_not_construct_runner_inline(self):
        # The gateway state-injection block must go through AgentHarness,
        # not `Runner(...)` directly — that would bypass session/artifact
        # service and plugin wiring.
        gateway_block_start = self.run_adk.index('app.state.app_name = "agent-hn3"')
        gateway_block_end = self.run_adk.index("app.include_router")
        gateway_block = self.run_adk[gateway_block_start:gateway_block_end]
        self.assertNotIn("Runner(", gateway_block)

    def test_streaming_section_uses_agent_harness(self):
        self.assertIn("from agent_hn3.harness import AgentHarness", self.run_adk)
        self.assertIn("harness = AgentHarness.get(app_name)", self.run_adk)
        # Runner is built through the harness (memory_service now wired in too).
        self.assertIn("harness.build_runner(agent=live_agent, memory_service=", self.run_adk)

    def test_cron_section_uses_agent_harness(self):
        self.assertIn("_harness = AgentHarness.get(app.state.app_name)", self.run_adk)
        self.assertIn("_harness.build_runner(", self.run_adk)
        self.assertIn("agent=_cron_root", self.run_adk)

    def test_standard_mode_uses_agent_harness_for_session_and_artifact_uris(self):
        self.assertIn('harness = AgentHarness.get("agent-hn3")', self.run_adk)
        self.assertIn("session_service_uri=harness.session_service_uri", self.run_adk)
        self.assertIn("artifact_service_uri=harness.artifact_service_uri", self.run_adk)
        self.assertIn("extra_plugins=harness.extra_plugins", self.run_adk)


class TestRunAdkGatewayUsesAgentHarnessNoFlags(unittest.TestCase):
    """Streaming and cron sections use AgentHarness even without gateway flags."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold("agent-hn4", output_dir=self.tmpdir)
        self.assertEqual(result["status"], "ok")
        self.agent_dir = Path(result["path"])
        self.run_adk = (self.agent_dir / "run_adk.py").read_text()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_streaming_section_uses_agent_harness(self):
        self.assertIn("from agent_hn4.harness import AgentHarness", self.run_adk)
        self.assertIn("harness = AgentHarness.get(app_name)", self.run_adk)

    def test_cron_section_uses_agent_harness(self):
        self.assertIn("_harness = AgentHarness.get(app.state.app_name)", self.run_adk)
        self.assertIn("_harness.build_runner(", self.run_adk)
        self.assertIn("agent=_cron_root", self.run_adk)
