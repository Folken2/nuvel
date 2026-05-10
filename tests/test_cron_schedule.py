"""Schedule parser tests.

The parser lives in the generated agent template; we scaffold a tiny agent
once per class, then import the module fresh from the stamped tree.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent


def _scaffold(tmpdir):
    result = scaffold_agent("agent-test", output_dir=tmpdir)
    if result["status"] != "ok":
        raise AssertionError(result.get("message"))
    return Path(result["path"]) / "agent_test"


def _import(pkg_dir: Path, dotted: str, fresh_name: str):
    file_path = pkg_dir / Path(*dotted.split(".")).with_suffix(".py")
    spec = importlib.util.spec_from_file_location(fresh_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fresh_name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.pkg = _scaffold(cls.tmpdir)
        cls.schedule = _import(cls.pkg, "cron.schedule", f"_csched_{cls.__name__}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)


class TestParseSchedule(_Base):
    def test_relative_durations(self):
        for inp, secs in [("30s", 30), ("30m", 1800), ("2h", 7200), ("1d", 86400)]:
            p = self.schedule.parse_schedule(inp)
            self.assertEqual(p.kind, "one_shot_offset", inp)
            self.assertEqual(p.seconds, secs)

    def test_interval(self):
        p = self.schedule.parse_schedule("every 1h")
        self.assertEqual(p.kind, "interval")
        self.assertEqual(p.seconds, 3600)
        self.assertTrue(p.is_recurring)

    def test_cron(self):
        p = self.schedule.parse_schedule("0 9 * * *")
        self.assertEqual(p.kind, "cron")
        self.assertEqual(p.cron_expr, "0 9 * * *")
        self.assertTrue(p.is_recurring)

    def test_iso(self):
        p = self.schedule.parse_schedule("2026-12-15T09:00:00")
        self.assertEqual(p.kind, "one_shot_at")
        self.assertIsNotNone(p.at)
        self.assertEqual(p.at.tzinfo, timezone.utc)

    def test_rejects_garbage(self):
        for bad in ["", "  ", "tomorrow", "0 * *", "every"]:
            with self.assertRaises(ValueError):
                self.schedule.parse_schedule(bad)


class TestComputeNextRun(_Base):
    def test_one_shot_offset_first_then_none(self):
        p = self.schedule.parse_schedule("30m")
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        first = self.schedule.compute_next_run(p, now=now)
        self.assertEqual(first, now + timedelta(minutes=30))
        self.assertIsNone(self.schedule.compute_next_run(p, now=now, last_run_at=first))

    def test_interval_uses_last_anchor(self):
        p = self.schedule.parse_schedule("every 30m")
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        last = datetime(2026, 1, 1, 11, 30, tzinfo=timezone.utc)
        self.assertEqual(
            self.schedule.compute_next_run(p, now=now, last_run_at=last),
            last + timedelta(minutes=30),
        )

    def test_cron_returns_aware(self):
        p = self.schedule.parse_schedule("0 9 * * *")
        now = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
        nxt = self.schedule.compute_next_run(p, now=now)
        self.assertEqual(nxt.tzinfo, timezone.utc)
        self.assertEqual(nxt.hour, 9)


if __name__ == "__main__":
    unittest.main()
