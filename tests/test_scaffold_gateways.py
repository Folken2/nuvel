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
