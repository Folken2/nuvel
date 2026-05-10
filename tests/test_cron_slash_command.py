"""Tests for the ``/cron`` gateway slash command."""

from __future__ import annotations

import asyncio
import unittest

from tests._cron_helpers import CronAgent


class TestCronSlashCommand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = CronAgent("cron-slash")
        # The slash command lives in the gateway-base overlay, which the
        # helper enables via with_telegram=True.
        import importlib
        cls.commands = importlib.import_module(f"{cls.agent.package}.gateways.commands")
        importlib.reload(cls.commands)
        # The cron command also registers on import; ensure it's there.
        cls.svc = cls.agent.service_mod.CronService()

    @classmethod
    def tearDownClass(cls):
        cls.agent.cleanup()

    def _dispatch(self, text: str, **extra) -> "tuple[bool, list[str]]":
        ctx = self.commands.CommandContext(
            user_id="u", channel="c", session_id="s", text=text, extra=extra,
        )
        result = asyncio.run(self.commands.try_dispatch(text, ctx))
        return result.handled, result.replies

    def test_help_lists_cron(self):
        handled, replies = self._dispatch("/help")
        self.assertTrue(handled)
        self.assertIn("/cron", replies[0])

    def test_cron_no_args_shows_help(self):
        handled, replies = self._dispatch("/cron")
        self.assertTrue(handled)
        self.assertIn("/cron list", replies[0])
        self.assertIn("/cron add", replies[0])

    def test_cron_list_when_empty(self):
        handled, replies = self._dispatch("/cron list")
        self.assertTrue(handled)
        self.assertIn("No cron jobs", replies[0])

    def test_cron_add_then_list_then_remove(self):
        handled, replies = self._dispatch(
            '/cron add "every 1h" "summarize my email" --name brief --deliver local'
        )
        self.assertTrue(handled)
        self.assertTrue(replies and "Scheduled" in replies[0])

        # list now non-empty
        _, listing = self._dispatch("/cron list")
        self.assertIn("brief", listing[0])

        # parse the id from list output
        # `  <id>  active    every 1h            next=...  name='brief'`
        line = next(l for l in listing[0].splitlines() if "brief" in l)
        jid = line.strip().split()[0]

        _, p = self._dispatch(f"/cron pause {jid}")
        self.assertIn("Paused", p[0])
        _, r = self._dispatch(f"/cron resume {jid}")
        self.assertIn("Resumed", r[0])
        _, run = self._dispatch(f"/cron run {jid}")
        self.assertIn("queued", run[0])
        _, rm = self._dispatch(f"/cron remove {jid}")
        self.assertIn("Removed", rm[0])

    def test_cron_add_records_origin_when_platform_present(self):
        handled, replies = self._dispatch(
            '/cron add "every 1h" "ping" --name p --deliver origin',
            platform="slack", channel="C123",
        )
        self.assertTrue(handled)
        # Find the created job and confirm origin was captured.
        jobs = self.svc.list_jobs()
        match = [j for j in jobs if j.get("name") == "p"]
        self.assertTrue(match)
        self.assertEqual(match[-1]["origin"]["platform"], "slack")
        self.assertEqual(match[-1]["origin"]["channel"], "C123")
        self.svc.delete_job(match[-1]["id"])

    def test_cron_unknown_subcommand(self):
        handled, replies = self._dispatch("/cron danceparty")
        self.assertTrue(handled)
        self.assertIn("Unknown subcommand", replies[0])


if __name__ == "__main__":
    unittest.main()
