"""Tests for atomic JSON storage and the tick lock."""

from __future__ import annotations

import json
import os
import threading
import unittest

from tests._cron_helpers import CronAgent


class TestStorage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = CronAgent("cron-stor")

    @classmethod
    def tearDownClass(cls):
        cls.agent.cleanup()

    def test_load_returns_empty_when_missing(self):
        self.assertEqual(self.agent.storage.load_jobs(), [])

    def test_save_then_load_roundtrip(self):
        jobs = [{"id": "a", "name": "n", "schedule": "30m", "status": "active"}]
        self.agent.storage.save_jobs(jobs)
        self.assertEqual(self.agent.storage.load_jobs(), jobs)

    def test_atomic_write_no_partial(self):
        # Write a big list, then write a smaller one and confirm the file
        # is exactly the new payload (no leftover bytes — proves replace).
        self.agent.storage.save_jobs([{"id": "x" * 1000}])
        self.agent.storage.save_jobs([{"id": "small"}])
        raw = self.agent.storage.jobs_file().read_text()
        self.assertEqual(json.loads(raw), [{"id": "small"}])

    def test_corrupt_file_recovers(self):
        # Simulate corruption and confirm load_jobs returns [] gracefully.
        path = self.agent.storage.jobs_file()
        path.write_text("{not json")
        self.assertEqual(self.agent.storage.load_jobs(), [])

    def test_write_output_creates_per_job_file(self):
        path = self.agent.storage.write_output("job-1", "hello")
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(), "hello")
        self.assertEqual(path.parent.name, "job-1")


class TestTickLock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = CronAgent("cron-lock")

    @classmethod
    def tearDownClass(cls):
        cls.agent.cleanup()

    def test_lock_held_blocks_second_acquire(self):
        s = self.agent.storage
        held = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []

        def _holder():
            try:
                with s.acquire_tick_lock():
                    held.set()
                    release.wait(timeout=2)
            except BaseException as exc:
                errors.append(exc)

        t = threading.Thread(target=_holder)
        t.start()
        try:
            self.assertTrue(held.wait(timeout=2))
            with self.assertRaises(s.TickLockBusy):
                with s.acquire_tick_lock():
                    pass
        finally:
            release.set()
            t.join(timeout=2)
        self.assertEqual(errors, [])

    def test_lock_released_allows_reacquire(self):
        s = self.agent.storage
        with s.acquire_tick_lock():
            pass
        with s.acquire_tick_lock():
            pass  # no exception means reacquire works


if __name__ == "__main__":
    unittest.main()
