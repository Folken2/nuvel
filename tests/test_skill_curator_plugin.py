"""Tests for SkillCuratorPlugin — post-run self-improving skills proposer.

Plugin variant: opt-in via NUVEL_SKILL_CURATOR=1, runs at Runner scope
(after_run_callback), aggregates state across after_tool / on_event
across the whole run trajectory (multi-agent aware), and writes
proposals to ~/.nuvel/skill-proposals/ for human review. NEVER
auto-applies.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nuvel.plugins import skill_curator_plugin as scp


# ── Helpers ──────────────────────────────────────────────────────────


def _event(text=None, function_call=None, author="agent"):
    parts = []
    if text is not None:
        parts.append(SimpleNamespace(text=text, function_call=None, function_response=None))
    if function_call is not None:
        parts.append(
            SimpleNamespace(
                text=None,
                function_call=SimpleNamespace(name=function_call),
                function_response=None,
            )
        )
    content = SimpleNamespace(parts=parts) if parts else None
    return SimpleNamespace(content=content, author=author)


def _ic(agent_name="root_agent"):
    """Build a minimal InvocationContext-like object."""
    session = SimpleNamespace(id="s1", events=[], app_name="app", user_id="u1")
    agent = SimpleNamespace(name=agent_name)
    return SimpleNamespace(session=session, agent=agent, invocation_id="inv1")


def _tool(name="some_tool"):
    return SimpleNamespace(name=name)


def _tool_ctx():
    return SimpleNamespace(state={})


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ── Heuristic ────────────────────────────────────────────────────────


class TestComplexityHeuristic(unittest.TestCase):
    def test_below_threshold(self):
        plugin = scp.SkillCuratorPlugin()
        self.assertFalse(plugin._is_complex(tool_calls=2, event_count=4, error_count=0,
                                            min_tools=5, min_events=12))

    def test_above_by_tool_count(self):
        plugin = scp.SkillCuratorPlugin()
        self.assertTrue(plugin._is_complex(tool_calls=6, event_count=4, error_count=0,
                                           min_tools=5, min_events=12))

    def test_above_by_event_count(self):
        plugin = scp.SkillCuratorPlugin()
        self.assertTrue(plugin._is_complex(tool_calls=0, event_count=15, error_count=0,
                                           min_tools=5, min_events=12))

    def test_above_by_error_signal(self):
        plugin = scp.SkillCuratorPlugin()
        # Repeated errors of one tool >= 3 always trip
        self.assertTrue(plugin._is_complex(tool_calls=0, event_count=0, error_count=3,
                                           min_tools=5, min_events=12))


# ── Env gate ─────────────────────────────────────────────────────────


class TestEnvGate(unittest.TestCase):
    def test_disabled_when_unset(self):
        llm_fn = mock.Mock()
        plugin = scp.SkillCuratorPlugin(llm_fn=llm_fn)
        # Simulate enough activity to trigger were the gate open
        plugin._tool_calls = 10
        plugin._event_count = 20
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NUVEL_SKILL_CURATOR", None)
            asyncio.run(plugin.after_run_callback(invocation_context=_ic()))
        llm_fn.assert_not_called()

    def test_enabled_when_set(self):
        llm_fn = mock.Mock(return_value=json.dumps({"action": "noop", "rationale": "ok"}))
        plugin = scp.SkillCuratorPlugin(llm_fn=llm_fn)
        plugin._tool_calls = 10
        plugin._event_count = 20
        plugin._triggering_agents.add("root_agent")
        with mock.patch.dict(os.environ, {"NUVEL_SKILL_CURATOR": "1"}):
            asyncio.run(plugin.after_run_callback(invocation_context=_ic()))
        llm_fn.assert_called_once()


# ── Aggregation across the trajectory ────────────────────────────────


class TestAggregation(unittest.TestCase):
    def test_after_tool_increments_counter(self):
        plugin = scp.SkillCuratorPlugin()
        async def go():
            for i in range(4):
                await plugin.after_tool_callback(
                    tool=_tool(f"t{i}"),
                    tool_args={},
                    tool_context=_tool_ctx(),
                    result={"status": "ok"},
                )
        asyncio.run(go())
        self.assertEqual(plugin._tool_calls, 4)

    def test_on_event_aggregates_event_count_and_agents(self):
        plugin = scp.SkillCuratorPlugin()
        async def go():
            await plugin.on_event_callback(
                invocation_context=_ic(), event=_event(text="hi", author="agent_a"),
            )
            await plugin.on_event_callback(
                invocation_context=_ic(), event=_event(function_call="x", author="agent_b"),
            )
        asyncio.run(go())
        self.assertEqual(plugin._event_count, 2)
        self.assertSetEqual(plugin._triggering_agents, {"agent_a", "agent_b"})

    def test_tool_error_accumulates(self):
        plugin = scp.SkillCuratorPlugin()
        async def go():
            for _ in range(3):
                await plugin.on_tool_error_callback(
                    tool=_tool("flaky"),
                    tool_args={},
                    tool_context=_tool_ctx(),
                    error=RuntimeError("boom"),
                )
        asyncio.run(go())
        self.assertEqual(plugin._tool_errors["flaky"], 3)


# ── Proposal write paths ─────────────────────────────────────────────


class TestProposalWriting(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nuvel-prop-")
        self.env = {
            "NUVEL_SKILL_CURATOR": "1",
            "NUVEL_SKILL_PROPOSALS_DIR": self.tmpdir,
        }

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_with(self, llm_fn, *, tool_calls=8, event_count=0, agents=("root_agent",), errors=None):
        plugin = scp.SkillCuratorPlugin(llm_fn=llm_fn)
        plugin._tool_calls = tool_calls
        plugin._event_count = event_count
        plugin._triggering_agents = set(agents)
        plugin._tool_errors = dict(errors or {})
        with mock.patch.dict(os.environ, self.env):
            asyncio.run(plugin.after_run_callback(invocation_context=_ic(agents[0])))

    def test_propose_new_writes_proposal(self):
        payload = {
            "action": "propose_new",
            "skill_name": "csv-summarizer",
            "rationale": "Pattern of CSV reads + summaries.",
            "patch": "# csv-summarizer\n\nWhen given a CSV...",
        }
        llm_fn = mock.Mock(return_value=json.dumps(payload))
        self._run_with(llm_fn)
        files = list(Path(self.tmpdir).glob("*.md"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text()
        self.assertIn("csv-summarizer", content)
        self.assertIn("propose_new", content)
        self.assertIn("Pattern of CSV reads", content)

    def test_noop_writes_nothing(self):
        llm_fn = mock.Mock(return_value=json.dumps({"action": "noop", "rationale": "fine"}))
        self._run_with(llm_fn)
        self.assertEqual(list(Path(self.tmpdir).glob("*.md")), [])

    def test_patch_existing_writes(self):
        payload = {
            "action": "patch_existing",
            "skill_name": "adk-tool-creation",
            "rationale": "Missing async edge case.",
            "patch": "Append: ## Async tools",
        }
        llm_fn = mock.Mock(return_value=json.dumps(payload))
        self._run_with(llm_fn)
        files = list(Path(self.tmpdir).glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("patch_existing", files[0].read_text())
        self.assertIn("adk-tool-creation", files[0].name)

    def test_malformed_json_skipped(self):
        llm_fn = mock.Mock(return_value="not-json {{{")
        self._run_with(llm_fn)
        self.assertEqual(list(Path(self.tmpdir).glob("*.md")), [])

    def test_unknown_action_skipped(self):
        llm_fn = mock.Mock(return_value=json.dumps({"action": "delete_everything"}))
        self._run_with(llm_fn)
        self.assertEqual(list(Path(self.tmpdir).glob("*.md")), [])

    def test_multi_agent_attribution_in_frontmatter(self):
        payload = {
            "action": "propose_new",
            "skill_name": "multi-agent-skill",
            "rationale": "spans agents",
            "patch": "body",
        }
        llm_fn = mock.Mock(return_value=json.dumps(payload))
        self._run_with(llm_fn, agents=("agent_a", "agent_b"))
        files = list(Path(self.tmpdir).glob("*.md"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text()
        self.assertIn("triggering_agents:", text)
        self.assertIn("agent_a", text)
        self.assertIn("agent_b", text)

    def test_tool_error_signal_triggers_proposal_path(self):
        """3+ errors on the same tool should trip complexity even with low tool_calls."""
        payload = {
            "action": "propose_new",
            "skill_name": "error-recovery",
            "rationale": "tool repeatedly failed",
            "patch": "body",
        }
        llm_fn = mock.Mock(return_value=json.dumps(payload))
        self._run_with(
            llm_fn,
            tool_calls=0,
            event_count=0,
            errors={"flaky_tool": 3},
        )
        # Heuristic should have triggered the LLM and produced a proposal
        llm_fn.assert_called_once()
        files = list(Path(self.tmpdir).glob("*.md"))
        self.assertEqual(len(files), 1)
        # The error signal should make it into the prompt
        prompt_arg = llm_fn.call_args[0][0]
        self.assertIn("flaky_tool", prompt_arg)


# ── Existing skills enumeration ──────────────────────────────────────


class TestSkillEnumeration(unittest.TestCase):
    def test_lists_skill_directories(self):
        d = tempfile.mkdtemp(prefix="nuvel-skills-")
        try:
            (Path(d) / "skill-a").mkdir()
            (Path(d) / "skill-a" / "SKILL.md").write_text("body")
            (Path(d) / "skill-b").mkdir()
            (Path(d) / "skill-b" / "SKILL.md").write_text("body")
            (Path(d) / "not-a-skill").mkdir()
            names = scp._existing_skill_names(Path(d))
            self.assertEqual(sorted(names), ["skill-a", "skill-b"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ── Below-threshold short-circuit ────────────────────────────────────


class TestBelowThresholdShortCircuit(unittest.TestCase):
    def test_no_llm_call_when_simple(self):
        llm_fn = mock.Mock()
        plugin = scp.SkillCuratorPlugin(llm_fn=llm_fn)
        plugin._tool_calls = 1
        plugin._event_count = 1
        with mock.patch.dict(os.environ, {"NUVEL_SKILL_CURATOR": "1"}):
            asyncio.run(plugin.after_run_callback(invocation_context=_ic()))
        llm_fn.assert_not_called()


# ── Per-run reset ────────────────────────────────────────────────────


class TestRunReset(unittest.TestCase):
    def test_before_run_resets_state(self):
        plugin = scp.SkillCuratorPlugin()
        plugin._tool_calls = 10
        plugin._event_count = 20
        plugin._tool_errors = {"x": 5}
        plugin._triggering_agents.add("a")
        asyncio.run(plugin.before_run_callback(invocation_context=_ic()))
        self.assertEqual(plugin._tool_calls, 0)
        self.assertEqual(plugin._event_count, 0)
        self.assertEqual(plugin._tool_errors, {})
        self.assertEqual(plugin._triggering_agents, set())


if __name__ == "__main__":
    unittest.main()
