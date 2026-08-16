"""Cron isolation hardening: scoped secrets, headless policy, HITL creation.

Covers ``cron/isolation.py`` (secret scoping + headless tool policy + the
run-context markers), the ``CronIsolationPlugin`` before_tool_callback, and the
HITL-gated creation flow in ``CronService`` / the scheduler.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import os
import types
import unittest
from datetime import datetime, timedelta, timezone

from tests._cron_helpers import CronAgent


@contextlib.contextmanager
def _env(**kw):
    """Temporarily set (or unset, with value=None) env vars."""
    prev = {k: os.environ.get(k) for k in kw}
    try:
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _fake_tool(name: str):
    return types.SimpleNamespace(name=name)


class TestScopedSecrets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = CronAgent("cron-iso-secrets")
        cls.iso = importlib.import_module(f"{cls.agent.package}.cron.isolation")

    @classmethod
    def tearDownClass(cls):
        cls.agent.cleanup()

    def test_only_declared_vars_injected(self):
        base = {"SLACK_TOKEN": "s", "GITHUB_TOKEN": "g", "SECRET_DB": "x"}
        with _env(NUVEL_CRON_SCOPE_SECRETS="1"):
            env = self.iso.resolve_cron_env(["SLACK_TOKEN", "GITHUB_TOKEN"], base)
        self.assertEqual(env, {"SLACK_TOKEN": "s", "GITHUB_TOKEN": "g"})
        self.assertNotIn("SECRET_DB", env)

    def test_empty_list_masks_everything(self):
        base = {"SLACK_TOKEN": "s", "OTHER": "y"}
        with _env(NUVEL_CRON_SCOPE_SECRETS="1"):
            env = self.iso.resolve_cron_env([], base)
        self.assertEqual(env, {})

    def test_no_secrets_list_is_full_env_backward_compat(self):
        base = {"SLACK_TOKEN": "s", "OTHER": "y"}
        with _env(NUVEL_CRON_SCOPE_SECRETS="1"):
            env = self.iso.resolve_cron_env(None, base)
        self.assertEqual(env, base)
        self.assertIsNot(env, base)  # copy, not alias

    def test_disabled_flag_is_full_env_even_with_declared(self):
        base = {"SLACK_TOKEN": "s", "OTHER": "y"}
        with _env(NUVEL_CRON_SCOPE_SECRETS=None):  # scoping off (default)
            env = self.iso.resolve_cron_env(["SLACK_TOKEN"], base)
        self.assertEqual(env, base)

    def test_declared_name_absent_from_base_is_skipped(self):
        base = {"SLACK_TOKEN": "s"}
        with _env(NUVEL_CRON_SCOPE_SECRETS="1"):
            env = self.iso.resolve_cron_env(["SLACK_TOKEN", "MISSING"], base)
        self.assertEqual(env, {"SLACK_TOKEN": "s"})

    def test_cron_isolation_sets_scope_contextvar_when_enabled(self):
        base = {"A": "1", "B": "2"}
        with _env(NUVEL_CRON_SCOPE_SECRETS="1"):
            with self.iso.cron_isolation("job1", secrets=["A"]):
                self.assertEqual(self.iso.active_cron_run().job_id, "job1")
                self.assertTrue(self.iso.is_headless())
                self.assertEqual(self.iso.active_secret_scope(), frozenset({"A"}))
                self.assertEqual(self.iso.active_cron_env(base), {"A": "1"})
            # reset on exit
            self.assertIsNone(self.iso.active_cron_run())
            self.assertFalse(self.iso.is_headless())
            self.assertIsNone(self.iso.active_secret_scope())
            self.assertIsNone(self.iso.active_cron_env(base))

    def test_cron_isolation_unscoped_when_flag_off(self):
        with _env(NUVEL_CRON_SCOPE_SECRETS=None):
            with self.iso.cron_isolation("job2", secrets=["A"]):
                # marker + headless still set, but no secret scope masking.
                self.assertEqual(self.iso.active_cron_run().job_id, "job2")
                self.assertTrue(self.iso.is_headless())
                self.assertIsNone(self.iso.active_secret_scope())


class TestHeadlessPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = CronAgent("cron-iso-headless")
        cls.iso = importlib.import_module(f"{cls.agent.package}.cron.isolation")
        cls.plugin_mod = importlib.import_module(
            f"{cls.agent.package}.plugins.cron_isolation_plugin"
        )
        cls.plugin = cls.plugin_mod.CronIsolationPlugin()

    @classmethod
    def tearDownClass(cls):
        cls.agent.cleanup()

    def _call(self, tool_name):
        return asyncio.run(
            self.plugin.before_tool_callback(
                tool=_fake_tool(tool_name), tool_args={}, tool_context=None,
            )
        )

    def test_inert_outside_cron_run(self):
        # No active cron run → never interferes, whatever the policy.
        with _env(NUVEL_CRON_HEADLESS_POLICY="deny-all"):
            self.assertIsNone(self._call("http_get"))

    def test_allow_shell_allows_shell_denies_http(self):
        with _env(NUVEL_CRON_HEADLESS_POLICY="allow-shell"):
            with self.iso.cron_isolation("j"):
                self.assertIsNone(self._call("shell"))
                self.assertIsNone(self._call("bash"))
                denied = self._call("http_get")
        self.assertIsInstance(denied, dict)
        self.assertTrue(denied.get("headless_denied"))
        self.assertEqual(denied.get("status"), "error")

    def test_allow_shell_is_the_default_policy(self):
        with _env(NUVEL_CRON_HEADLESS_POLICY=None):  # unset → default
            with self.iso.cron_isolation("j"):
                self.assertIsNone(self._call("shell"))
                self.assertIsInstance(self._call("db_write"), dict)

    def test_deny_all_denies_everything(self):
        with _env(NUVEL_CRON_HEADLESS_POLICY="deny-all"):
            with self.iso.cron_isolation("j"):
                self.assertIsInstance(self._call("shell"), dict)
                self.assertIsInstance(self._call("http_get"), dict)

    def test_allow_all_allows_everything(self):
        with _env(NUVEL_CRON_HEADLESS_POLICY="allow-all"):
            with self.iso.cron_isolation("j"):
                self.assertIsNone(self._call("shell"))
                self.assertIsNone(self._call("http_get"))

    def test_unknown_policy_falls_back_to_allow_shell(self):
        with _env(NUVEL_CRON_HEADLESS_POLICY="bogus"):
            with self.iso.cron_isolation("j"):
                self.assertIsNone(self._call("shell"))
                self.assertIsInstance(self._call("http_get"), dict)

    def test_custom_shell_tool_names_via_env(self):
        with _env(
            NUVEL_CRON_HEADLESS_POLICY="allow-shell",
            NUVEL_CRON_SHELL_TOOLS="run_box",
        ):
            with self.iso.cron_isolation("j"):
                self.assertIsNone(self._call("run_box"))
                # "shell" is no longer in the (overridden) shell set.
                self.assertIsInstance(self._call("shell"), dict)


class TestHitlCreation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = CronAgent("cron-iso-hitl")
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

    def test_pending_job_does_not_fire_until_confirmed(self):
        runs: list[str] = []

        async def _invoker(job_id, prompt):
            runs.append(job_id)
            return "ran"

        with _env(NUVEL_CRON_HITL_CREATE="1"):
            job = self.svc.create_job(
                name="hitl", prompt="p", schedule="30m", delivery="local",
            )
        self.assertEqual(job["status"], "pending")

        # Even forced due, a pending job must not run.
        self._force_due(job["id"])
        n = asyncio.run(self.scheduler.tick_once(_invoker, in_flight=set()))
        self.assertEqual(n, 0)
        self.assertEqual(runs, [])

        # Confirm → active → fires on the next due tick.
        confirmed = self.svc.confirm_job(job["id"])
        self.assertEqual(confirmed["status"], "active")
        self._force_due(job["id"])
        n = asyncio.run(self.scheduler.tick_once(_invoker, in_flight=set()))
        self.assertEqual(n, 1)
        self.assertEqual(runs, [job["id"]])

    def test_disable_flag_creates_active_backward_compat(self):
        with _env(NUVEL_CRON_HITL_CREATE="0"):
            job = self.svc.create_job(
                name="plain", prompt="p", schedule="30m", delivery="local",
            )
        self.assertEqual(job["status"], "active")

    def test_unset_flag_creates_active_backward_compat(self):
        with _env(NUVEL_CRON_HITL_CREATE=None):
            job = self.svc.create_job(
                name="plain2", prompt="p", schedule="30m", delivery="local",
            )
        self.assertEqual(job["status"], "active")

    def test_confirm_is_idempotent_on_active_job(self):
        with _env(NUVEL_CRON_HITL_CREATE=None):
            job = self.svc.create_job(
                name="idem", prompt="p", schedule="30m", delivery="local",
            )
        again = self.svc.confirm_job(job["id"])
        self.assertEqual(again["status"], "active")

    def test_confirm_unknown_job_raises(self):
        with self.assertRaises(KeyError):
            self.svc.confirm_job("nope")

    def test_secrets_persisted_on_create(self):
        with _env(NUVEL_CRON_HITL_CREATE=None):
            job = self.svc.create_job(
                name="sec", prompt="p", schedule="30m", delivery="local",
                secrets=["SLACK_TOKEN", "SLACK_TOKEN", " GITHUB_TOKEN "],
            )
        # de-duplicated + stripped, order preserved.
        self.assertEqual(job["secrets"], ["SLACK_TOKEN", "GITHUB_TOKEN"])

    def test_no_secrets_is_none(self):
        with _env(NUVEL_CRON_HITL_CREATE=None):
            job = self.svc.create_job(
                name="nosec", prompt="p", schedule="30m", delivery="local",
            )
        self.assertIsNone(job["secrets"])


if __name__ == "__main__":
    unittest.main()
