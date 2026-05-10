"""Tests for the in-process CRUD facade."""

from __future__ import annotations

import unittest

from tests._cron_helpers import CronAgent


class TestService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = CronAgent("cron-svc")
        cls.svc = cls.agent.service_mod.CronService()

    @classmethod
    def tearDownClass(cls):
        cls.agent.cleanup()

    def test_create_validates(self):
        with self.assertRaises(ValueError):
            self.svc.create_job(name="", prompt="x", schedule="30m")
        with self.assertRaises(ValueError):
            self.svc.create_job(name="n", prompt="", schedule="30m")
        with self.assertRaises(ValueError):
            self.svc.create_job(name="n", prompt="p", schedule="garbage")
        with self.assertRaises(ValueError):
            self.svc.create_job(name="n", prompt="p", schedule="30m", delivery="mars")

    def test_crud_roundtrip(self):
        job = self.svc.create_job(
            name="t", prompt="hello", schedule="every 1h", delivery="local",
        )
        self.assertEqual(job["status"], "active")
        self.assertIsNotNone(job["next_run_at"])

        self.assertIsNotNone(self.svc.get_job(job["id"]))
        listed = [j["id"] for j in self.svc.list_jobs()]
        self.assertIn(job["id"], listed)

        updated = self.svc.update_job(job["id"], name="renamed")
        self.assertEqual(updated["name"], "renamed")

        paused = self.svc.pause(job["id"])
        self.assertEqual(paused["status"], "paused")
        resumed = self.svc.resume(job["id"])
        self.assertEqual(resumed["status"], "active")

        triggered = self.svc.trigger_now(job["id"])
        self.assertIn("next_run_at", triggered)

        self.assertTrue(self.svc.delete_job(job["id"]))
        self.assertFalse(self.svc.delete_job(job["id"]))

    def test_update_unknown_fields_rejected(self):
        job = self.svc.create_job(
            name="t2", prompt="hello", schedule="30m", delivery="local",
        )
        with self.assertRaises(ValueError):
            self.svc.update_job(job["id"], created_at="now")

    def test_update_schedule_recomputes_next_run(self):
        job = self.svc.create_job(
            name="t3", prompt="hello", schedule="30m", delivery="local",
        )
        before = job["next_run_at"]
        updated = self.svc.update_job(job["id"], schedule="every 5h")
        self.assertEqual(updated["schedule"], "every 5h")
        self.assertNotEqual(updated["next_run_at"], before)


if __name__ == "__main__":
    unittest.main()
