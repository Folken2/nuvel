"""Tests for cron delivery wrapping and silent suppression."""

from __future__ import annotations

import asyncio
import unittest

from tests._cron_helpers import CronAgent


class TestDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = CronAgent("cron-deliv")
        cls.delivery = cls.agent.delivery

    @classmethod
    def tearDownClass(cls):
        cls.agent.cleanup()

    def test_wrap_response_format(self):
        out = self.delivery.wrap_response("daily-brief", "Stocks are up.")
        self.assertIn("Cronjob Response: daily-brief", out)
        self.assertIn("-------------", out)
        self.assertIn("Stocks are up.", out)
        self.assertIn("agent cannot see this message", out)

    def test_local_delivery_succeeds(self):
        result = asyncio.run(self.delivery.deliver(
            name="x", response="hello", delivery="local",
        ))
        self.assertTrue(result.delivered)
        self.assertEqual(result.target, "local")

    def test_silent_prefix_suppresses_delivery(self):
        result = asyncio.run(self.delivery.deliver(
            name="x", response="[SILENT] nothing to say", delivery="local",
        ))
        self.assertFalse(result.delivered)
        self.assertTrue(result.silent)

    def test_silent_is_case_insensitive(self):
        self.assertTrue(self.delivery.is_silent("[silent] foo"))
        self.assertTrue(self.delivery.is_silent("  [SILENT]  foo"))
        self.assertFalse(self.delivery.is_silent("not silent"))

    def test_origin_without_metadata_errors(self):
        result = asyncio.run(self.delivery.deliver(
            name="x", response="hi", delivery="origin", origin=None,
        ))
        self.assertFalse(result.delivered)
        self.assertIsNotNone(result.error)

    def test_unknown_target_errors(self):
        result = asyncio.run(self.delivery.deliver(
            name="x", response="hi", delivery="discord:foo",
        ))
        self.assertFalse(result.delivered)
        self.assertIn("unknown delivery", result.error or "")


if __name__ == "__main__":
    unittest.main()
