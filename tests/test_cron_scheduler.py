"""Scheduler tick semantics: due-job pickup, in-flight de-dup, lock skip."""

from __future__ import annotations

import asyncio
import threading
import unittest
from datetime import datetime, timedelta, timezone

from tests._cron_helpers import CronAgent


def _shift(job_dict, **kw):
    job_dict.update(kw)
    return job_dict


class TestSchedulerTick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = CronAgent("cron-tick")
        cls.svc = cls.agent.service_mod.CronService()
        cls.scheduler = cls.agent.scheduler

    @classmethod
    def tearDownClass(cls):
        cls.agent.cleanup()

    def _force_due(self, job_id: str) -> None:
        with self.agent.storage.transaction():
            jobs = self.agent.storage.load_jobs()
            for j in jobs:
                if j["id"] == job_id:
                    j["next_run_at"] = (
                        datetime.now(timezone.utc) - timedelta(minutes=1)
                    ).isoformat()
            self.agent.storage.save_jobs(jobs)

    def test_only_active_due_jobs_run(self):
        runs: list[str] = []

        async def _invoker(job_id, prompt):
            runs.append(job_id)
            return f"ran {job_id}"

        # One due, one paused, one future.
        due = self.svc.create_job(name="due", prompt="p", schedule="30m", delivery="local")
        paused = self.svc.create_job(name="ps", prompt="p", schedule="30m", delivery="local")
        self.svc.pause(paused["id"])
        future = self.svc.create_job(name="ft", prompt="p", schedule="every 1d", delivery="local")
        self._force_due(due["id"])
        self._force_due(paused["id"])  # still paused — must not run

        in_flight: set[str] = set()
        n = asyncio.run(self.scheduler.tick_once(_invoker, in_flight=in_flight))
        self.assertEqual(n, 1)
        self.assertEqual(runs, [due["id"]])
        self.assertNotIn(future["id"], runs)

    def test_one_shot_completes(self):
        async def _invoker(job_id, prompt):
            return "done"

        job = self.svc.create_job(name="oneshot", prompt="p", schedule="30m", delivery="local")
        self._force_due(job["id"])
        asyncio.run(self.scheduler.tick_once(_invoker, in_flight=set()))
        refreshed = self.svc.get_job(job["id"])
        self.assertEqual(refreshed["status"], "completed")

    def test_in_flight_dedup(self):
        async def _invoker(job_id, prompt):
            return "x"

        job = self.svc.create_job(name="dup", prompt="p", schedule="every 1m", delivery="local")
        self._force_due(job["id"])
        # Pre-populate in_flight to simulate a still-running tick.
        in_flight = {job["id"]}
        n = asyncio.run(self.scheduler.tick_once(_invoker, in_flight=in_flight))
        self.assertEqual(n, 0)

    def test_tick_skipped_when_lock_held(self):
        async def _runner():
            ran: list[str] = []

            async def _invoker(job_id, prompt):
                ran.append(job_id)
                return "x"

            job = self.svc.create_job(name="lk", prompt="p", schedule="30m", delivery="local")
            self._force_due(job["id"])

            # Hold the lock from a worker thread.
            held = threading.Event()
            release = threading.Event()

            def _holder():
                with self.agent.storage.acquire_tick_lock():
                    held.set()
                    release.wait(timeout=2)

            t = threading.Thread(target=_holder)
            t.start()
            try:
                self.assertTrue(held.wait(timeout=2))
                n = await self.scheduler.tick_once(_invoker, in_flight=set())
            finally:
                release.set()
                t.join(timeout=2)
            return n, ran

        n, ran = asyncio.run(_runner())
        self.assertEqual(n, 0)
        self.assertEqual(ran, [])

    def test_recursion_guard_set_during_run(self):
        import os
        observed: list[str | None] = []

        async def _invoker(job_id, prompt):
            observed.append(os.environ.get(self.agent.service_mod.NUVEL_CRON_RUNNING_ENV))
            return "ok"

        job = self.svc.create_job(name="rec", prompt="p", schedule="30m", delivery="local")
        self._force_due(job["id"])
        asyncio.run(self.scheduler.tick_once(_invoker, in_flight=set()))
        self.assertEqual(observed, ["1"])
        # Cleared after run.
        self.assertNotEqual(
            os.environ.get(self.agent.service_mod.NUVEL_CRON_RUNNING_ENV), "1",
        )


if __name__ == "__main__":
    unittest.main()
