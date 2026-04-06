# DEV Traces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture comprehensive conversation traces as consolidated JSON files for automated self-improvement evals.

**Architecture:** New `ConversationTraceWriter` class (pure Python, no ADK dependency) composed into the existing `TracePlugin`. The writer accumulates turn data during a run and writes one JSON file per conversation. Existing raw event JSONL stays unchanged. Enhanced data capture (system prompt content, thinking extraction, message history) added to the trace plugin's existing callbacks.

**Tech Stack:** Python stdlib (json, pathlib, dataclasses), existing TracePlugin/ADK callbacks

**Spec:** `docs/superpowers/specs/2026-04-06-dev-traces-design.md`

---

### Task 1: ConversationTraceWriter — core data accumulation

**Files:**
- Create: `meta_agent/plugins/conversation_trace_writer.py`
- Create: `tests/test_conversation_trace.py`

- [ ] **Step 1: Write the failing test for single-turn conversation**

```python
# tests/test_conversation_trace.py
"""Tests for ConversationTraceWriter."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from meta_agent.plugins.conversation_trace_writer import ConversationTraceWriter


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
            agent="meta_agent",
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_conversation_trace.py::TestConversationTraceWriter::test_single_turn_produces_valid_json -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meta_agent.plugins.conversation_trace_writer'`

- [ ] **Step 3: Write the ConversationTraceWriter implementation**

```python
# meta_agent/plugins/conversation_trace_writer.py
"""
ConversationTraceWriter — Consolidated conversation traces for evals.

Accumulates turn-level data during an agent run and writes a single
JSON file per conversation to traces/conversations/.

No ADK dependency — receives data via simple method calls from TracePlugin.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MAX_SYSTEM_PROMPT_CHARS = 50_000
_MAX_FIELD_CHARS = 5_000


def _safe_serialize(obj: Any, max_len: int = _MAX_FIELD_CHARS) -> Any:
    """Make an object JSON-serializable, truncating large values."""
    if obj is None:
        return None
    if isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return obj[:max_len] + "...[truncated]" if len(obj) > max_len else obj
    if isinstance(obj, dict):
        return {k: _safe_serialize(v, max_len) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v, max_len) for v in obj]
    return str(obj)[:max_len]


class ConversationTraceWriter:
    """Accumulates conversation data and writes a consolidated JSON trace."""

    def __init__(self, trace_dir: Path) -> None:
        self._trace_dir = trace_dir
        self._reset()

    def _reset(self) -> None:
        self._conversation_id: str = ""
        self._session_id: str = ""
        self._agent: str = ""
        self._model: str = ""
        self._system_prompt: str = ""
        self._skills_loaded: list[str] = []
        self._tools_available: list[str] = []
        self._timestamp_start: str = ""
        self._turns: list[dict] = []
        self._current_turn: Optional[dict] = None
        self._run_start: float = 0

    def start_run(
        self,
        *,
        conversation_id: str,
        session_id: str,
        agent: str,
        model: str,
        system_prompt: str,
        skills_loaded: list[str],
        tools_available: list[str],
    ) -> None:
        self._reset()
        self._conversation_id = conversation_id
        self._session_id = session_id
        self._agent = agent
        self._model = model
        self._system_prompt = system_prompt[:_MAX_SYSTEM_PROMPT_CHARS]
        self._skills_loaded = skills_loaded
        self._tools_available = tools_available
        self._timestamp_start = datetime.now(timezone.utc).isoformat()
        self._run_start = time.monotonic()

    def add_user_turn(self, user_input: str) -> None:
        # Close any unclosed previous turn
        if self._current_turn is not None:
            self.close_turn()
        self._current_turn = {
            "turn_index": len(self._turns) + 1,
            "user_input": user_input,
            "tool_calls": [],
            "llm_calls": [],
            "turn_start": time.monotonic(),
        }

    def add_tool_call(
        self,
        *,
        tool: str,
        args: Any,
        result: Any,
        status: str,
        duration_ms: Optional[int],
    ) -> None:
        if self._current_turn is None:
            return
        self._current_turn["tool_calls"].append({
            "tool": tool,
            "args": _safe_serialize(args),
            "result": _safe_serialize(result),
            "status": status,
            "duration_ms": duration_ms,
        })

    def add_llm_call(
        self,
        *,
        thinking: Optional[str],
        response: Optional[str],
        function_calls: list[dict],
        usage: Optional[dict],
        latency_ms: Optional[int],
    ) -> None:
        if self._current_turn is None:
            return
        self._current_turn["llm_calls"].append({
            "thinking": thinking,
            "response": _safe_serialize(response, max_len=10_000),
            "function_calls": function_calls,
            "usage": usage,
            "latency_ms": latency_ms,
        })

    def close_turn(self) -> None:
        if self._current_turn is None:
            return
        turn_start = self._current_turn.pop("turn_start", None)
        if turn_start is not None:
            self._current_turn["turn_duration_ms"] = round(
                (time.monotonic() - turn_start) * 1000
            )
        else:
            self._current_turn["turn_duration_ms"] = None
        self._turns.append(self._current_turn)
        self._current_turn = None

    def finish_run(self) -> Path:
        # Close any unclosed turn
        if self._current_turn is not None:
            self.close_turn()

        total_llm_calls = sum(len(t["llm_calls"]) for t in self._turns)
        total_tool_calls = sum(len(t["tool_calls"]) for t in self._turns)
        total_tokens = 0
        for t in self._turns:
            for lc in t["llm_calls"]:
                usage = lc.get("usage") or {}
                total_tokens += (usage.get("prompt_tokens") or 0) + (
                    usage.get("completion_tokens") or 0
                )

        trace = {
            "meta": {
                "conversation_id": self._conversation_id,
                "session_id": self._session_id,
                "timestamp_start": self._timestamp_start,
                "timestamp_end": datetime.now(timezone.utc).isoformat(),
                "model": self._model,
                "agent": self._agent,
            },
            "system_prompt": self._system_prompt,
            "skills_loaded": self._skills_loaded,
            "tools_available": self._tools_available,
            "turns": self._turns,
            "summary": {
                "turn_count": len(self._turns),
                "total_llm_calls": total_llm_calls,
                "total_tool_calls": total_tool_calls,
                "total_tokens": total_tokens,
                "total_duration_ms": round(
                    (time.monotonic() - self._run_start) * 1000
                ),
            },
        }

        out_dir = self._trace_dir / "conversations"
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        safe_id = self._session_id[:16].replace("/", "_")
        path = out_dir / f"{date_str}_{safe_id}.json"

        try:
            path.write_text(
                json.dumps(trace, indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(
                "[ConversationTrace] Wrote %d turns to %s", len(self._turns), path
            )
        except Exception as e:
            logger.error("[ConversationTrace] Failed to write trace: %s", e)

        self._reset()
        return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_conversation_trace.py::TestConversationTraceWriter::test_single_turn_produces_valid_json -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meta_agent/plugins/conversation_trace_writer.py tests/test_conversation_trace.py
git commit -m "feat: add ConversationTraceWriter with single-turn test"
```

---

### Task 2: ConversationTraceWriter — multi-turn and edge case tests

**Files:**
- Modify: `tests/test_conversation_trace.py`

- [ ] **Step 1: Add multi-turn, tool calls, and edge case tests**

Append to `TestConversationTraceWriter` class in `tests/test_conversation_trace.py`:

```python
    def test_multi_turn_with_tool_calls(self):
        """Multiple turns with tool calls and multiple LLM calls per turn."""
        self.writer.start_run(
            conversation_id="multi-001",
            session_id="sess-002",
            agent="meta_agent",
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
            agent="meta_agent",
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
            agent="meta_agent",
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
            agent="meta_agent",
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
            agent="meta_agent",
            model="test-model",
            system_prompt="",
            skills_loaded=[],
            tools_available=[],
        )
        path = self.writer.finish_run()

        self.assertIn("abcdef1234567890", path.name)
        self.assertTrue(path.name.endswith(".json"))
        self.assertTrue(path.parent.name == "conversations")
```

- [ ] **Step 2: Run all tests to verify they pass**

Run: `python -m pytest tests/test_conversation_trace.py -v`
Expected: All 6 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_conversation_trace.py
git commit -m "test: add multi-turn and edge case tests for ConversationTraceWriter"
```

---

### Task 3: Extract thinking from LLM responses

**Files:**
- Modify: `meta_agent/plugins/trace_plugin.py` (add helper + modify `after_model_callback`)

- [ ] **Step 1: Add `_extract_thinking` helper and update `_extract_text` to skip thought parts**

After the existing `_extract_text` function (line 72) in `trace_plugin.py`, add:

```python
def _extract_thinking(content: Optional[types.Content]) -> Optional[str]:
    """Extract thinking/reasoning text from a Content object.

    Returns text from parts marked as thought (part.thought == True).
    Returns None if no thinking parts found.
    """
    if not content or not content.parts:
        return None
    thoughts = [p.text for p in content.parts if p.thought and p.text]
    return "\n".join(thoughts) if thoughts else None
```

Then modify `_extract_text` to exclude thinking parts so response text is clean:

Replace the existing `_extract_text` (lines 72-77):
```python
def _extract_text(content: Optional[types.Content]) -> Optional[str]:
    """Extract concatenated text from a Content object (excludes thinking parts)."""
    if not content or not content.parts:
        return None
    texts = [p.text for p in content.parts if p.text and not p.thought]
    return "\n".join(texts) if texts else None
```

- [ ] **Step 2: Update `after_model_callback` to include thinking**

In `after_model_callback` (around line 508), add thinking extraction and include it in the record. Replace lines 508-520:

```python
        text = _extract_text(llm_response.content)
        thinking = _extract_thinking(llm_response.content)
        function_calls = _extract_function_calls(llm_response.content)

        self._record("llm_response", {
            "call_index": self._llm_call_index,
            "model_version": llm_response.model_version,
            "latency_ms": latency_ms,
            "turn_complete": llm_response.turn_complete,
            "finish_reason": str(llm_response.finish_reason) if llm_response.finish_reason else None,
            "usage": usage,
            "thinking": _safe_serialize(thinking, max_len=10_000),
            "response_text": _safe_serialize(text, max_len=10_000),
            "function_calls": function_calls,
        })
```

- [ ] **Step 3: Run existing tests to verify nothing broke**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add meta_agent/plugins/trace_plugin.py
git commit -m "feat: extract thinking from LLM responses in trace plugin"
```

---

### Task 4: Capture system prompt and message history in llm_request

**Files:**
- Modify: `meta_agent/plugins/trace_plugin.py` (add helpers + modify `before_model_callback`)

- [ ] **Step 1: Add `_extract_system_instruction` and `_extract_messages` helpers**

Add after the `_extract_thinking` function:

```python
def _extract_system_instruction(llm_request: LlmRequest) -> Optional[str]:
    """Extract full system instruction text from an LLM request."""
    if not llm_request.config or not llm_request.config.system_instruction:
        return None
    si = llm_request.config.system_instruction
    if isinstance(si, str):
        return si[:50_000]
    if hasattr(si, "parts") and si.parts:
        text = "\n".join(p.text or "" for p in si.parts)
        return text[:50_000] if text else None
    return None


def _extract_messages(contents: Optional[list[types.Content]]) -> list[dict]:
    """Extract message history as a list of {role, content_preview} dicts."""
    if not contents:
        return []
    messages = []
    for content in contents:
        role = content.role or "user"
        text = _extract_text(content)
        fn_calls = _extract_function_calls(content)
        preview = text[:500] if text else None
        messages.append({
            "role": role,
            "content_preview": preview,
            "has_function_calls": len(fn_calls) > 0,
        })
    return messages
```

- [ ] **Step 2: Update `before_model_callback` to include system prompt and messages**

Replace `before_model_callback` (lines 460-490):

```python
    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> Optional[LlmResponse]:
        self._llm_count += 1
        self._llm_call_index += 1
        agent_name = (
            callback_context.agent_name
            if hasattr(callback_context, "agent_name")
            else "unknown"
        )
        self._llm_starts[agent_name] = time.monotonic()

        tools_available = list(llm_request.tools_dict.keys()) if llm_request.tools_dict else []
        system_instruction = _extract_system_instruction(llm_request)
        messages = _extract_messages(llm_request.contents)

        # Capture system prompt on first LLM call for the conversation writer
        if self._llm_call_index == 1 and system_instruction:
            self._first_system_prompt = system_instruction
            self._first_tools_available = tools_available

        self._record("llm_request", {
            "call_index": self._llm_call_index,
            "model": llm_request.model,
            "message_count": len(messages),
            "messages": messages,
            "tools_available": tools_available,
            "system_instruction": _safe_serialize(system_instruction, max_len=50_000),
            "system_instruction_chars": len(system_instruction) if system_instruction else 0,
        })
        return None
```

- [ ] **Step 3: Add `_first_system_prompt` and `_first_tools_available` to `__init__`**

In `__init__` (after line 377), add:

```python
        self._first_system_prompt: str = ""
        self._first_tools_available: list[str] = []
```

- [ ] **Step 4: Run tests to verify nothing broke**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add meta_agent/plugins/trace_plugin.py
git commit -m "feat: capture system prompt content and message history in traces"
```

---

### Task 5: Wire ConversationTraceWriter into TracePlugin

**Files:**
- Modify: `meta_agent/plugins/trace_plugin.py`

- [ ] **Step 1: Import and instantiate ConversationTraceWriter**

Add import at top of `trace_plugin.py` (after existing imports):

```python
from .conversation_trace_writer import ConversationTraceWriter
```

In `TracePlugin.__init__` (after `self._db_writer` line), add:

```python
        self._conversation_writer = ConversationTraceWriter(trace_dir=_TRACE_DIR)
```

- [ ] **Step 2: Feed data to the writer in `before_run_callback`**

In `before_run_callback`, after the existing `self._record("run_start", ...)` call, add:

```python
        # Detect skills loaded from the agent's tools
        skills_loaded = []
        if hasattr(invocation_context.agent, "tools") and invocation_context.agent.tools:
            for tool in invocation_context.agent.tools:
                if hasattr(tool, "skills"):
                    skills_loaded = [s.name for s in tool.skills]
                    break

        self._conversation_writer.start_run(
            conversation_id=self._run_id,
            session_id=self._session_id,
            agent=invocation_context.agent.name,
            model="",  # captured on first LLM call
            system_prompt="",  # captured on first LLM call
            skills_loaded=skills_loaded,
            tools_available=[],  # captured on first LLM call
        )
```

- [ ] **Step 3: Feed user turns in `on_user_message_callback`**

In `on_user_message_callback`, after the existing `self._record(...)` call, add:

```python
        self._conversation_writer.add_user_turn(_extract_text(user_message) or "")
```

- [ ] **Step 4: Feed LLM calls in `after_model_callback`**

In `after_model_callback`, after the existing `self._record("llm_response", ...)` call, add:

```python
        # Update model/system_prompt on first LLM call
        if self._llm_call_index == 1:
            self._conversation_writer._model = llm_response.model_version or ""
            self._conversation_writer._system_prompt = self._first_system_prompt
            self._conversation_writer._tools_available = self._first_tools_available

        self._conversation_writer.add_llm_call(
            thinking=_extract_thinking(llm_response.content),
            response=text,
            function_calls=function_calls,
            usage=usage,
            latency_ms=latency_ms,
        )
```

- [ ] **Step 5: Feed tool calls in `after_tool_callback`**

In `after_tool_callback`, after the existing `self._record("tool_end", ...)` call, add:

```python
        self._conversation_writer.add_tool_call(
            tool=tool.name,
            args=tool_args,
            result=result,
            status="error" if is_error else "success",
            duration_ms=duration_ms,
        )
```

- [ ] **Step 6: Finish the conversation trace in `after_run_callback`**

In `after_run_callback`, after the existing `self._record("run_end", ...)` call, add:

```python
        if _TRACE_ENABLED:
            self._conversation_writer.finish_run()
```

- [ ] **Step 7: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add meta_agent/plugins/trace_plugin.py
git commit -m "feat: wire ConversationTraceWriter into TracePlugin"
```

---

### Task 6: Manual integration test

**Files:** None (verification only)

- [ ] **Step 1: Start the dev server with plugins**

Run: `make dev-ui`

- [ ] **Step 2: Send a test message in the ADK dev UI**

Open http://127.0.0.1:8000, send a message like "Create a simple hello-world agent".

- [ ] **Step 3: Verify raw event traces are enhanced**

Check the JSONL file in `traces/` has `system_instruction` content and `messages` list in `llm_request` events, and `thinking` field in `llm_response` events:

```bash
cat traces/2026-04-06_*.jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    e = json.loads(line)
    if e['event'] == 'llm_request':
        print('system_instruction present:', 'system_instruction' in e)
        print('messages present:', 'messages' in e)
        break
"
```

- [ ] **Step 4: Verify consolidated conversation trace was written**

```bash
ls traces/conversations/
cat traces/conversations/2026-04-06_*.json | python3 -m json.tool | head -40
```

Expected: A well-formed JSON file with `meta`, `system_prompt`, `turns`, and `summary` fields.

- [ ] **Step 5: Commit any fixes if needed, then final commit**

```bash
git add -A
git commit -m "feat: complete DEV traces for self-improvement evals"
```
