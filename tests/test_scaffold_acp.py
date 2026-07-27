"""Tests for the --with-acp flag on `nuvel new`.

`--with-acp` makes a generated ADK agent ACP-compatible (Agent Client
Protocol, stdio JSON-RPC) and CLI-runnable (local terminal entrypoint).
Like the other cross-cutting flags it is ADK-only and rejected by the
Claude Agent SDK and Managed Agents backends.
"""

import py_compile
import shutil
import tempfile
import unittest
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent as adk_scaffold
from nuvel.backends.claude_agent_sdk.scaffold import scaffold_agent as cas_scaffold
from nuvel.backends.anthropic_managed_agents.scaffold import scaffold_agent as ama_scaffold


class TestADKAcceptsAcpFlag(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_flag_returns_ok_and_no_acp(self):
        result = adk_scaffold("agent-a", output_dir=self.tmpdir)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result.get("with_acp"))

    def test_with_acp_flag_accepted_and_echoed(self):
        result = adk_scaffold("agent-b", output_dir=self.tmpdir, with_acp=True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["with_acp"])

    def test_with_acp_is_independent_of_other_bundles(self):
        # Does not imply composio/persona/workflow, and does not require them.
        result = adk_scaffold("agent-c", output_dir=self.tmpdir, with_acp=True)
        self.assertFalse(result.get("with_composio"))
        self.assertFalse(result.get("persona"))
        self.assertFalse(result.get("workflow"))


class TestNonAdkBackendsRejectAcp(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_claude_agent_sdk_rejects_with_acp(self):
        result = cas_scaffold("agent-d", output_dir=self.tmpdir, with_acp=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("with-acp", result["message"].lower())

    def test_anthropic_managed_rejects_with_acp(self):
        result = ama_scaffold("agent-e", output_dir=self.tmpdir, with_acp=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("with-acp", result["message"].lower())


class TestCLIParsing(unittest.TestCase):
    def test_parser_accepts_with_acp_flag(self):
        from nuvel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["new", "agent-f", "--with-acp"])
        self.assertTrue(args.with_acp)

    def test_parser_defaults_with_acp_false(self):
        from nuvel.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["new", "agent-g"])
        self.assertFalse(args.with_acp)


class TestNoFlagIsClean(unittest.TestCase):
    """A no-flag scaffold must not leak ACP files, env, or README blocks."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold("agent-plain", output_dir=self.tmpdir)
        self.assertEqual(result["status"], "ok")
        self.agent_dir = Path(result["path"])

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_acp_package(self):
        self.assertFalse((self.agent_dir / "agent_plain" / "acp").exists())

    def test_no_cli_module(self):
        self.assertFalse((self.agent_dir / "agent_plain" / "cli.py").exists())

    def test_env_example_has_no_acp_block_or_placeholder(self):
        env = (self.agent_dir / ".env.example").read_text()
        self.assertNotIn("{{acp", env)
        self.assertNotIn("ACP_USER_ID", env)

    def test_readme_has_no_acp_section_or_placeholder(self):
        readme = (self.agent_dir / "README.md").read_text()
        self.assertNotIn("{{acp", readme)
        self.assertNotIn("Agent Client Protocol", readme)


class TestAcpOverlay(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold("agent-acp", output_dir=self.tmpdir, with_acp=True)
        self.assertEqual(result["status"], "ok")
        self.agent_dir = Path(result["path"])
        self.pkg_dir = self.agent_dir / "agent_acp"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_acp_modules_exist(self):
        for rel in (
            "acp/__init__.py",
            "acp/__main__.py",
            "acp/jsonrpc.py",
            "acp/runtime.py",
            "acp/server.py",
            "acp/mcp.py",
            "acp/fs.py",
            "acp/permission.py",
            "cli.py",
        ):
            self.assertTrue((self.pkg_dir / rel).is_file(), f"missing {rel}")

    def test_generated_modules_compile(self):
        targets = list((self.pkg_dir / "acp").rglob("*.py")) + [self.pkg_dir / "cli.py"]
        for t in targets:
            py_compile.compile(str(t), doraise=True)

    def test_no_unresolved_placeholders(self):
        targets = list((self.pkg_dir / "acp").rglob("*.py")) + [self.pkg_dir / "cli.py"]
        for t in targets:
            self.assertNotIn("{{", t.read_text(), f"unresolved placeholder in {t.name}")

    def test_package_name_substituted_in_entrypoints(self):
        # The APP_NAME and module paths must reference the real package.
        runtime = (self.pkg_dir / "acp" / "runtime.py").read_text()
        self.assertIn('APP_NAME = "agent-acp"', runtime)
        cli = (self.pkg_dir / "cli.py").read_text()
        self.assertIn("agent_acp", cli)

    def test_env_example_has_acp_block(self):
        env = (self.agent_dir / ".env.example").read_text()
        self.assertIn("ACP_USER_ID", env)
        self.assertIn("python -m agent_acp.acp", env)
        self.assertNotIn("{{acp", env)

    def test_readme_has_acp_section(self):
        readme = (self.agent_dir / "README.md").read_text()
        self.assertIn("Agent Client Protocol", readme)
        self.assertIn("python -m agent_acp.acp", readme)
        self.assertIn("python -m agent_acp.cli", readme)
        self.assertNotIn("{{acp", readme)


if __name__ == "__main__":
    unittest.main()
