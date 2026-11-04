"""Tests for nuvel.doctor — the diagnostic subcommand."""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import unittest
from pathlib import Path
from unittest import mock

from nuvel import doctor


_home = tempfile.TemporaryDirectory()
_patchers = [
    mock.patch.object(
        doctor, "check_provider_reachable",
        side_effect=lambda name, provider, **_: doctor.Check(f"{name} reachable", doctor.OK),
    ),
    mock.patch.dict(os.environ, {"HOME": _home.name}),
    mock.patch("nuvel.pricing.fetch_openrouter", return_value={}),
]


def setUpModule():
    # Hermetic: no real probes, and the developer's own keys and home stay out.
    for patcher in _patchers:
        patcher.start()
    for key in list(os.environ):
        if key.endswith(("_API_KEY", "_API_BASE", "_BASE_URL")) or key in ("FAST_MODEL", "REASONING_MODEL", "TRACE_DIR", "XDG_DATA_HOME"):
            del os.environ[key]


def tearDownModule():
    for patcher in reversed(_patchers):
        patcher.stop()
    _home.cleanup()


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
        (self.root / ".env").write_text("OPENROUTER_API_KEY=sk-or-test-key-value\n")
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
        (self.root / ".env").write_text("OPENROUTER_API_KEY=sk-or-test-key-value\n")
        (self.root / "Dockerfile").write_text("FROM python:3.13\n")
        info = doctor.detect_agent(self.root)
        checks = doctor.run_agent_checks(self.root, info)
        names = [c.name for c in checks]
        self.assertIn("Docker available", names)

    def test_gateways_dir_triggers_gateway_checks(self):
        (self.root / ".env").write_text("OPENROUTER_API_KEY=sk-or-test-key-value\n")
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


class _TmpDirCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def _adk_agent(self, env: str | None = "OPENROUTER_API_KEY=sk-or-test-key-value\n") -> Path:
        (self.root / "requirements.txt").write_text("google-adk==1.26.0\n")
        (self.root / "agent.py").write_text("# agent")
        if env is not None:
            (self.root / ".env").write_text(env)
        return self.root

    def _check(self, checks, name):
        return next(c for c in checks if c.name == name)

    def _run(self, *args, **kwargs):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = doctor.run_doctor(*args, **kwargs)
        return code, buf.getvalue()


class TestCleanBox(_TmpDirCase):
    """No configuration at all: name what's missing, and how to fix each item."""

    def _isolated_meta(self):
        return mock.patch.object(doctor, "_find_env_file", return_value=None)

    def test_exits_nonzero_and_names_the_key(self):
        with self._isolated_meta():
            code, out = self._run(cwd=self.root)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]  OPENROUTER_API_KEY", out)
        self.assertIn("export OPENROUTER_API_KEY", out)

    def test_every_problem_has_one_next_step_and_no_traceback(self):
        with self._isolated_meta():
            sections = doctor.collect_sections(self.root)
        problems = [c for _, checks in sections for c in checks if c.status != doctor.OK]
        self.assertTrue(problems)
        for check in problems:
            self.assertTrue(check.fix, f"{check.name} has no next step")
            self.assertNotIn("\n", check.fix)
            self.assertNotIn("Traceback", check.detail)

    def test_exported_key_is_enough(self):
        with self._isolated_meta(), mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-key-value"}):
            code, _ = self._run(cwd=self.root)
        self.assertEqual(code, 0)

    def test_cwd_env_is_not_read_by_nuvel_run(self):
        # load_dotenv() searches from the installed package, not the cwd.
        (self.root / ".env").write_text("OPENROUTER_API_KEY=sk-or-test-key-value\n")
        with self._isolated_meta():
            code, out = self._run(cwd=self.root)
        self.assertEqual(code, 1)
        self.assertIn("does not read that file", out)

    def test_source_checkout_is_not_treated_as_a_generated_agent(self):
        (self.root / "nuvel").mkdir()
        (self.root / "nuvel" / "doctor.py").write_text("")
        (self.root / "nuvel" / "agent.py").write_text("")
        (self.root / "requirements.txt").write_text("google-adk\n")
        self.assertFalse(doctor.detect_agent(self.root).is_agent)


class TestDocumentedSetup(_TmpDirCase):
    def test_scaffolded_agent_is_green_after_cp_env_example(self):
        from nuvel.backends.adk.scaffold import scaffold_agent

        result = scaffold_agent("my-agent", output_dir=str(self.root))
        agent = Path(result["path"])
        env = (agent / ".env.example").read_text()
        code, out = self._run(cwd=agent)
        self.assertEqual(code, 1, out)  # .env not created yet.
        (agent / ".env").write_text(
            env.replace("your_openrouter_api_key_here", "sk-or-real-value")
        )
        with mock.patch.object(doctor, "_check_docker_running", return_value=doctor.Check("Docker", doctor.OK)):
            code, out = self._run(cwd=agent)
        self.assertEqual(code, 0, out)
        self.assertIn("Agent (adk)", out)


class TestConfigFile(_TmpDirCase):
    def test_export_prefix_and_inline_comment_are_understood(self):
        path = self.root / ".env"
        path.write_text("export FOO=bar  # note\n")
        self.assertEqual(doctor.parse_env_file(path)["FOO"], "bar")

    def test_multiline_quoted_value_is_valid(self):
        path = self.root / ".env"
        path.write_text('CERT="a\nb"\n')
        self.assertEqual(doctor.check_env_file_valid(path).status, doctor.OK)

    def test_malformed_line_is_reported_without_its_content(self):
        path = self.root / ".env"
        path.write_text("OK=1\nsk-or-SECRET not an assignment\n")
        check = doctor.check_env_file_valid(path)
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn("2", check.detail)
        self.assertNotIn("SECRET", check.detail + check.fix)

    def test_non_utf8_file_fails_with_a_next_step(self):
        path = self.root / ".env"
        path.write_bytes(b"KEY=\xff\xfe\n")
        check = doctor.check_env_file_valid(path)
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn("UTF-8", check.fix)

    def test_environment_satisfies_a_key_and_wins_over_env_file(self):
        agent = self._adk_agent("OPENROUTER_API_KEY=your_key_here\n")
        info = doctor.detect_agent(agent)
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-key-value"}):
            checks = doctor.run_agent_checks(agent, info)
        self.assertEqual(self._check(checks, "OPENROUTER_API_KEY").status, doctor.OK)

    def test_missing_env_file_is_fine_when_environment_has_the_key(self):
        agent = self._adk_agent(env=None)
        info = doctor.detect_agent(agent)
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-key-value"}):
            checks = doctor.run_agent_checks(agent, info)
        self.assertEqual(self._check(checks, ".env file").status, doctor.OK)

    def test_keyless_provider_needs_no_env_file(self):
        agent = self._adk_agent(env=None)
        models = {"FAST_MODEL": "ollama/m", "REASONING_MODEL": "ollama/m"}
        with mock.patch.dict(os.environ, models):
            checks = doctor.run_agent_checks(agent, doctor.detect_agent(agent))
        self.assertEqual(self._check(checks, ".env file").status, doctor.OK)

    def test_required_key_follows_the_configured_model(self):
        agent = self._adk_agent(
            "FAST_MODEL=anthropic/claude-x\nREASONING_MODEL=anthropic/claude-y\n"
        )
        checks = doctor.run_agent_checks(agent, doctor.detect_agent(agent))
        names = [c.name for c in checks]
        self.assertIn("ANTHROPIC_API_KEY", names)
        self.assertNotIn("OPENROUTER_API_KEY", names)

    def test_unknown_provider_needs_its_conventional_key(self):
        agent = self._adk_agent("FAST_MODEL=acme/m\nOPENROUTER_API_KEY=sk\n")
        checks = doctor.run_agent_checks(agent, doctor.detect_agent(agent))
        self.assertEqual(self._check(checks, "ACME_API_KEY").status, doctor.FAIL)

    def test_unrecognised_framework_does_not_pass_silently(self):
        (self.root / "requirements.txt").write_text("httpx\n")
        (self.root / "server.py").write_text("")
        (self.root / ".env").write_text("A=1\n")
        checks = doctor.run_agent_checks(self.root, doctor.detect_agent(self.root))
        self.assertEqual(self._check(checks, "API keys").status, doctor.WARN)


class TestHermes(_TmpDirCase):
    def _profile(self, config: str) -> Path:
        profile = self.root / "bot"
        profile.mkdir()
        (profile / "config.yaml").write_text(config)
        (profile / "SOUL.md").write_text("soul")
        return profile

    def test_scaffolded_profile_is_detected_from_project_dir(self):
        from nuvel.backends.hermes.scaffold import scaffold_agent

        result = scaffold_agent("bot", output_dir=str(self.root))
        info = doctor.detect_agent(Path(result["path"]))
        self.assertEqual(info.framework, "hermes")
        self.assertEqual(doctor.check_hermes_config(info.root)[1], "openrouter")

    def test_key_follows_model_provider(self):
        self._profile("model:\n  provider: groq\n")
        info = doctor.detect_agent(self.root)
        checks = doctor.run_agent_checks(self.root, info)
        self.assertEqual(self._check(checks, "GROQ_API_KEY").status, doctor.FAIL)
        self.assertIn("/bot/.env", self._check(checks, "GROQ_API_KEY").fix)

    def test_invalid_yaml_fails_with_location(self):
        profile = self._profile("model: [unclosed\n")
        check, provider, _ = doctor.check_hermes_config(profile)
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIsNone(provider)
        self.assertTrue(check.fix)

    def test_config_without_provider_fails(self):
        check, _, _ = doctor.check_hermes_config(self._profile("agent: {}\n"))
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn("provider", check.fix)


class TestInstallIntegrity(unittest.TestCase):
    def test_this_install_passes(self):
        for fn in (doctor.check_distribution, doctor.check_declared_dependencies, doctor.check_bundled_files):
            self.assertEqual(fn().status, doctor.OK, fn.__name__)

    def test_missing_distribution_fails(self):
        missing = doctor.importlib.metadata.PackageNotFoundError
        with mock.patch.object(doctor.importlib.metadata, "version", side_effect=missing):
            check = doctor.check_distribution()
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn("install", check.fix.lower())

    def test_wrong_dependency_version_is_unmet(self):
        self.assertIn("does not satisfy", doctor._unmet_requirement("pyyaml<0.1"))

    def test_absent_dependency_is_unmet(self):
        self.assertIn("not installed", doctor._unmet_requirement("not-a-real-dist-xyz>=1"))

    def test_dependency_for_other_extra_is_ignored(self):
        self.assertIsNone(doctor._unmet_requirement('not-a-real-dist-xyz; extra == "docs"'))

    def test_import_error_of_any_kind_is_reported(self):
        with mock.patch.object(doctor.importlib, "import_module", side_effect=OSError("bad .so")):
            check = doctor.check_import("yaml")
        self.assertEqual(check.status, doctor.FAIL)
        self.assertTrue(check.fix)


class TestState(_TmpDirCase):
    def test_uncreated_dir_is_probed_through_its_parent_and_not_created(self):
        target = self.root / "a" / "b"
        self.assertEqual(doctor.check_writable("x", target).status, doctor.OK)
        self.assertFalse((self.root / "a").exists())

    def test_unwritable_dir_fails_with_next_step(self):
        with mock.patch.object(doctor.tempfile, "TemporaryFile", side_effect=PermissionError(13, "denied")):
            check = doctor.check_writable("x", self.root)
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn(str(self.root), check.fix)

    def test_path_blocked_by_a_file_fails(self):
        (self.root / "f").write_text("")
        check = doctor.check_writable("x", self.root / "f" / "sub")
        self.assertEqual(check.status, doctor.FAIL)


class TestNetwork(unittest.TestCase):
    def _probe(self, **urlopen):
        with mock.patch.object(doctor.urllib.request, "urlopen", **urlopen):
            return _REAL_REACHABLE("openrouter", doctor._PROVIDERS["openrouter"])

    def test_auth_error_still_means_reachable(self):
        err = urllib.error.HTTPError("u", 401, "no", {}, None)
        self.assertEqual(self._probe(side_effect=err).status, doctor.OK)

    def test_server_error_warns(self):
        err = urllib.error.HTTPError("u", 503, "down", {}, None)
        self.assertEqual(self._probe(side_effect=err).status, doctor.WARN)

    def test_unreachable_fails_with_next_step(self):
        check = self._probe(side_effect=urllib.error.URLError("no route"))
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn("curl", check.fix)

    def test_timeout_fails(self):
        self.assertEqual(self._probe(side_effect=TimeoutError("slow")).status, doctor.FAIL)

    def test_probe_sends_no_credentials(self):
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-SECRET"}):
            with mock.patch.object(doctor.urllib.request, "urlopen", return_value=response) as call:
                _REAL_REACHABLE("openrouter", doctor._PROVIDERS["openrouter"])
        request = call.call_args.args[0]
        self.assertFalse(request.headers)


class TestCaches(_TmpDirCase):
    def test_absent_cache_is_ok(self):
        self.assertEqual(doctor.check_scored_cache("c", self.root / "none").status, doctor.OK)

    def test_old_entries_warn_with_next_step(self):
        old = self.root / "skill" / "k.json"
        old.parent.mkdir()
        old.write_text("{}")
        age = time.time() - 60 * 86400
        os.utime(old, (age, age))
        check = doctor.check_scored_cache("c", self.root)
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn(f"rm -rf {self.root}", check.fix)

    def test_fresh_entries_pass(self):
        (self.root / "k.json").write_text("{}")
        self.assertEqual(doctor.check_scored_cache("c", self.root).status, doctor.OK)

    def test_old_hub_clone_warns(self):
        head = self.root / ".git" / "HEAD"
        head.parent.mkdir()
        head.write_text("ref")
        age = time.time() - 60 * 86400
        os.utime(head, (age, age))
        check = doctor.check_skills_hub_clone(self.root)
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("pull", check.fix)


class TestJson(_TmpDirCase):
    def test_same_findings_as_text_and_same_exit_code(self):
        agent = self._adk_agent("OPENROUTER_API_KEY=your_key_here\n")
        text_code, text = self._run(cwd=agent)
        json_code, out = self._run(cwd=agent, as_json=True)
        report = json.loads(out)
        self.assertEqual(json_code, text_code)
        self.assertEqual(report["exit_code"], json_code)
        self.assertFalse(report["ok"])
        for check in report["checks"]:
            self.assertIn(check["name"], text)
            self.assertIn(f"[{check['status']}]", text)
            if check["fix"]:
                self.assertIn(check["fix"], text)
        total = report["summary"]
        self.assertEqual(sum(total.values()), len(report["checks"]))

    def test_chatty_import_cannot_corrupt_json(self):
        original = doctor.run_install_checks

        def chatty():
            print("banner printed by some import")
            return original()

        with mock.patch.object(doctor, "run_install_checks", chatty):
            with contextlib.redirect_stderr(io.StringIO()):
                _, out = self._run(cwd=self.root, as_json=True)
        json.loads(out)

    def test_secret_values_never_appear(self):
        agent = self._adk_agent("OPENROUTER_API_KEY=sk-or-TOPSECRET\nnot a valid sk-or-TOPSECRET line\n")
        for as_json in (False, True):
            _, out = self._run(cwd=agent, as_json=as_json)
            self.assertNotIn("TOPSECRET", out)

    def test_cli_flag(self):
        from nuvel.cli import main

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["doctor", "--json", "--path", str(self._adk_agent())])
        self.assertEqual(json.loads(buf.getvalue())["exit_code"], code)

    def test_missing_path_fails_with_next_step(self):
        code, out = self._run(cwd=self.root / "nope", as_json=True)
        self.assertEqual(code, 1)
        fails = [c for c in json.loads(out)["checks"] if c["status"] == doctor.FAIL]
        self.assertIn("nuvel doctor --path", fails[-1]["fix"])


class TestBrokenInstall(_TmpDirCase):
    def test_doctor_reports_a_missing_dependency_instead_of_crashing(self):
        # yaml is imported by the full CLI parser's subcommands; nuvel_mcp too.
        for module in ("yaml", "nuvel_mcp"):
            script = (
                "import sys\n"
                f"sys.modules[{module!r}] = None\n"
                "from nuvel import doctor\n"
                "from nuvel.cli import main\n"
                "doctor.check_provider_reachable = lambda name, provider, **_: "
                "doctor.Check(name, doctor.OK)\n"
                "sys.exit(main(['doctor', '--json', '--path', sys.argv[1]]))\n"
            )
            proc = subprocess.run(
                [sys.executable, "-c", script, str(self.root)],
                capture_output=True, text=True, timeout=120,
            )
            self.assertNotIn("Traceback", proc.stderr, module)
            self.assertEqual(proc.returncode, 1, module)
            json.loads(proc.stdout)


class TestLazyBots(unittest.TestCase):
    def _python(self, code):
        return subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
        )

    def test_attribute_access_and_from_import_both_work(self):
        for code in (
            "import nuvel; print(nuvel.bots.__name__)",
            "from nuvel import bots; print(bots.__name__)",
        ):
            proc = self._python(code)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), "nuvel.bots")

    def test_importing_the_package_does_not_load_bots(self):
        proc = self._python("import sys, nuvel; print('nuvel.bots' in sys.modules)")
        self.assertEqual(proc.stdout.strip(), "False", proc.stderr)


@unittest.skipIf(os.geteuid() == 0, "root ignores directory permissions")
class TestNoTraceback(_TmpDirCase):
    def _lock(self, path: Path) -> None:
        path.chmod(0)
        self.addCleanup(path.chmod, 0o755)

    def test_unreadable_path_fails_with_next_step_in_text_and_json(self):
        locked = self.root / "locked"
        locked.mkdir()
        self._lock(locked)
        code, out = self._run(cwd=locked)
        self.assertEqual(code, 1)
        self.assertIn(f"chmod u+rx {locked}", out)
        code, out = self._run(cwd=locked, as_json=True)
        self.assertEqual(code, 1)
        target = [c for c in json.loads(out)["checks"] if c["section"] == "Target"]
        self.assertEqual(target[0]["status"], doctor.FAIL)
        self.assertTrue(target[0]["fix"])

    def test_path_below_an_unreadable_dir(self):
        locked = self.root / "locked"
        (locked / "inner").mkdir(parents=True)
        self._lock(locked)
        code, out = self._run(cwd=locked / "inner")
        self.assertEqual(code, 1)
        self.assertIn("cannot be read", out)

    def test_unreadable_subdirectory_does_not_stop_the_report(self):
        sub = self.root / "lost+found"
        sub.mkdir()
        self._lock(sub)
        self._adk_agent()
        self.assertTrue(doctor.detect_agent(self.root).is_agent)
        code, out = self._run(cwd=self.root)
        self.assertIn("Summary", out)
        self.assertIn(code, (0, 1))
        self.assertNotIn("check raised", out)

    def test_unreadable_hermes_config_says_how_to_fix_it(self):
        profile = self.root / "bot"
        profile.mkdir()
        (profile / "config.yaml").write_text("model: {provider: openrouter}\n")
        self._lock(profile / "config.yaml")
        check, _, _ = doctor.check_hermes_config(profile)
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn("chmod u+r", check.fix)


class TestSymlinkLoopPath(_TmpDirCase):
    def _loop(self) -> Path:
        loop = self.root / "loop"
        loop.symlink_to(loop)
        return loop

    def test_text_names_a_next_step(self):
        code, out = self._run(cwd=self._loop())
        self.assertEqual(code, 1)
        self.assertIn('nuvel doctor --path "$HOME"', out)
        self.assertIn("Summary", out)

    def test_json_still_reports(self):
        loop = self._loop()
        code, out = self._run(cwd=loop, as_json=True)
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(report["cwd"], str(loop))
        target = [c for c in report["checks"] if c["section"] == "Target"]
        self.assertEqual(target[0]["status"], doctor.FAIL)
        self.assertTrue(target[0]["fix"])

    def test_cli_exits_without_a_traceback(self):
        proc = subprocess.run(
            [sys.executable, "-m", "nuvel.cli", "doctor", "--path", str(self._loop())],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("Traceback", proc.stderr)


class TestRequirementsEncoding(_TmpDirCase):
    def _agent_with_requirements(self, raw: bytes) -> Path:
        self._adk_agent()
        (self.root / "requirements.txt").write_bytes(raw)
        return self.root / "requirements.txt"

    def test_stray_byte_keeps_the_framework_and_every_section(self):
        req = self._agent_with_requirements(b"# caf\xe9\ngoogle-adk==1.26.0\n")
        code, out = self._run(cwd=self.root, as_json=True)
        self.assertEqual(code, 1)
        checks = json.loads(out)["checks"]
        found = next(c for c in checks if c["name"] == "requirements.txt")
        self.assertEqual(found["status"], doctor.FAIL)
        self.assertEqual(found["fix"], f"Re-save {req} as UTF-8")
        self.assertEqual(
            [title for title in dict.fromkeys(c["section"] for c in checks)],
            ["Install", "Agent (adk)", "State", "Network", "Caches"],
        )

    def test_utf16_file_is_still_an_agent_that_says_to_resave(self):
        self._agent_with_requirements("google-adk==1.26.0\n".encode("utf-16"))
        code, out = self._run(cwd=self.root)
        self.assertEqual(code, 1)
        self.assertIn("as UTF-8", out)
        self.assertNotIn("check raised", out)
        self.assertNotIn("Report this", out)

    def test_utf8_requirements_add_no_finding(self):
        self._adk_agent()
        self.assertEqual(doctor.detect_agent(self.root).problems, [])


class TestStdoutEncoding(_TmpDirCase):
    def _run_encoded(self, encoding: str, cwd: Path | None = None) -> str:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding=encoding)
        with mock.patch.object(doctor.sys, "stdout", stream):
            doctor.run_doctor(cwd=cwd or self.root)
            stream.flush()
        return raw.getvalue().decode(encoding)

    def test_legacy_encodings_print_the_whole_report(self):
        for encoding in ("cp1252", "ascii"):
            with self.subTest(encoding=encoding):
                out = self._run_encoded(encoding)
                self.assertIn("-> ", out)
                self.assertIn("Summary", out)

    def test_utf8_keeps_the_arrow(self):
        self.assertIn("→ ", self._run_encoded("utf-8"))

    def test_path_stdout_cannot_encode_does_not_crash(self):
        target = self.root / "naïve→日本"
        target.mkdir()
        out = self._run_encoded("ascii", cwd=target)
        self.assertIn(r"na\xefve", out)
        self.assertIn("Summary", out)

    def test_undecodable_filename_does_not_crash_utf8(self):
        out = self._run_encoded("utf-8", cwd=Path(self.root, "gone\udcff"))
        self.assertIn(r"gone\udcff", out)


class TestCommandsAreShellQuoted(_TmpDirCase):
    def _split(self, fix: str, command: str) -> list[str]:
        return shlex.split(fix[fix.index(command):])

    def setUp(self):
        super().setUp()
        self.spaced = self.root / "my dir"
        self.spaced.mkdir()

    def test_stale_cache_rm_targets_one_path(self):
        (self.spaced / "k.json").symlink_to(self.root / "nowhere")
        fix = doctor.check_scored_cache("c", self.spaced).fix
        self.assertEqual(self._split(fix, "rm -rf"), ["rm", "-rf", str(self.spaced)])

    def test_broken_hub_clone_rm_targets_one_path(self):
        (self.spaced / ".git").mkdir()
        fix = doctor.check_skills_hub_clone(self.spaced).fix
        self.assertEqual(self._split(fix, "rm -rf"), ["rm", "-rf", str(self.spaced)])

    def test_stale_hub_clone_pull_targets_one_path(self):
        (self.spaced / ".git").mkdir()
        (self.spaced / ".git" / "HEAD").write_text("ref")
        old = time.time() - 90 * 86400
        os.utime(self.spaced / ".git" / "HEAD", (old, old))
        fix = doctor.check_skills_hub_clone(self.spaced).fix
        self.assertEqual(self._split(fix, "git -C"), ["git", "-C", str(self.spaced), "pull", "--ff-only"])

    def test_agent_env_cp_targets_two_paths(self):
        (self.spaced / "requirements.txt").write_text("google-adk==1.26.0\n")
        (self.spaced / "agent.py").write_text("# agent")
        (self.spaced / ".env.example").write_text("X=1\n")
        info = doctor.detect_agent(self.spaced)
        fix = self._check(doctor.run_agent_checks(self.spaced, info), ".env file").fix
        self.assertEqual(
            self._split(fix, "cp "),
            ["cp", f"{self.spaced}/.env.example", f"{self.spaced}/.env"],
        )

    def test_unreadable_target_chmod_targets_one_path(self):
        self.spaced.chmod(0)
        self.addCleanup(self.spaced.chmod, 0o755)
        self.assertIn(f"chmod u+rx {shlex.quote(str(self.spaced))}", doctor.check_target(self.spaced).fix)

    def test_crashed_check_ls_targets_one_path(self):
        def boom():
            raise PermissionError(13, "Permission denied", "/some where")

        fix = doctor._safe(boom, "x").fix
        self.assertEqual(self._split(fix, "ls -ld"), ["ls", "-ld", "/some where"])

    def test_reinstall_from_a_spaced_checkout(self):
        with mock.patch.object(doctor, "_source_root", return_value=self.spaced):
            fix = doctor._reinstall_fix()
        self.assertEqual(
            self._split(fix, "/")[-4:],
            ["-e", f"{self.spaced}/packages/nuvel-mcp", "-e", str(self.spaced)],
        )


class TestDeletedCwd(_TmpDirCase):
    def _doctor_in_deleted_cwd(self, *args):
        script = (
            "import os, sys\n"
            "os.mkdir(sys.argv[1]); os.chdir(sys.argv[1]); os.rmdir(sys.argv[1])\n"
            "from nuvel import doctor\n"
            "from nuvel.cli import main\n"
            "doctor.check_provider_reachable = lambda name, provider, **_: "
            "doctor.Check(name, doctor.OK)\n"
            "sys.exit(main(['doctor', *sys.argv[2:]]))\n"
        )
        repo = Path(__file__).resolve().parent.parent
        env = {**os.environ, "PYTHONPATH": str(repo)}
        return subprocess.run(
            [sys.executable, "-c", script, str(self.root / "gone"), *args],
            capture_output=True, text=True, timeout=120, env=env,
        )

    def test_text(self):
        proc = self._doctor_in_deleted_cwd()
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no longer exists", proc.stdout)
        self.assertIn('cd "$HOME" && nuvel doctor', proc.stdout)

    def test_json(self):
        proc = self._doctor_in_deleted_cwd("--json")
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.returncode, 1)
        self.assertIsNone(json.loads(proc.stdout)["cwd"])

    def test_relative_path_argument(self):
        proc = self._doctor_in_deleted_cwd("--path", ".")
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.returncode, 1)


class TestActionableNextSteps(_TmpDirCase):
    def test_invalid_yaml_date_is_a_finding_not_a_crash(self):
        profile = self.root / "bot"
        profile.mkdir()
        (profile / "config.yaml").write_text("d: 2001-13-45\nmodel: {provider: openrouter}\n")
        (profile / "SOUL.md").write_text("soul")
        code, out = self._run(cwd=self.root)
        self.assertEqual(code, 1)
        self.assertIn(f"Fix the YAML in {profile}/config.yaml", out)

    def test_dangling_cache_symlink_says_to_clear_the_cache(self):
        (self.root / "k.json").symlink_to(self.root / "nowhere")
        check = doctor.check_scored_cache("c", self.root)
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn(f"rm -rf {self.root}", check.fix)

    def test_broken_hub_clone_says_to_remove_it(self):
        (self.root / ".git").mkdir()
        check = doctor.check_skills_hub_clone(self.root)
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn(f"rm -rf {self.root}", check.fix)

    def test_unparsable_pyproject_says_to_restore_it(self):
        (self.root / "pyproject.toml").write_text("not [ toml")
        with mock.patch.object(doctor, "_source_root", return_value=self.root):
            check = doctor.check_distribution()
        self.assertEqual(check.status, doctor.FAIL)
        self.assertNotEqual(check.fix, doctor._REPORT_FIX)
        self.assertIn("pyproject.toml", check.fix)

    def test_pricing_that_is_not_an_object_fails_with_next_step(self):
        plugins = self.root / "pkg" / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "pricing.json").write_text("[]")
        checks = doctor.check_pricing_coverage(self.root, {})
        self.assertEqual(checks[0].status, doctor.FAIL)
        self.assertIn("Fix the JSON", checks[0].fix)

    def test_crashed_check_on_a_path_points_at_the_path(self):
        def boom():
            raise PermissionError(13, "Permission denied", "/some/where")

        check = doctor._safe(boom, "x")
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn("/some/where", check.fix)


class TestOldPython(unittest.TestCase):
    def test_module_imports_without_tomllib(self):
        # tomllib is stdlib only from 3.11: its import must not kill the report.
        script = (
            "import sys\n"
            "sys.modules['tomllib'] = None\n"
            "from nuvel import doctor\n"
            "print(doctor.check_distribution().status)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
        )
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn(proc.stdout.strip(), (doctor.OK, doctor.FAIL))

    def test_old_python_fails_with_next_step(self):
        version = mock.MagicMock()
        version.__getitem__.return_value = (3, 10)
        version.micro = 4
        with mock.patch.object(doctor.sys, "version_info", version):
            check = doctor.check_python_version()
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn("3.11", check.fix)


class TestUnknownProvider(_TmpDirCase):
    def test_missing_key_exits_nonzero_and_names_it(self):
        agent = self._adk_agent(
            "FAST_MODEL=moonshotai/kimi-k2.5\nREASONING_MODEL=moonshotai/kimi-k2.5\n"
        )
        code, out = self._run(cwd=agent)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]  MOONSHOTAI_API_KEY", out)
        self.assertIn("openrouter", out)

    def test_set_key_exits_zero_and_warns_it_was_not_probed(self):
        agent = self._adk_agent(
            "FAST_MODEL=acme/m\nREASONING_MODEL=acme/m\nACME_API_KEY=acme-test-key-value\n"
        )
        code, out = self._run(cwd=agent)
        self.assertEqual(code, 0, out)
        self.assertIn("[WARN]  acme reachable", out)

    def test_probes_known_provider_alongside_an_unknown_one(self):
        names = {c.name: c.status for c in doctor.run_network_checks(["acme", "openrouter"])}
        self.assertEqual(names, {"acme reachable": doctor.WARN, "openrouter reachable": doctor.OK})

    def test_provider_without_a_single_key_warns(self):
        agent = self._adk_agent("FAST_MODEL=bedrock/m\nOPENROUTER_API_KEY=sk\n")
        checks = doctor.run_agent_checks(agent, doctor.detect_agent(agent))
        self.assertEqual(self._check(checks, "provider bedrock").status, doctor.WARN)

    def test_bare_model_id_maps_to_its_provider(self):
        agent = self._adk_agent("FAST_MODEL=gpt-4o\nOPENROUTER_API_KEY=sk\n")
        checks = doctor.run_agent_checks(agent, doctor.detect_agent(agent))
        self.assertEqual(self._check(checks, "OPENAI_API_KEY").status, doctor.FAIL)


class TestGatewaySetup(_TmpDirCase):
    """Documented setup: cp .env.example .env, then the values the README asks for."""

    _REAL_VALUES = {
        "your_openrouter_api_key_here": "sk-or-real-value",
        "your_composio_api_key_here": "ak-0123456789abcdef",
        "your_telegram_bot_token_here": "123:real-value",
        "change_me_to_a_long_random_string": "real-random-secret",
    }

    def _scaffold(self, **flags) -> Path:
        from nuvel.backends.adk.scaffold import scaffold_agent

        result = scaffold_agent("my-agent", output_dir=str(self.root), **flags)
        return Path(result["path"])

    def _configure(self, agent: Path, keep: tuple[str, ...] = ()) -> None:
        env = (agent / ".env.example").read_text()
        for placeholder, value in self._REAL_VALUES.items():
            if placeholder not in keep:
                env = env.replace(placeholder, value)
        (agent / ".env").write_text(env)

    def _doctor(self, agent: Path):
        with mock.patch.object(doctor, "_check_docker_running", return_value=doctor.Check("Docker", doctor.OK)):
            return self._run(cwd=agent)

    def test_each_channel_is_green_after_documented_setup(self):
        for flags in ({"with_slack": True}, {"with_telegram": True}, {"with_teams": True}):
            with self.subTest(flags), tempfile.TemporaryDirectory() as tmp:
                self.root = Path(tmp).resolve()
                agent = self._scaffold(**flags)
                self._configure(agent)
                code, out = self._doctor(agent)
                self.assertEqual(code, 0, out)

    def test_slack_bot_token_only_warns(self):
        agent = self._scaffold(with_slack=True)
        self._configure(agent)
        _, out = self._doctor(agent)
        self.assertIn("[WARN]  gateway:slack SLACK_BOT_TOKEN", out)

    def test_change_me_placeholder_fails(self):
        agent = self._scaffold(with_slack=True)
        self._configure(agent, keep=("change_me_to_a_long_random_string",))
        code, out = self._doctor(agent)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]  gateway:slack COMPOSIO_WEBHOOK_SECRET", out)

    def test_telegram_webhook_secret_is_required(self):
        agent = self._scaffold(with_telegram=True)
        self._configure(agent, keep=("change_me_to_a_long_random_string",))
        code, out = self._doctor(agent)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]  gateway:telegram TELEGRAM_WEBHOOK_SECRET", out)


class TestHermesSetup(_TmpDirCase):
    def _create(self, name: str, *flags: str) -> Path:
        from nuvel.cli import main

        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["agent", "create", name, "--framework", "hermes", "--output-dir", str(self.root), *flags])
        self.assertEqual(code, 0)
        return self.root / name

    def _configure(self, project: Path, name: str, token: str = "") -> None:
        profile = project / name
        env = (profile / ".env.example").read_text()
        env = env.replace("your_openrouter_api_key_here", "sk-or-real-value")
        if token:
            env += f"\nTELEGRAM_BOT_TOKEN={token}\n"
        (profile / ".env").write_text(env)

    def test_cli_creates_a_profile_that_is_green_after_documented_setup(self):
        project = self._create("bot")
        code, _ = self._run(cwd=project)
        self.assertEqual(code, 1)
        self._configure(project, "bot")
        code, out = self._run(cwd=project)
        self.assertEqual(code, 0, out)

    def test_litellm_flag_is_rejected_not_a_typeerror(self):
        from nuvel.backends.hermes.scaffold import scaffold_agent

        result = scaffold_agent("bot", output_dir=str(self.root), with_litellm=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("--with-litellm", result["message"])

    def test_enabled_platform_needs_its_token(self):
        project = self._create("bot", "--with-telegram")
        self._configure(project, "bot")
        code, out = self._run(cwd=project)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]  platform:telegram TELEGRAM_BOT_TOKEN", out)
        self._configure(project, "bot", token="123:real-value")
        code, out = self._run(cwd=project)
        self.assertEqual(code, 0, out)

    def test_unknown_enabled_platform_warns(self):
        profile = self.root / "bot"
        profile.mkdir()
        (profile / "config.yaml").write_text(
            "model: {provider: openrouter}\nplatforms: {matrix: {enabled: true}}\n"
        )
        (profile / "SOUL.md").write_text("soul")
        checks = doctor.run_agent_checks(self.root, doctor.detect_agent(self.root))
        self.assertEqual(self._check(checks, "platform:matrix").status, doctor.WARN)

    def test_config_is_parsed_once(self):
        project = self._create("bot")
        self._configure(project, "bot")
        with mock.patch.object(doctor, "check_hermes_config", wraps=doctor.check_hermes_config) as parse:
            self._run(cwd=project)
        self.assertEqual(parse.call_count, 1)


class TestAcpSetup(_TmpDirCase):
    def _agent(self) -> Path:
        from nuvel.backends.adk.scaffold import scaffold_agent

        return Path(scaffold_agent("my-agent", output_dir=str(self.root), with_acp=True)["path"])

    def test_scaffolded_adapter_answers_initialize(self):
        check = doctor.check_acp_handshake(self._agent(), "my_agent", timeout=120.0)
        self.assertEqual(check.status, doctor.OK, check.detail)

    def test_json_carries_the_zed_config(self):
        agent = self._agent()
        ok = [doctor.Check("ACP stdio handshake", doctor.OK)]
        with mock.patch.object(doctor, "run_acp_checks", return_value=ok):
            _, out = self._run(cwd=agent, as_json=True)
        self.assertIn("my-agent", json.loads(out)["zed_config"]["agent_servers"])


class TestHubLocation(_TmpDirCase):
    def test_hub_clone_ignores_xdg_like_nuvel_bots_does(self):
        head = Path.home() / ".local" / "share" / "nuvel" / "skills" / ".git" / "HEAD"
        head.parent.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, Path.home() / ".local")
        head.write_text("ref")
        age = time.time() - 60 * 86400
        os.utime(head, (age, age))
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root)}):
            checks = doctor.run_cache_checks()
        self.assertEqual(self._check(checks, "skills hub clone").status, doctor.WARN)


class TestReinstallFix(unittest.TestCase):
    def test_checkout_installs_the_unpublished_workspace_member_first(self):
        fix = doctor._reinstall_fix()
        root = doctor._source_root()
        self.assertIn("source checkout", fix)
        self.assertEqual(
            shlex.split(fix.split(": ", 1)[1]),
            [sys.executable, "-m", "pip", "install", "-e", f"{root}/packages/nuvel-mcp", "-e", str(root)],
        )

    def test_published_install_says_so(self):
        with mock.patch.object(doctor, "_source_root", return_value=None):
            fix = doctor._reinstall_fix()
        self.assertIn("published package", fix)
        self.assertTrue(fix.endswith("install --force-reinstall nuvel-cli"), fix)
        self.assertNotIn("-e", shlex.split(fix))

    def test_venv_without_pip_goes_through_uv(self):
        with (
            mock.patch.object(doctor.importlib.util, "find_spec", return_value=None),
            mock.patch.object(doctor.shutil, "which", return_value="/bin/uv"),
        ):
            command = doctor._pip_install("nuvel-cli")
        self.assertEqual(shlex.split(command), ["uv", "pip", "install", "--python", sys.executable, "nuvel-cli"])

    def test_venv_without_pip_or_uv_bootstraps_pip(self):
        with (
            mock.patch.object(doctor.importlib.util, "find_spec", return_value=None),
            mock.patch.object(doctor.shutil, "which", return_value=None),
        ):
            command = doctor._pip_install("nuvel-cli")
        bootstrap, install = command.split(" && ")
        self.assertEqual(shlex.split(bootstrap), [sys.executable, "-m", "ensurepip"])
        self.assertEqual(shlex.split(install), [sys.executable, "-m", "pip", "install", "nuvel-cli"])

    def test_pipx_install_reinstalls_through_pipx(self):
        with (
            mock.patch.object(doctor, "_source_root", return_value=None),
            mock.patch.object(doctor.sys, "prefix", "/home/me/.local/pipx/venvs/nuvel-cli"),
            mock.patch.object(doctor.shutil, "which", return_value="/bin/pipx"),
        ):
            self.assertEqual(doctor._reinstall_fix(), "Reinstall the published package: pipx reinstall nuvel-cli")

    def test_pipx_venv_without_pipx_on_path_falls_back_to_pip(self):
        with (
            mock.patch.object(doctor, "_source_root", return_value=None),
            mock.patch.object(doctor.sys, "prefix", "/home/me/.local/pipx/venvs/nuvel-cli"),
            mock.patch.object(doctor.shutil, "which", return_value=None),
        ):
            self.assertNotIn("pipx reinstall", doctor._reinstall_fix())

    def test_pipx_prefix_in_a_checkout_still_gets_the_checkout_hint(self):
        with (
            mock.patch.object(doctor.sys, "prefix", "/home/me/.local/pipx/venvs/nuvel-cli"),
            mock.patch.object(doctor.shutil, "which", return_value="/bin/pipx"),
        ):
            self.assertIn("source checkout", doctor._reinstall_fix())


class TestPythonUpgradeFix(unittest.TestCase):
    def _steps(self, fix: str) -> list[list[str]]:
        return [shlex.split(step) for step in fix.split(": ", 1)[1].split(" && ")]

    def setUp(self):
        patcher = mock.patch.object(doctor, "_usable_python", return_value="python3.12")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_checkout_rebuilds_its_venv_with_the_workspace_member(self):
        with mock.patch.object(doctor, "_source_root", return_value=Path("/my dir")):
            fix = doctor._python_upgrade_fix()
        self.assertIn("source checkout", fix)
        self.assertNotIn("pipx", fix)
        self.assertEqual(self._steps(fix), [
            ["python3.12", "-m", "venv", "/my dir/.venv"],
            ["/my dir/.venv/bin/python", "-m", "pip", "install", "-e", "/my dir/packages/nuvel-mcp", "-e", "/my dir"],
        ])

    def test_published_install_makes_a_new_venv(self):
        with mock.patch.object(doctor, "_source_root", return_value=None):
            fix = doctor._python_upgrade_fix()
        venv = f"{Path.home()}/.nuvel-venv"
        self.assertIn("published package", fix)
        self.assertEqual(self._steps(fix), [
            ["python3.12", "-m", "venv", venv],
            [f"{venv}/bin/python", "-m", "pip", "install", "nuvel-cli"],
        ])

    def test_no_usable_python_prints_no_command_that_cannot_run(self):
        for root in (Path("/my dir"), None):
            with (
                self.subTest(root=root),
                mock.patch.object(doctor, "_source_root", return_value=root),
                mock.patch.object(doctor, "_usable_python", return_value=None),
            ):
                fix = doctor._python_upgrade_fix()
                self.assertIn("https://www.python.org/downloads/", fix)
                self.assertIn("run nuvel doctor again", fix)
                self.assertNotIn(" -m venv", fix)


class TestUsablePython(_TmpDirCase):
    """Real scripts on a real PATH, as the probe has to run them to believe them."""

    def _path(self, **scripts: int) -> mock._patch:
        for name, code in scripts.items():
            path = self.root / name.replace("_", ".")
            path.write_text(f"#!/bin/sh\nexit {code}\n")
            path.chmod(0o755)
        return mock.patch.dict(os.environ, {"PATH": str(self.root)})

    def test_takes_a_python_the_probe_accepts(self):
        with self._path(python3_12=0):
            self.assertEqual(doctor._usable_python(), "python3.12")

    def test_too_old_python3_is_skipped_for_a_newer_one(self):
        with self._path(python3=1, python3_11=0):
            self.assertEqual(doctor._usable_python(), "python3.11")

    def test_nothing_usable_is_none(self):
        with self._path(python3=1, python3_13=127):
            self.assertIsNone(doctor._usable_python())


class TestDockerStartFix(unittest.TestCase):
    def _fix(self, platform: str, dirs: tuple[str, ...], systemctl_code: int = 0) -> str:
        with (
            mock.patch.object(doctor.sys, "platform", platform),
            mock.patch.object(doctor.os.path, "isdir", side_effect=lambda path: path in dirs),
            mock.patch.object(
                doctor.subprocess, "run",
                return_value=subprocess.CompletedProcess([], systemctl_code),
            ),
        ):
            return doctor._docker_start_fix()

    def test_systemd_hint_only_where_systemd_runs_docker(self):
        systemd = ("/run/systemd/system",)
        self.assertIn("sudo systemctl start docker", self._fix("linux", systemd))
        self.assertNotIn("systemctl", self._fix("linux", systemd, systemctl_code=1))
        self.assertNotIn("systemctl", self._fix("linux", ()))

    def test_macos_opens_docker_desktop_only_if_installed(self):
        self.assertIn("open -a Docker", self._fix("darwin", ("/Applications/Docker.app",)))
        fix = self._fix("darwin", ())
        self.assertNotIn("open -a", fix)
        self.assertNotIn("systemctl", fix)
        self.assertIn("docker info", fix)

    def test_missing_systemctl_is_not_a_crash(self):
        with (
            mock.patch.object(doctor.os.path, "isdir", return_value=True),
            mock.patch.object(doctor.subprocess, "run", side_effect=FileNotFoundError("systemctl")),
        ):
            self.assertFalse(doctor._systemd_manages_docker())


class TestComposioKey(_TmpDirCase):
    def _check(self, value: str) -> doctor.Check:
        (self.root / "requirements.txt").write_text("google-adk\n")
        (self.root / "agent.py").write_text("")
        (self.root / "tools").mkdir(exist_ok=True)
        (self.root / "tools" / "composio_mcp.py").write_text("")
        (self.root / ".env").write_text(f"COMPOSIO_API_KEY={value}\n")
        checks = doctor.run_agent_checks(self.root, doctor.detect_agent(self.root))
        return next(c for c in checks if c.name == "COMPOSIO_API_KEY")

    def test_values_that_are_not_keys_fail_with_shape_and_source(self):
        for value in ("", "'not a key'", "<your key>", "your_composio_api_key_here", "abc"):
            with self.subTest(value=value):
                check = self._check(value)
                self.assertEqual(check.status, doctor.FAIL)
                self.assertIn("long random string", check.fix)
                self.assertIn(doctor._COMPOSIO_KEYS_PAGE, check.fix)

    def test_real_shaped_key_passes(self):
        self.assertEqual(self._check("ak_0123456789abcdef").status, doctor.OK)


class TestApiKeyValue(unittest.TestCase):
    def _check(self, value: str, name: str = "openrouter", **env: str) -> doctor.Check:
        checks = doctor._provider_key_checks(
            [name], {doctor._provider_key(name): value, **env}, lambda key: f"Add {key}=<your key> to .env",
        )
        return checks[0]

    def test_values_that_are_not_keys_fail_without_being_echoed(self):
        for value in (
            "", "   ", "<your key>", "*** key>", "<value>", "***", "...", "your key", "your-api-key",
            "your_key_here", "sk-or-", "sk-test", "abc", "sk-ant-api03-a-different-providers-key",
            "sk-or-v1-has a-space-in-it-000", "sk-or-v1-nön-äscii-value-000",
            "placeholder-placeholder", "sk-or-placeholder-value", "sk-or-example-key-value",
            "sk-or-fake-key-1234567890", "not a key", "sk-or-aaaaaaaaaaaaaaaaaaaa",
        ):
            with self.subTest(value=value):
                check = self._check(value)
                self.assertEqual(check.status, doctor.FAIL)
                if value.strip():
                    self.assertNotIn(value, check.detail)
                self.assertIn("sk-or-", check.fix)
                self.assertIn("https://openrouter.ai/keys", check.fix)

    def test_each_provider_names_its_own_key_shape(self):
        for name, prefix in (("anthropic", "sk-ant-"), ("openai", "sk-"), ("groq", "gsk_")):
            with self.subTest(provider=name):
                self.assertEqual(self._check(f"{prefix}0123456789abcdef", name).status, doctor.OK)
                check = self._check("not-this-providers-format", name)
                self.assertEqual(check.status, doctor.FAIL)
                self.assertIn(f"starts with {prefix}", check.detail + check.fix)
                self.assertIn(doctor._PROVIDERS[name].keys_page, check.fix)

    def test_real_shaped_keys_pass(self):
        self.assertEqual(self._check("sk-or-v1-" + "0a" * 32).status, doctor.OK)
        self.assertEqual(self._check("AIzaSy" + "A1b2" * 8, "gemini").status, doctor.OK)

    def test_unknown_provider_gets_the_generic_shape_rules(self):
        self.assertEqual(self._check("acme-0123456789abcdef", "acme").status, doctor.OK)
        check = self._check("short", "acme")
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn("long random string from acme's dashboard", check.fix)

    def test_custom_endpoint_does_not_excuse_a_value_that_is_not_a_key(self):
        for env in ({"OPENROUTER_API_BASE": "http://proxy:4000"}, {"OPENROUTER_BASE_URL": "http://proxy:4000"}):
            for value in ("not a key", "<your key>", "placeholder-placeholder"):
                with self.subTest(env=env, value=value):
                    self.assertEqual(self._check(value, **env).status, doctor.FAIL)

    def test_custom_endpoint_accepts_the_proxys_own_key_format(self):
        for env in ({"OPENROUTER_API_BASE": "http://proxy:4000"}, {"OPENROUTER_BASE_URL": "http://proxy:4000"}):
            with self.subTest(env=env):
                self.assertEqual(self._check("sk-1234", **env).status, doctor.OK)
        self.assertEqual(self._check("sk-1234").status, doctor.FAIL)

    def test_placeholder_without_a_known_key_shape_fails(self):
        self.assertEqual(self._check("placeholder-placeholder", "gemini").status, doctor.FAIL)
        self.assertEqual(self._check("placeholder-placeholder", "acme").status, doctor.FAIL)

    def test_webhook_secrets_keep_the_placeholder_rule_only(self):
        self.assertEqual(doctor._check_env_key("SECRET", {"SECRET": "short"}).status, doctor.OK)
        self.assertEqual(doctor._check_env_key("SECRET", {"SECRET": "<value>"}).status, doctor.FAIL)


class TestDoctorsOwnHintIsNotAKey(_TmpDirCase):
    def test_placeholder_in_env_file_fails_in_text_and_json_alike(self):
        (self.root / "requirements.txt").write_text("google-adk\n")
        (self.root / "agent.py").write_text("")
        (self.root / ".env").write_text("OPENROUTER_API_KEY=<your key>\n")
        code, text = self._run(cwd=self.root)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]  OPENROUTER_API_KEY", text)
        json_code, out = self._run(cwd=self.root, as_json=True)
        report = json.loads(out)
        self.assertEqual(json_code, 1)
        check = next(c for c in report["checks"] if c["name"] == "OPENROUTER_API_KEY")
        self.assertEqual(check["status"], doctor.FAIL)
        self.assertIn(check["detail"], text)
        self.assertIn(check["fix"], text)


class TestHermesEnvLocation(_TmpDirCase):
    def _profile(self) -> Path:
        profile = self.root / "bot"
        profile.mkdir()
        (profile / "config.yaml").write_text("model: {provider: openrouter}\n")
        (profile / "SOUL.md").write_text("soul")
        return profile

    def _env_checks(self, profile: Path) -> tuple[doctor.Check, doctor.Check]:
        checks = doctor.run_agent_checks(self.root, doctor.detect_agent(self.root))
        return self._check(checks, ".env file"), self._check(checks, "OPENROUTER_API_KEY")

    def test_env_in_a_parent_directory_does_not_count(self):
        profile = self._profile()
        (self.root / ".env").write_text("OPENROUTER_API_KEY=sk-or-test-key-value\n")
        env_file, key = self._env_checks(profile)
        self.assertEqual((env_file.status, key.status), (doctor.FAIL, doctor.FAIL))
        self.assertIn(f"{profile}/.env", env_file.fix)
        self.assertIn(f"{profile}/.env", key.fix)

    def test_env_in_the_profile_counts(self):
        profile = self._profile()
        (profile / ".env").write_text("OPENROUTER_API_KEY=sk-or-test-key-value\n")
        env_file, key = self._env_checks(profile)
        self.assertEqual((env_file.status, key.status), (doctor.OK, doctor.OK))
        self.assertIn(f"{profile}/.env", env_file.detail)

    def test_exported_key_does_not_mask_a_placeholder_in_the_profile(self):
        # Hermes loads its .env with override=True, so the placeholder is what would run.
        profile = self._profile()
        (profile / ".env").write_text("OPENROUTER_API_KEY=<your key>\n")
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-0123456789abcdef"}):
            _, key = self._env_checks(profile)
        self.assertEqual(key.status, doctor.FAIL)

    def test_exported_key_counts_when_the_profile_does_not_set_it(self):
        profile = self._profile()
        (profile / ".env").write_text("OTHER=1\n")
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-0123456789abcdef"}):
            _, key = self._env_checks(profile)
        self.assertEqual(key.status, doctor.OK)

    def test_generated_agent_env_still_loses_to_the_environment(self):
        agent = self._adk_agent("OPENROUTER_API_KEY=<your key>\n")
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-0123456789abcdef"}):
            checks = doctor.run_agent_checks(agent, doctor.detect_agent(agent))
        self.assertEqual(self._check(checks, "OPENROUTER_API_KEY").status, doctor.OK)


def _command(fix: str) -> str:
    """The command a next step ends with, as a user would paste it."""
    return fix.rsplit(": ", 1)[1]


class TestHintsActuallyRun(_TmpDirCase):
    """Runs next steps through a shell, since a hint only has to read well to pass a substring check."""

    REAL_KEY = "sk-or-v1-0123456789abcdef"

    def _shell(self, script: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", script], input=stdin, capture_output=True, text=True, timeout=30)

    def _meta_key_fix(self, **env) -> str:
        checks = doctor.run_meta_checks(self.root, None, env)
        return self._check(checks, "OPENROUTER_API_KEY").fix

    def test_key_prompt_sets_a_key_doctor_then_accepts(self):
        command = _command(self._meta_key_fix())
        proc = self._shell(f'{command} && printenv OPENROUTER_API_KEY', f"{self.REAL_KEY}\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), self.REAL_KEY)
        checks = doctor.run_meta_checks(self.root, None, {"OPENROUTER_API_KEY": proc.stdout.strip()})
        self.assertEqual(self._check(checks, "OPENROUTER_API_KEY").status, doctor.OK)

    def _installed_env_file_fix(self, prefix: Path) -> str:
        with (
            mock.patch.object(doctor, "_source_root", return_value=None),
            mock.patch.object(doctor, "__file__", str(prefix / "lib" / "nuvel" / "doctor.py")),
            mock.patch.object(doctor.sys, "prefix", str(prefix)),
        ):
            return self._check(doctor.run_meta_checks(self.root, None, {}), ".env file").fix

    def test_env_file_prompt_writes_a_key_doctor_then_accepts(self):
        prefix = self.root / "my venv"
        prefix.mkdir()
        proc = self._shell(_command(self._installed_env_file_fix(prefix)), f"{self.REAL_KEY}\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        env = doctor.parse_env_file(prefix / ".env")
        self.assertEqual(env, {"OPENROUTER_API_KEY": self.REAL_KEY})

    def test_export_fallback_runs_when_there_is_no_venv_to_write_to(self):
        with (
            mock.patch.object(doctor, "_source_root", return_value=None),
            mock.patch.object(doctor, "__file__", str(self.root / "elsewhere" / "doctor.py")),
            mock.patch.object(doctor.sys, "prefix", str(self.root / "venv")),
        ):
            fix = self._check(doctor.run_meta_checks(self.root, None, {}), ".env file").fix
        proc = self._shell(f"{_command(fix)} && printenv OPENROUTER_API_KEY", f"{self.REAL_KEY}\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), self.REAL_KEY)

    def test_missing_directory_hint_runs(self):
        check = doctor.check_target(self.root / "gone")
        nuvel = shlex.join([sys.executable, "-m", "nuvel.cli"])
        proc = subprocess.run(
            ["bash", "-c", f"{_command(check.fix).replace('nuvel', nuvel, 1)} --json"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "HOME": str(self.root), "PYTHONPATH": str(Path(doctor.__file__).parent.parent)},
        )
        report = json.loads(proc.stdout)
        self.assertEqual(report["cwd"], str(self.root))
        self.assertNotIn("directory", [c["name"] for c in report["checks"]])

    def test_no_command_in_a_next_step_is_left_with_a_blank_to_fill(self):
        agent = self.root / "agent"
        agent.mkdir()
        (agent / "requirements.txt").write_text("google-adk\n")
        (agent / "agent.py").write_text("")
        (agent / ".env").write_text("FAST_MODEL=acme/m\nREASONING_MODEL=acme/m\n")
        checks = [
            *doctor.run_meta_checks(self.root, None, {}),
            *doctor.run_agent_checks(agent, doctor.detect_agent(agent)),
            *doctor.run_network_checks(["acme"]),
            *doctor.run_network_checks([]),
            doctor.check_target(self.root / "gone"),
            _REAL_REACHABLE("ollama", doctor._PROVIDERS["ollama"], 0.1, env={"OLLAMA_API_BASE": "ftp://x"}),
        ]
        for check in checks:
            with self.subTest(check=check.name):
                self.assertNotRegex(check.fix, r"(export \w+=|curl -I |--path |echo |http://)\S*<")
                self.assertNotRegex(check.fix, r"<(its|\w+ base|dir|host)")

    def _pricing_fix(self, model: str, catalog: dict | None) -> tuple[str, Path]:
        plugins = self.root / "my pkg" / "plugins"
        plugins.mkdir(parents=True)
        pricing = plugins / "pricing.json"
        pricing.write_text("{}\n")
        side_effect = RuntimeError("offline") if catalog is None else None
        with mock.patch("nuvel.pricing.fetch_openrouter", return_value=catalog, side_effect=side_effect):
            checks = doctor.check_pricing_coverage(self.root, {"FAST_MODEL": model, "REASONING_MODEL": model})
        self.assertEqual(checks[0].status, doctor.WARN)
        return checks[0].fix, pricing

    def test_pricing_hint_for_a_listed_model_succeeds_from_any_cwd(self):
        from nuvel.cli import main

        price = {"input": 1e-06, "output": 2e-06}
        fix, pricing = self._pricing_fix("openrouter/acme/m", {"acme/m": price})
        argv = shlex.split(fix.split(": ", 1)[1])
        self.assertEqual(argv[0], "nuvel")
        with mock.patch("nuvel.pricing.fetch_openrouter", return_value={"acme/m": price}):
            code = main(argv[1:])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(pricing.read_text())["acme/m"]["input"], 1e-06)
        self.assertTrue(doctor._find_pricing_entry("openrouter/acme/m", json.loads(pricing.read_text())))

    def test_pricing_hint_for_an_unlisted_model_does_not_offer_the_command(self):
        fix, pricing = self._pricing_fix("ollama/llama3", {"acme/m": {"input": 0.0, "output": 0.0}})
        self.assertNotIn("nuvel pricing add", fix)
        self.assertIn(str(pricing), fix)
        self.assertIn('"ollama/llama3"', fix)

    def test_pricing_hint_says_so_when_the_catalog_is_out_of_reach(self):
        fix, pricing = self._pricing_fix("acme/m", None)
        self.assertIn("Once OpenRouter is reachable", fix)
        self.assertIn(f"nuvel pricing add --target {shlex.quote(str(pricing))} acme/m", fix)
        self.assertIn("by hand", fix)

    def test_pricing_catalog_is_fetched_once_and_only_when_needed(self):
        plugins = self.root / "pkg" / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "pricing.json").write_text('{"acme/m": {"input": 0, "output": 0}}')
        with mock.patch("nuvel.pricing.fetch_openrouter", return_value={}) as fetch:
            doctor.check_pricing_coverage(self.root, {"FAST_MODEL": "acme/m", "REASONING_MODEL": "acme/m"})
            fetch.assert_not_called()
            doctor.check_pricing_coverage(self.root, {"FAST_MODEL": "a/b", "REASONING_MODEL": "c/d"})
            fetch.assert_called_once()


@unittest.skipUnless(
    os.environ.get("NUVEL_TEST_INSTALL_HINT"),
    "builds a venv and downloads every dependency; set NUVEL_TEST_INSTALL_HINT=1",
)
class TestSourceReinstallHintInFreshVenv(_TmpDirCase):
    def test_hint_succeeds_where_a_plain_editable_install_would_not(self):
        venv = self.root / "fresh venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        python = str(venv / "bin" / "python")
        with mock.patch.object(doctor.sys, "executable", python):
            fix = doctor._reinstall_fix()
        proc = subprocess.run(["bash", "-c", _command(fix)], capture_output=True, text=True, timeout=1800)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        report = subprocess.run(
            [python, "-m", "nuvel.cli", "doctor", "--json"], capture_output=True, text=True, timeout=300,
        )
        checks = {c["name"]: c["status"] for c in json.loads(report.stdout)["checks"]}
        self.assertEqual(checks["nuvel-cli package"], doctor.OK)
        self.assertEqual(checks["declared dependencies"], doctor.OK)


@unittest.skipUnless(
    os.environ.get("NUVEL_TEST_INSTALL_HINT"),
    "builds a venv and downloads every dependency; set NUVEL_TEST_INSTALL_HINT=1",
)
class TestPythonUpgradeHintInCopiedCheckout(_TmpDirCase):
    def test_hint_builds_a_working_venv(self):
        repo = Path(doctor.__file__).resolve().parent.parent
        copy = self.root / "check out"
        shutil.copytree(repo, copy, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "generated-agents"))
        with mock.patch.object(doctor, "_source_root", return_value=copy):
            fix = doctor._python_upgrade_fix()
        proc = subprocess.run(["bash", "-c", _command(fix)], capture_output=True, text=True, timeout=1800)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        report = subprocess.run(
            [str(copy / ".venv/bin/python"), "-m", "nuvel.cli", "doctor", "--json"],
            capture_output=True, text=True, timeout=300, cwd=self.root,
        )
        checks = {c["name"]: c["status"] for c in json.loads(report.stdout)["checks"]}
        self.assertEqual(checks["Python version"], doctor.OK)
        self.assertEqual(checks["nuvel-cli package"], doctor.OK)


class _HeadHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class TestOllamaBase(_TmpDirCase):
    def setUp(self):
        super().setUp()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HeadHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.live = f"http://127.0.0.1:{server.server_address[1]}"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.dead = f"http://127.0.0.1:{sock.getsockname()[1]}"

    def _probe(self, base: str, name: str = "ollama") -> doctor.Check:
        return _REAL_REACHABLE(name, doctor._PROVIDERS[name], 2.0, env={"OLLAMA_API_BASE": base})

    def test_configured_base_is_probed(self):
        for name in ("ollama", "ollama_chat"):
            with self.subTest(provider=name):
                check = self._probe(self.live, name)
                self.assertEqual(check.status, doctor.OK, check.detail)
                self.assertIn(self.live, check.detail)

    def test_dead_configured_base_is_not_reported_as_a_local_problem(self):
        check = self._probe(self.dead)
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn(f"not reachable at {self.dead}/", check.detail)
        self.assertNotIn("locally", check.detail)
        self.assertNotIn("ollama serve", check.fix)
        self.assertIn(f"curl -I {self.dead}/", check.fix)

    def test_unset_base_is_reported_as_not_running_locally(self):
        provider = doctor.Provider(None, f"{self.dead}/", base_env="OLLAMA_API_BASE")
        for env in ({}, {"OLLAMA_API_BASE": "  "}, None):
            with self.subTest(env=env), mock.patch.object(doctor.shutil, "which", return_value="/bin/ollama"):
                check = _REAL_REACHABLE("ollama", provider, 2.0, env=env)
                self.assertEqual(check.status, doctor.FAIL)
                self.assertIn("not running locally", check.detail)
                self.assertTrue(check.fix.startswith("Start it: ollama serve"), check.fix)

    def test_start_hint_installs_ollama_first_when_it_is_missing(self):
        with mock.patch.object(doctor.shutil, "which", return_value=None):
            self.assertIn("Install Ollama", doctor._local_server_fix("OLLAMA_API_BASE"))

    def test_base_that_is_not_a_url_says_so(self):
        for base in ("your_url_here", "ftp://host", "http://[bad", "http://"):
            with self.subTest(base=base):
                check = self._probe(base)
                self.assertEqual(check.status, doctor.FAIL)
                self.assertIn("OLLAMA_API_BASE is not an http(s) URL", check.detail)

    def test_credentials_in_the_base_are_dropped(self):
        host = self.live.removeprefix("http://")
        check = self._probe(f"http://user:hunter2@{host}/?token=hunter2")
        self.assertEqual(check.status, doctor.OK, check.detail)
        self.assertNotIn("hunter2", check.detail)

    def test_base_from_the_env_file_reaches_the_probe(self):
        (self.root / "requirements.txt").write_text("google-adk\n")
        (self.root / "agent.py").write_text("")
        (self.root / ".env").write_text(
            f"FAST_MODEL=ollama/m\nREASONING_MODEL=ollama/m\nOLLAMA_API_BASE={self.live}\n"
        )
        with mock.patch.object(doctor, "check_provider_reachable", _REAL_REACHABLE):
            code, out = self._run(cwd=self.root, as_json=True)
        check = next(c for c in json.loads(out)["checks"] if c["name"] == "ollama reachable")
        self.assertEqual((code, check["status"]), (0, doctor.OK), out)
        self.assertIn(self.live, check["detail"])

    def test_cloud_providers_ignore_it(self):
        provider = doctor.Provider("K", f"{self.dead}/")
        check = _REAL_REACHABLE("cloud", provider, 2.0, env={"OLLAMA_API_BASE": self.live})
        self.assertEqual(check.status, doctor.FAIL)
        self.assertNotIn(self.live, check.detail)


_REAL_REACHABLE = doctor.check_provider_reachable


if __name__ == "__main__":
    unittest.main()
