"""Tests for nuvel.doctor — the diagnostic subcommand."""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nuvel import doctor


class TestStatusAndCheck(unittest.TestCase):
    def test_status_ok(self):
        c = doctor.Check("python", doctor.OK, "3.13")
        self.assertEqual(c.status, doctor.OK)
        line = c.format()
        self.assertIn("[OK]", line)
        self.assertIn("python", line)

    def test_status_fail(self):
        c = doctor.Check("env", doctor.FAIL, "missing")
        self.assertIn("[FAIL]", c.format())

    def test_status_warn(self):
        c = doctor.Check("docker", doctor.WARN, "not running")
        self.assertIn("[WARN]", c.format())


class TestInstallChecks(unittest.TestCase):
    def test_python_version_check_passes(self):
        # Whatever runs the test suite must be >= 3.11.
        c = doctor.check_python_version()
        self.assertEqual(c.status, doctor.OK)

    def test_check_import_known_module(self):
        c = doctor.check_import("yaml")
        self.assertEqual(c.status, doctor.OK)

    def test_check_import_missing(self):
        c = doctor.check_import("definitely_not_a_real_module_xyz_123")
        self.assertEqual(c.status, doctor.FAIL)

    def test_check_import_optional_missing_is_warn(self):
        c = doctor.check_import("definitely_not_a_real_module_xyz_123", optional=True)
        self.assertEqual(c.status, doctor.WARN)

    def test_run_install_checks_returns_list(self):
        checks = doctor.run_install_checks()
        self.assertTrue(all(isinstance(c, doctor.Check) for c in checks))
        names = [c.name for c in checks]
        self.assertIn("Python version", names)


class TestAgentDetection(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_agent_in_empty_dir(self):
        info = doctor.detect_agent(Path(self.tmpdir))
        self.assertFalse(info.is_agent)
        self.assertIsNone(info.framework)

    def test_detects_adk_agent(self):
        root = Path(self.tmpdir)
        (root / "requirements.txt").write_text("google-adk==1.26.0\nlitellm>=1.0\n")
        (root / "agent.py").write_text("# agent")
        (root / ".env.example").write_text("OPENROUTER_API_KEY=foo\n")
        info = doctor.detect_agent(root)
        self.assertTrue(info.is_agent)
        self.assertEqual(info.framework, "adk")

    def test_detects_claude_agent(self):
        root = Path(self.tmpdir)
        (root / "requirements.txt").write_text("claude-agent-sdk>=0.1\n")
        (root / "server.py").write_text("# server")
        info = doctor.detect_agent(root)
        self.assertTrue(info.is_agent)
        self.assertEqual(info.framework, "claude-agent-sdk")

    def test_detects_managed_agent(self):
        root = Path(self.tmpdir)
        (root / "requirements.txt").write_text("anthropic>=0.40\n")
        (root / "setup.py").write_text("# setup")
        (root / "server.py").write_text("# server")
        info = doctor.detect_agent(root)
        self.assertTrue(info.is_agent)
        self.assertEqual(info.framework, "anthropic-managed-agents")


class TestAgentChecks(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir)
        (self.root / "requirements.txt").write_text("google-adk==1.26.0\n")
        (self.root / "agent.py").write_text("# agent")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_missing_env_file_fails(self):
        info = doctor.detect_agent(self.root)
        checks = doctor.run_agent_checks(self.root, info)
        env_check = next(c for c in checks if c.name == ".env file")
        self.assertEqual(env_check.status, doctor.FAIL)

    def test_env_file_missing_required_key_fails(self):
        (self.root / ".env").write_text("PORT=8000\n")
        info = doctor.detect_agent(self.root)
        checks = doctor.run_agent_checks(self.root, info)
        key_check = next(
            c for c in checks if c.name == "OPENROUTER_API_KEY"
        )
        self.assertEqual(key_check.status, doctor.FAIL)

    def test_env_file_with_required_key_passes(self):
        (self.root / ".env").write_text("OPENROUTER_API_KEY=sk-test\n")
        info = doctor.detect_agent(self.root)
        checks = doctor.run_agent_checks(self.root, info)
        key_check = next(
            c for c in checks if c.name == "OPENROUTER_API_KEY"
        )
        self.assertEqual(key_check.status, doctor.OK)

    def test_env_placeholder_value_fails(self):
        (self.root / ".env").write_text(
            "OPENROUTER_API_KEY=your_openrouter_api_key_here\n"
        )
        info = doctor.detect_agent(self.root)
        checks = doctor.run_agent_checks(self.root, info)
        key_check = next(
            c for c in checks if c.name == "OPENROUTER_API_KEY"
        )
        self.assertEqual(key_check.status, doctor.FAIL)

    def test_dockerfile_triggers_docker_check(self):
        (self.root / ".env").write_text("OPENROUTER_API_KEY=sk-test\n")
        (self.root / "Dockerfile").write_text("FROM python:3.13\n")
        info = doctor.detect_agent(self.root)
        checks = doctor.run_agent_checks(self.root, info)
        names = [c.name for c in checks]
        self.assertIn("Docker available", names)

    def test_gateways_dir_triggers_gateway_checks(self):
        (self.root / ".env").write_text("OPENROUTER_API_KEY=sk-test\n")
        gw = self.root / "gateways"
        gw.mkdir()
        (gw / "slack.py").write_text("# slack")
        info = doctor.detect_agent(self.root)
        checks = doctor.run_agent_checks(self.root, info)
        names = [c.name for c in checks]
        self.assertTrue(any("SLACK_BOT_TOKEN" in n for n in names))


class TestParseEnvFile(unittest.TestCase):
    def test_parses_basic_kv(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("FOO=bar\n# comment\nBAZ=\"quoted value\"\n\nEMPTY=\n")
            path = f.name
        try:
            env = doctor.parse_env_file(Path(path))
            self.assertEqual(env["FOO"], "bar")
            self.assertEqual(env["BAZ"], "quoted value")
            self.assertEqual(env["EMPTY"], "")
            self.assertNotIn("# comment", env)
        finally:
            os.unlink(path)

    def test_missing_file_returns_empty(self):
        env = doctor.parse_env_file(Path("/no/such/file.env"))
        self.assertEqual(env, {})


class TestRunDoctor(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_install_only_when_no_agent(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = doctor.run_doctor(cwd=Path(self.tmpdir))
        out = buf.getvalue()
        self.assertIn("Install", out)
        # No agent detected → no agent section heading.
        self.assertNotIn("Agent (", out)
        # rc should be 0 if install is clean (it is in test env).
        self.assertIn(rc, (0, 1))

    def test_failing_agent_returns_nonzero(self):
        root = Path(self.tmpdir)
        (root / "requirements.txt").write_text("google-adk==1.26.0\n")
        (root / "agent.py").write_text("# agent")
        # No .env at all → guaranteed FAIL.
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = doctor.run_doctor(cwd=root)
        out = buf.getvalue()
        self.assertIn("Agent", out)
        self.assertIn("[FAIL]", out)
        self.assertEqual(rc, 1)

    def test_summary_printed(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            doctor.run_doctor(cwd=Path(self.tmpdir))
        self.assertIn("Summary", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
