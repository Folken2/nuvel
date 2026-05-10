"""Tests for the ``cronjob`` ADK tool: every action + recursion guard."""

from __future__ import annotations

import os
import unittest

from tests._cron_helpers import CronAgent


class TestCronjobTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = CronAgent("cron-tool")
        cls.cronjob = staticmethod(cls.agent.tools.cronjob)
        cls.guard_env = cls.agent.service_mod.NUVEL_CRON_RUNNING_ENV

    @classmethod
    def tearDownClass(cls):
        cls.agent.cleanup()

    def tearDown(self):
        os.environ.pop(self.guard_env, None)

    def test_create_then_list_get(self):
        out = self.cronjob(
            action="create", name="t", prompt="p", schedule="every 1h",
            delivery="local",
        )
        self.assertEqual(out["status"], "ok")
        jid = out["job"]["id"]

        listed = self.cronjob(action="list")
        self.assertEqual(listed["status"], "ok")
        self.assertTrue(any(j["id"] == jid for j in listed["jobs"]))

        got = self.cronjob(action="get", job_id=jid)
        self.assertEqual(got["status"], "ok")
        self.assertEqual(got["job"]["id"], jid)

    def test_update_pause_resume_run_remove(self):
        jid = self.cronjob(
            action="create", name="t2", prompt="p", schedule="every 1h",
            delivery="local",
        )["job"]["id"]

        self.assertEqual(
            self.cronjob(action="update", job_id=jid, new_name="renamed")["job"]["name"],
            "renamed",
        )
        self.assertEqual(
            self.cronjob(action="pause", job_id=jid)["job"]["status"], "paused",
        )
        self.assertEqual(
            self.cronjob(action="resume", job_id=jid)["job"]["status"], "active",
        )
        self.assertEqual(self.cronjob(action="run", job_id=jid)["status"], "ok")
        self.assertEqual(
            self.cronjob(action="remove", job_id=jid),
            {"status": "ok", "removed": jid},
        )

    def test_unknown_action(self):
        out = self.cronjob(action="dance")  # type: ignore[arg-type]
        self.assertEqual(out["status"], "error")

    def test_recursion_guard_blocks_mutations(self):
        os.environ[self.guard_env] = "1"
        out = self.cronjob(
            action="create", name="x", prompt="p", schedule="30m",
        )
        self.assertEqual(out["status"], "error")
        self.assertIn("cannot create", out["message"])
        # Read-only actions still work under the guard.
        self.assertEqual(self.cronjob(action="list")["status"], "ok")


if __name__ == "__main__":
    unittest.main()
