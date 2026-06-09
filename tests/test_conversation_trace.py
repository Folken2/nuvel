"""Tests for ConversationTraceWriter."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from nuvel.plugins.conversation_trace_writer import ConversationTraceWriter


class TestConversationTraceWriter(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.trace_dir = Path(self.tmpdir) / "traces"
        self.writer = ConversationTraceWriter(trace_dir=self.trace_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_single_turn_produces_valid_json(self):
        """A single user turn with one LLM call produces a valid trace file."""
        self.writer.start_run(
            conversation_id="abc123",
            session_id="sess-001",
            agent="nuvel",
            model="openrouter/test-model",
            system_prompt="You are a helpful agent.",
            skills_loaded=["skill-a"],
            tools_available=["tool_x", "tool_y"],
        )
        self.writer.add_user_turn("Hello agent")
        self.writer.add_llm_call(
            thinking=None,
            response="Hi there!",
            function_calls=[],
            usage={"prompt_tokens": 100, "completion_tokens": 20},
            latency_ms=500,
        )
        self.writer.close_turn()
        path = self.writer.finish_run()

        self.assertTrue(path.exists())
        self.assertTrue(path.name.endswith(".json"))
        self.assertTrue(path.parent.name == "conversations")

        data = json.loads(path.read_text())
        self.assertEqual(data["meta"]["conversation_id"], "abc123")
        self.assertEqual(data["meta"]["session_id"], "sess-001")
        self.assertEqual(data["meta"]["model"], "openrouter/test-model")
        self.assertEqual(data["system_prompt"], "You are a helpful agent.")
        self.assertEqual(data["skills_loaded"], ["skill-a"])
        self.assertEqual(data["tools_available"], ["tool_x", "tool_y"])
        self.assertEqual(len(data["turns"]), 1)

        turn = data["turns"][0]
        self.assertEqual(turn["turn_index"], 1)
        self.assertEqual(turn["user_input"], "Hello agent")
        self.assertEqual(turn["llm_calls"][0]["response"], "Hi there!")
        self.assertIsNone(turn["llm_calls"][0]["thinking"])
        self.assertEqual(turn["tool_calls"], [])

        self.assertEqual(data["summary"]["turn_count"], 1)
        self.assertEqual(data["summary"]["total_llm_calls"], 1)
        self.assertEqual(data["summary"]["total_tool_calls"], 0)
        self.assertEqual(data["summary"]["total_tokens"], 120)

    def test_context_window_persisted_and_peak_in_summary(self):
        """Per-call context_window is stored and the peak surfaces in summary."""
        self.writer.start_run(
            conversation_id="ctx-001",
            session_id="sess-ctx",
            agent="nuvel",
            model="anthropic/claude-sonnet-4",
            system_prompt="sys",
            skills_loaded=[],
            tools_available=[],
        )
        self.writer.add_user_turn("first")
        self.writer.add_llm_call(
            thinking=None, response="a", function_calls=[],
            usage={"prompt_tokens": 1000, "completion_tokens": 100},
            latency_ms=10,
            context_window={"used_tokens": 1100, "used_pct": 0.55, "max_tokens": 200000},
        )
        # Second call has higher occupancy — should become the peak.
        self.writer.add_llm_call(
            thinking=None, response="b", function_calls=[],
            usage={"prompt_tokens": 5000, "completion_tokens": 300},
            latency_ms=10,
            context_window={"used_tokens": 5300, "used_pct": 2.65, "max_tokens": 200000},
        )
        self.writer.close_turn()
        data = json.loads(self.writer.finish_run().read_text())

        calls = data["turns"][0]["llm_calls"]
        self.assertEqual(calls[0]["context_window"]["used_tokens"], 1100)
        self.assertEqual(calls[1]["context_window"]["used_pct"], 2.65)

        self.assertEqual(data["summary"]["peak_context_tokens"], 5300)
        self.assertEqual(data["summary"]["peak_context_pct"], 2.65)

    def test_no_context_window_leaves_peak_none(self):
        """Without context_window data the peak fields are None, not errors."""
        self.writer.start_run(
            conversation_id="ctx-002", session_id="sess-ctx2", agent="nuvel",
            model="m", system_prompt="s", skills_loaded=[], tools_available=[],
        )
        self.writer.add_user_turn("hi")
        self.writer.add_llm_call(
            thinking=None, response="x", function_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5}, latency_ms=1,
        )
        self.writer.close_turn()
        data = json.loads(self.writer.finish_run().read_text())
        self.assertIsNone(data["summary"]["peak_context_tokens"])
        self.assertIsNone(data["summary"]["peak_context_pct"])
        self.assertIsNone(data["turns"][0]["llm_calls"][0]["context_window"])

    def test_multi_turn_with_tool_calls(self):
        """Multiple turns with tool calls and multiple LLM calls per turn."""
        self.writer.start_run(
            conversation_id="multi-001",
            session_id="sess-002",
            agent="nuvel",
            model="test-model",
            system_prompt="System prompt.",
            skills_loaded=[],
            tools_available=["scaffold_agent"],
        )

        # Turn 1: user asks, LLM calls a tool, then responds
        self.writer.add_user_turn("Create an agent called foo")
        self.writer.add_llm_call(
            thinking="Need to scaffold",
            response=None,
            function_calls=[{"name": "scaffold_agent", "args": {"name": "foo"}}],
            usage={"prompt_tokens": 500, "completion_tokens": 50},
            latency_ms=1000,
        )
        self.writer.add_tool_call(
            tool="scaffold_agent",
            args={"name": "foo"},
            result={"status": "ok", "path": "/tmp/foo"},
            status="success",
            duration_ms=200,
        )
        self.writer.add_llm_call(
            thinking=None,
            response="Created agent foo!",
            function_calls=[],
            usage={"prompt_tokens": 600, "completion_tokens": 30},
            latency_ms=800,
        )
        self.writer.close_turn()

        # Turn 2: follow-up
        self.writer.add_user_turn("Add a greeting tool")
        self.writer.add_llm_call(
            thinking=None,
            response="Done!",
            function_calls=[],
            usage={"prompt_tokens": 700, "completion_tokens": 20},
            latency_ms=600,
        )
        self.writer.close_turn()

        path = self.writer.finish_run()
        data = json.loads(path.read_text())

        self.assertEqual(len(data["turns"]), 2)
        self.assertEqual(len(data["turns"][0]["llm_calls"]), 2)
        self.assertEqual(len(data["turns"][0]["tool_calls"]), 1)
        self.assertEqual(data["turns"][0]["tool_calls"][0]["tool"], "scaffold_agent")
        self.assertEqual(data["turns"][0]["llm_calls"][0]["thinking"], "Need to scaffold")
        self.assertEqual(data["summary"]["turn_count"], 2)
        self.assertEqual(data["summary"]["total_llm_calls"], 3)
        self.assertEqual(data["summary"]["total_tool_calls"], 1)
        self.assertEqual(data["summary"]["total_tokens"], 1900)

    def test_empty_conversation(self):
        """A run with no turns produces valid trace with empty turns list."""
        self.writer.start_run(
            conversation_id="empty-001",
            session_id="sess-003",
            agent="nuvel",
            model="test-model",
            system_prompt="Prompt.",
            skills_loaded=[],
            tools_available=[],
        )
        path = self.writer.finish_run()
        data = json.loads(path.read_text())

        self.assertEqual(data["turns"], [])
        self.assertEqual(data["summary"]["turn_count"], 0)
        self.assertEqual(data["summary"]["total_tokens"], 0)

    def test_unclosed_turn_auto_closes(self):
        """Finish_run auto-closes an unclosed turn."""
        self.writer.start_run(
            conversation_id="unclose-001",
            session_id="sess-004",
            agent="nuvel",
            model="test-model",
            system_prompt="Prompt.",
            skills_loaded=[],
            tools_available=[],
        )
        self.writer.add_user_turn("Hello")
        self.writer.add_llm_call(
            thinking=None,
            response="Hi!",
            function_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            latency_ms=100,
        )
        # No close_turn() call — finish_run should handle it
        path = self.writer.finish_run()
        data = json.loads(path.read_text())

        self.assertEqual(len(data["turns"]), 1)
        self.assertEqual(data["turns"][0]["user_input"], "Hello")

    def test_add_user_turn_auto_closes_previous(self):
        """Starting a new turn auto-closes the previous one."""
        self.writer.start_run(
            conversation_id="auto-001",
            session_id="sess-005",
            agent="nuvel",
            model="test-model",
            system_prompt="Prompt.",
            skills_loaded=[],
            tools_available=[],
        )
        self.writer.add_user_turn("First")
        self.writer.add_llm_call(
            thinking=None, response="R1", function_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5}, latency_ms=100,
        )
        # No explicit close — add_user_turn should auto-close
        self.writer.add_user_turn("Second")
        self.writer.add_llm_call(
            thinking=None, response="R2", function_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5}, latency_ms=100,
        )
        path = self.writer.finish_run()
        data = json.loads(path.read_text())

        self.assertEqual(len(data["turns"]), 2)
        self.assertEqual(data["turns"][0]["user_input"], "First")
        self.assertEqual(data["turns"][1]["user_input"], "Second")

    def test_file_naming_convention(self):
        """Output file follows date_sessionid.json pattern."""
        self.writer.start_run(
            conversation_id="name-001",
            session_id="abcdef1234567890-extra",
            agent="nuvel",
            model="test-model",
            system_prompt="",
            skills_loaded=[],
            tools_available=[],
        )
        path = self.writer.finish_run()

        self.assertIn("abcdef1234567890", path.name)
        self.assertTrue(path.name.endswith(".json"))
        self.assertTrue(path.parent.name == "conversations")


if __name__ == "__main__":
    unittest.main()
