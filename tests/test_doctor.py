"""Tests for nuvel.doctor — the diagnostic subcommand."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
]


def setUpModule():
    # Hermetic: no real probes, and the developer's own keys and home stay out.
    for patcher in _patchers:
        patcher.start()
    for key in list(os.environ):
        if key.endswith("_API_KEY") or key in ("FAST_MODEL", "REASONING_MODEL", "TRACE_DIR", "XDG_DATA_HOME"):
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


class _TmpDirCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def _adk_agent(self, env: str | None = "OPENROUTER_API_KEY=sk-test\n") -> Path:
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
        self.assertIn("export OPENROUTER_API_KEY=", out)

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
        with self._isolated_meta(), mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-1"}):
            code, _ = self._run(cwd=self.root)
        self.assertEqual(code, 0)

    def test_cwd_env_is_not_read_by_nuvel_run(self):
        # load_dotenv() searches from the installed package, not the cwd.
        (self.root / ".env").write_text("OPENROUTER_API_KEY=sk-or-1\n")
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
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-1"}):
            checks = doctor.run_agent_checks(agent, info)
        self.assertEqual(self._check(checks, "OPENROUTER_API_KEY").status, doctor.OK)

    def test_missing_env_file_is_fine_when_environment_has_the_key(self):
        agent = self._adk_agent(env=None)
        info = doctor.detect_agent(agent)
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-1"}):
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

    def test_unknown_provider_warns_instead_of_passing_silently(self):
        agent = self._adk_agent("FAST_MODEL=acme/m\nOPENROUTER_API_KEY=sk\n")
        checks = doctor.run_agent_checks(agent, doctor.detect_agent(agent))
        self.assertEqual(self._check(checks, "provider acme").status, doctor.WARN)

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
        check, provider = doctor.check_hermes_config(profile)
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIsNone(provider)
        self.assertTrue(check.fix)

    def test_config_without_provider_fails(self):
        check, _ = doctor.check_hermes_config(self._profile("agent: {}\n"))
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
                "doctor.check_provider_reachable = lambda name, provider: "
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


_REAL_REACHABLE = doctor.check_provider_reachable


if __name__ == "__main__":
    unittest.main()
