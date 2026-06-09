"""Tests for the unified slash-command registry shipped in gateway-base.

The registry module lives inside a *generated* agent — so each test scaffolds
a tiny agent in a tmpdir, then imports its `commands` module fresh. Importing
fresh per-class also means the module-level `_REGISTRY` is reinitialized,
which keeps tests deterministic.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from nuvel.backends.adk.scaffold import scaffold_agent


def _scaffold_with(tmpdir, **flags):
    result = scaffold_agent("agent-test", output_dir=tmpdir, **flags)
    if result["status"] != "ok":
        raise AssertionError(result.get("message"))
    return Path(result["path"]) / "agent_test"


def _import_module(pkg_dir: Path, dotted: str, *, fresh_name: str | None = None):
    file_path = pkg_dir / Path(*dotted.split(".")).with_suffix(".py")
    name = fresh_name or f"_gw_{dotted.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _CommandsTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.pkg = _scaffold_with(cls.tmpdir, with_telegram=True)
        cls.commands = _import_module(cls.pkg, "gateways.commands", fresh_name=f"_cmds_{cls.__name__}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)


class TestRegistryShape(_CommandsTestBase):
    def test_builtin_commands_are_registered(self):
        names = [r.name for r in self.commands.list_commands()]
        self.assertIn("/new", names)
        self.assertIn("/help", names)
        self.assertIn("/usage", names)
        self.assertIn("/stop", names)

    def test_reset_is_alias_of_new(self):
        self.assertTrue(self.commands.is_command("/reset"))
        self.assertTrue(self.commands.is_command("/new"))

    def test_is_command_rejects_unknown_and_non_slash(self):
        self.assertFalse(self.commands.is_command("hello"))
        self.assertFalse(self.commands.is_command("/nope"))
        self.assertFalse(self.commands.is_command(""))

    def test_decorator_registers_custom_command(self):
        commands = self.commands

        @commands.command("/ping", help="ping")
        async def _h(ctx):
            return commands.CommandResult(handled=True, replies=["pong"])

        self.assertTrue(commands.is_command("/ping"))


class TestDispatchOrdering(_CommandsTestBase):
    def test_unknown_text_passes_through(self):
        ctx = self.commands.CommandContext(
            user_id="u", channel="c", session_id="s", text="hello world",
        )
        result = asyncio.run(self.commands.try_dispatch("hello world", ctx))
        self.assertFalse(result.handled)
        self.assertEqual(result.replies, [])

    def test_help_returns_listing(self):
        ctx = self.commands.CommandContext(
            user_id="u", channel="c", session_id="s", text="/help",
        )
        result = asyncio.run(self.commands.try_dispatch("/help", ctx))
        self.assertTrue(result.handled)
        self.assertEqual(len(result.replies), 1)
        text = result.replies[0]
        self.assertIn("/new", text)
        self.assertIn("/help", text)
        self.assertIn("/usage", text)
        self.assertIn("/stop", text)
        # alias visibility
        self.assertIn("/reset", text)

    def test_alias_dispatches_same_handler(self):
        # /reset should hit /new which requires a runner; without one it
        # gracefully reports "unavailable here".
        ctx = self.commands.CommandContext(
            user_id="u", channel="c", session_id="s", text="/reset",
        )
        result = asyncio.run(self.commands.try_dispatch("/reset", ctx))
        self.assertTrue(result.handled)
        self.assertTrue(any("unavailable" in r.lower() for r in result.replies))

    def test_case_insensitive_and_arg_tail(self):
        seen: dict[str, str] = {}

        @self.commands.command("/echo", help="echo args")
        async def _h(ctx):
            seen["text"] = ctx.text
            return self.commands.CommandResult(handled=True, replies=[ctx.text])

        ctx = self.commands.CommandContext(
            user_id="u", channel="c", session_id="s", text="",
        )
        result = asyncio.run(self.commands.try_dispatch("/Echo hello there", ctx))
        self.assertTrue(result.handled)
        self.assertEqual(seen["text"], "hello there")
        self.assertEqual(result.replies, ["hello there"])


class TestNewCommandResetsSession(_CommandsTestBase):
    def test_new_deletes_then_creates(self):
        runner = MagicMock()
        runner.session_service.get_session = AsyncMock(return_value=object())
        runner.session_service.delete_session = AsyncMock(return_value=None)
        runner.session_service.create_session = AsyncMock(return_value=None)

        ctx = self.commands.CommandContext(
            user_id="u1", channel="c1", session_id="s1",
            text="/new", runner=runner, app_name="agent_test",
        )
        result = asyncio.run(self.commands.try_dispatch("/new", ctx))
        self.assertTrue(result.handled)
        runner.session_service.delete_session.assert_awaited_once()
        runner.session_service.create_session.assert_awaited_once()

    def test_new_creates_when_no_session_exists(self):
        runner = MagicMock()
        runner.session_service.get_session = AsyncMock(return_value=None)
        runner.session_service.delete_session = AsyncMock(return_value=None)
        runner.session_service.create_session = AsyncMock(return_value=None)

        ctx = self.commands.CommandContext(
            user_id="u2", channel="c2", session_id="s2",
            text="/new", runner=runner, app_name="agent_test",
        )
        result = asyncio.run(self.commands.try_dispatch("/new", ctx))
        self.assertTrue(result.handled)
        runner.session_service.delete_session.assert_not_awaited()
        runner.session_service.create_session.assert_awaited_once()


class TestUsageCommand(_CommandsTestBase):
    def _run_usage(self, *, events, state):
        sess = MagicMock()
        sess.events = events
        sess.state = state
        runner = MagicMock()
        runner.session_service.get_session = AsyncMock(return_value=sess)
        ctx = self.commands.CommandContext(
            user_id="u", channel="c", session_id="s",
            text="/usage", runner=runner, app_name="agent_test",
        )
        result = asyncio.run(self.commands.try_dispatch("/usage", ctx))
        self.assertTrue(result.handled)
        return result.replies[0]

    def test_usage_reports_context_and_cost_from_state(self):
        events = [MagicMock(author="user"), MagicMock(author="model"), MagicMock(author="user")]
        state = {
            "context_window": {
                "used_tokens": 146000, "used_pct": 73.0, "max_tokens": 200000,
            },
            "cost_guard": {"session_cost_usd": 0.0123, "budget_usd": 0.0},
        }
        text = self._run_usage(events=events, state=state)
        self.assertIn("2 user turn(s)", text)
        self.assertIn("73.0% used", text)
        self.assertIn("146.0k/200.0k", text)
        self.assertIn("$0.0123", text)

    def test_usage_shows_budget_when_set(self):
        state = {"cost_guard": {"session_cost_usd": 0.25, "budget_usd": 0.50}}
        text = self._run_usage(events=[MagicMock(author="user")], state=state)
        self.assertIn("$0.2500 of $0.50 budget", text)

    def test_usage_without_state_reports_turns_only(self):
        text = self._run_usage(events=[MagicMock(author="user")], state={})
        self.assertIn("1 user turn(s)", text)
        self.assertNotIn("Context:", text)
        self.assertNotIn("Cost:", text)

    def test_usage_unavailable_without_runner(self):
        ctx = self.commands.CommandContext(
            user_id="u", channel="c", session_id="s", text="/usage",
        )
        result = asyncio.run(self.commands.try_dispatch("/usage", ctx))
        self.assertTrue(result.handled)
        self.assertTrue(any("unavailable" in r.lower() for r in result.replies))


class TestStopAndCancelEvent(_CommandsTestBase):
    def test_stop_without_active_run(self):
        ctx = self.commands.CommandContext(
            user_id="u", channel="c", session_id="no-run", text="/stop",
        )
        result = asyncio.run(self.commands.try_dispatch("/stop", ctx))
        self.assertTrue(result.handled)
        self.assertTrue(any("nothing to stop" in r.lower() for r in result.replies))

    def test_stop_sets_existing_event(self):
        async def _run():
            ev = self.commands.get_cancel_event("active-run")
            self.assertFalse(ev.is_set())
            ctx = self.commands.CommandContext(
                user_id="u", channel="c", session_id="active-run", text="/stop",
            )
            result = await self.commands.try_dispatch("/stop", ctx)
            self.assertTrue(result.handled)
            self.assertTrue(ev.is_set())
            self.commands.clear_cancel_event("active-run")

        asyncio.run(_run())


class TestHandlerExceptionsAreContained(_CommandsTestBase):
    def test_handler_exception_returns_handled_with_apology(self):
        @self.commands.command("/boom", help="explode")
        async def _h(ctx):
            raise RuntimeError("kaboom")

        ctx = self.commands.CommandContext(
            user_id="u", channel="c", session_id="s", text="/boom",
        )
        result = asyncio.run(self.commands.try_dispatch("/boom", ctx))
        self.assertTrue(result.handled)
        self.assertTrue(any("failed" in r.lower() for r in result.replies))


if __name__ == "__main__":
    unittest.main()
