"""Tests for the messaging-gateway flags on `nuvel new`."""

import shutil
import tempfile
import unittest
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent as adk_scaffold
from nuvel.backends.claude_agent_sdk.scaffold import scaffold_agent as cas_scaffold
from nuvel.backends.anthropic_managed_agents.scaffold import scaffold_agent as ama_scaffold


class TestADKAcceptsChannelFlags(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_flags_returns_ok_and_no_channels(self):
        result = adk_scaffold("agent-a", output_dir=self.tmpdir)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result.get("with_slack"))
        self.assertFalse(result.get("with_telegram"))
        self.assertFalse(result.get("with_teams"))

    def test_with_telegram_flag_accepted_and_echoed(self):
        result = adk_scaffold("agent-b", output_dir=self.tmpdir, with_telegram=True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["with_telegram"])

    def test_with_teams_flag_accepted_and_echoed(self):
        result = adk_scaffold("agent-c", output_dir=self.tmpdir, with_teams=True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["with_teams"])

    def test_with_slack_auto_enables_composio(self):
        result = adk_scaffold("agent-d", output_dir=self.tmpdir, with_slack=True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["with_slack"])
        self.assertTrue(result["with_composio"], "with_slack must auto-enable with_composio")


class TestNonAdkBackendsRejectChannelFlags(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_claude_agent_sdk_rejects_with_slack(self):
        result = cas_scaffold("agent-e", output_dir=self.tmpdir, with_slack=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("with-slack", result["message"].lower())

    def test_claude_agent_sdk_rejects_with_telegram(self):
        result = cas_scaffold("agent-f", output_dir=self.tmpdir, with_telegram=True)
        self.assertEqual(result["status"], "error")

    def test_claude_agent_sdk_rejects_with_teams(self):
        result = cas_scaffold("agent-g", output_dir=self.tmpdir, with_teams=True)
        self.assertEqual(result["status"], "error")

    def test_anthropic_managed_rejects_all_channel_flags(self):
        for kw in ("with_slack", "with_telegram", "with_teams"):
            result = ama_scaffold("agent-x", output_dir=self.tmpdir, **{kw: True})
            self.assertEqual(result["status"], "error", f"{kw} should be rejected")


class TestCLIParsing(unittest.TestCase):
    def test_parser_accepts_channel_flags(self):
        from nuvel.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(
            ["new", "agent-y", "--with-slack", "--with-telegram", "--with-teams"]
        )
        self.assertTrue(args.with_slack)
        self.assertTrue(args.with_telegram)
        self.assertTrue(args.with_teams)


class TestNoFlagsByteIdentical(unittest.TestCase):
    """Scaffolding with no channel flags must produce the same files as today."""

    def setUp(self):
        self.tmp_a = tempfile.mkdtemp()
        self.tmp_b = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_a, ignore_errors=True)
        shutil.rmtree(self.tmp_b, ignore_errors=True)

    def test_no_flags_run_adk_has_no_gateway_imports(self):
        adk_scaffold("agent-base", output_dir=self.tmp_a)
        run_adk = (Path(self.tmp_a) / "agent-base" / "run_adk.py").read_text()
        self.assertNotIn("{{gateway", run_adk,
                         "no gateway placeholder substrings should remain")
        self.assertNotIn("from agent_base.gateways", run_adk,
                         "run_adk.py must not import gateways when no channel flags are set")
        # Cron routes are always mounted (cron is opt-in via env, not flags),
        # so the only `include_router` line in a no-flags scaffold is the
        # cron one. Confirm no *gateway* routers are mounted.
        gateway_router_lines = [
            l for l in run_adk.splitlines()
            if "include_router" in l and "cron" not in l.lower()
        ]
        self.assertEqual(
            gateway_router_lines, [],
            "run_adk.py must not mount gateway routers when no channel flags are set",
        )

    def test_no_flags_env_example_has_no_gateway_block(self):
        adk_scaffold("agent-base2", output_dir=self.tmp_b)
        env = (Path(self.tmp_b) / "agent-base2" / ".env.example").read_text()
        self.assertNotIn("{{gateway", env)


class TestTelegramOverlay(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold("agent-tg", output_dir=self.tmpdir, with_telegram=True)
        self.assertEqual(result["status"], "ok")
        self.agent_dir = Path(result["path"])

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_telegram_module_exists(self):
        self.assertTrue((self.agent_dir / "agent_tg" / "gateways" / "telegram.py").is_file())
        self.assertTrue((self.agent_dir / "agent_tg" / "gateways" / "_common.py").is_file())

    def test_run_adk_imports_and_mounts_telegram(self):
        run_adk = (self.agent_dir / "run_adk.py").read_text()
        self.assertIn("from agent_tg.gateways import telegram as gw_telegram", run_adk)
        self.assertIn("app.include_router(gw_telegram.router)", run_adk)

    def test_env_example_has_telegram_block(self):
        env = (self.agent_dir / ".env.example").read_text()
        self.assertIn("TELEGRAM_BOT_TOKEN", env)
        self.assertIn("TELEGRAM_WEBHOOK_SECRET", env)

    def test_readme_has_telegram_section(self):
        readme = (self.agent_dir / "README.md").read_text()
        self.assertIn("Telegram", readme)
        self.assertIn("setWebhook", readme)


class TestSlackOverlay(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold("agent-sl", output_dir=self.tmpdir, with_slack=True)
        self.assertEqual(result["status"], "ok")
        # Slack auto-enables composio.
        self.assertTrue(result["with_composio"])
        self.agent_dir = Path(result["path"])

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_slack_module_exists(self):
        self.assertTrue((self.agent_dir / "agent_sl" / "gateways" / "slack.py").is_file())

    def test_run_adk_imports_and_mounts_slack(self):
        run_adk = (self.agent_dir / "run_adk.py").read_text()
        self.assertIn("from agent_sl.gateways import slack as gw_slack", run_adk)
        self.assertIn("app.include_router(gw_slack.router)", run_adk)

    def test_env_example_has_slack_block(self):
        env = (self.agent_dir / ".env.example").read_text()
        self.assertIn("COMPOSIO_WEBHOOK_SECRET", env)

    def test_readme_has_slack_section(self):
        readme = (self.agent_dir / "README.md").read_text()
        self.assertIn("Slack", readme)
        self.assertIn("composio trigger create", readme)

    def test_run_adk_wires_composio_client(self):
        run_adk = (self.agent_dir / "run_adk.py").read_text()
        self.assertIn("app.state.composio_client = get_composio_client()", run_adk)
        self.assertIn("from agent_sl.gateways._common import get_composio_client", run_adk)


class TestTeamsOverlay(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold("agent-tm", output_dir=self.tmpdir, with_teams=True)
        self.assertEqual(result["status"], "ok")
        self.agent_dir = Path(result["path"])

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_teams_bridge_module_exists(self):
        self.assertTrue((self.agent_dir / "agent_tm" / "gateways" / "teams_bridge.py").is_file())

    def test_requirements_includes_microsoft_agents(self):
        reqs = (self.agent_dir / "requirements.txt").read_text()
        self.assertIn("microsoft-agents-hosting-aiohttp", reqs)
        self.assertIn("microsoft-agents-authentication-msal", reqs)
        self.assertIn("aiohttp", reqs)
        self.assertIn("pypdf", reqs)

    def test_env_example_has_teams_block(self):
        env = (self.agent_dir / ".env.example").read_text()
        self.assertIn("TEAMS_BRIDGE_PORT", env)

    def test_readme_has_teams_section(self):
        readme = (self.agent_dir / "README.md").read_text()
        self.assertIn("Teams", readme)
        self.assertIn("teams_bridge", readme)

    def test_teams_bridge_uses_renamed_envvars(self):
        bridge = (self.agent_dir / "agent_tm" / "gateways" / "teams_bridge.py").read_text()
        # Old names that MUST be gone:
        self.assertNotIn("DATA_AGENT_BASE_URL", bridge)
        self.assertNotIn("DATA_AGENT_APP_NAME", bridge)
        self.assertNotIn("DATA_AGENT_API_KEY", bridge)
        self.assertNotIn("DATA_AGENT_TIMEOUT_SECONDS", bridge)
        # M365_* renamed to TEAMS_*:
        self.assertNotIn("M365_BRIDGE_PORT", bridge)
        self.assertNotIn("M365_PROGRESS_TEXTS", bridge)
        self.assertNotIn("M365_ENABLE_INTERMEDIATE_MESSAGES", bridge)
        # New names:
        self.assertIn("AGENT_BASE_URL", bridge)
        self.assertIn("AGENT_APP_NAME", bridge)
        self.assertIn("API_KEY", bridge)
        self.assertIn("AGENT_TIMEOUT_SECONDS", bridge)
        self.assertIn("TEAMS_BRIDGE_PORT", bridge)
        self.assertIn("TEAMS_PROGRESS_TEXTS", bridge)

    def test_teams_bridge_default_app_name_is_scaffolded(self):
        bridge = (self.agent_dir / "agent_tm" / "gateways" / "teams_bridge.py").read_text()
        # Default for AGENT_APP_NAME should be the scaffolded agent name.
        self.assertIn('os.getenv("AGENT_APP_NAME", "agent-tm")', bridge)


class TestAllChannelsTogether(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold(
            "agent-all", output_dir=self.tmpdir,
            with_slack=True, with_telegram=True, with_teams=True,
        )
        self.assertEqual(result["status"], "ok")
        self.agent_dir = Path(result["path"])

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_all_three_modules_present(self):
        gw = self.agent_dir / "agent_all" / "gateways"
        for fname in ("__init__.py", "_common.py", "slack.py", "telegram.py", "teams_bridge.py"):
            self.assertTrue((gw / fname).is_file(), f"Missing: {fname}")

    def test_run_adk_mounts_slack_and_telegram_only(self):
        run_adk = (self.agent_dir / "run_adk.py").read_text()
        self.assertIn("gw_slack", run_adk)
        self.assertIn("gw_telegram", run_adk)
        self.assertNotIn("teams_bridge", run_adk)  # Teams is a sidecar, not mounted

    def test_env_example_contains_all_three_blocks(self):
        env = (self.agent_dir / ".env.example").read_text()
        self.assertIn("TELEGRAM_BOT_TOKEN", env)
        self.assertIn("COMPOSIO_WEBHOOK_SECRET", env)
        self.assertIn("TEAMS_BRIDGE_PORT", env)

    def test_readme_contains_all_three_sections(self):
        readme = (self.agent_dir / "README.md").read_text()
        self.assertIn("Channel: Slack", readme)
        self.assertIn("Channel: Telegram", readme)
        self.assertIn("Channel: Microsoft Teams", readme)

    def test_no_unrendered_placeholders_anywhere(self):
        # Walk the agent directory; no file should contain `{{` template syntax.
        for path in self.agent_dir.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".md", ".txt", ".example"):
                content = path.read_text(errors="ignore")
                self.assertNotIn("{{", content, f"Unrendered placeholder in {path}")
