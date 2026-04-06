# DEV Traces for Self-Improvement Evals

**Date:** 2026-04-06
**Status:** Approved
**Approach:** B — ConversationTraceWriter composed into existing TracePlugin

## Goal

Capture comprehensive traces of every meta-agent conversation for automated eval pipelines that drive self-improvement. DEV mode writes local files; PROD (Neon DB) is a future phase.

## Architecture

Two-layer trace system:

1. **Raw event JSONL** (existing) — fine-grained per-event records for debugging
2. **Consolidated conversation JSON** (new) — one file per conversation for eval consumption

Both layers are orchestrated by the existing `TracePlugin`. The consolidated writer is a composed object with no ADK dependency.

```
traces/
  2026-04-06_<session>.jsonl          # raw events (existing, unchanged)
  conversations/
    2026-04-06_<session>.json         # consolidated record (new)
```

## Changes

### New: `meta_agent/plugins/conversation_trace_writer.py`

Pure data class. Accumulates turn state during a run, writes one JSON file at `finish_run()`.

**Public API:**

```python
class ConversationTraceWriter:
    def start_run(self, *, conversation_id, session_id, agent, model, system_prompt, skills_loaded, tools_available) -> None
    def add_user_turn(self, user_input: str) -> None
    def add_tool_call(self, *, tool, args, result, status, duration_ms) -> None
    def add_llm_call(self, *, thinking, response, function_calls, usage, latency_ms) -> None
    def close_turn(self) -> None
    def finish_run(self) -> Path  # writes file, returns path
```

**Output schema:**

```json
{
  "meta": {
    "conversation_id": "hex16",
    "session_id": "uuid",
    "timestamp_start": "ISO",
    "timestamp_end": "ISO",
    "model": "openrouter/moonshotai/kimi-k2.5",
    "agent": "meta_agent"
  },
  "system_prompt": "full system instruction text",
  "skills_loaded": ["adk-agent-patterns", "adk-tool-creation"],
  "tools_available": ["scaffold_agent", "write_file", "read_file"],
  "turns": [
    {
      "turn_index": 1,
      "user_input": "Create an agent that...",
      "tool_calls": [
        {
          "tool": "scaffold_agent",
          "args": {"name": "my-agent"},
          "result": {"status": "ok", "path": "/tmp/my-agent"},
          "status": "success",
          "duration_ms": 120
        }
      ],
      "llm_calls": [
        {
          "thinking": "The user wants to create...",
          "response": "I'll create an agent for you...",
          "function_calls": [{"name": "scaffold_agent", "args": {"name": "my-agent"}}],
          "usage": {"prompt_tokens": 2967, "completion_tokens": 313},
          "latency_ms": 8390
        }
      ],
      "turn_duration_ms": 9200
    }
  ],
  "summary": {
    "turn_count": 1,
    "total_llm_calls": 1,
    "total_tool_calls": 1,
    "total_tokens": 3280,
    "total_duration_ms": 9200
  }
}
```

**Key design decisions:**

- `llm_calls` is a list per turn because one user message can trigger multiple LLM calls (tool use loops)
- `thinking` is `null` when it cannot be reliably separated from the response
- Tool call args and results are serialized with `_safe_serialize` (truncated at 5000 chars)
- System prompt is captured in full (truncated at 50k chars as safety bound)

### Modified: `meta_agent/plugins/trace_plugin.py`

**Enhanced data capture in existing events:**

`llm_request` gains:
- `system_instruction`: full system prompt text (truncated at 50k)
- `messages`: list of `{role, content_preview}` for conversation history

`llm_response` gains:
- `thinking`: extracted reasoning/thinking text, separate from `response_text`

**Composition with ConversationTraceWriter:**

- Instantiate writer in `__init__`
- `before_run_callback` → `writer.start_run()`
- `on_user_message_callback` → `writer.add_user_turn()`
- `before_model_callback` → capture system prompt + messages for the writer
- `after_model_callback` → `writer.add_llm_call()`
- `after_tool_callback` → `writer.add_tool_call()`
- `after_run_callback` → `writer.close_turn()` + `writer.finish_run()`

### Thinking extraction strategy

- For models with structured thinking (Claude): check `part.thought` on response content parts
- For models that mix thinking into text (Kimi, etc.): no heuristic splitting — thinking stays in `response`, `thinking` is `null`
- The eval agent handles both cases

## Configuration

No new env vars. Reuses existing:
- `TRACE_ENABLED` — master on/off (default: true)
- `TRACE_DIR` — base directory (default: `./traces`); conversations land in `<TRACE_DIR>/conversations/`

## Testing

New file: `tests/test_conversation_trace.py`

Tests the writer in isolation (no ADK dependency):
- Turn accumulation (single turn, multi-turn, multi-LLM-call turns)
- Tool call recording within turns
- Output file structure matches schema
- File naming convention
- Edge cases: empty conversation, turn with no tool calls, missing thinking

## Future (out of scope)

- PROD traces to Neon DB — separate spec when DEV traces are validated
- Eval scoring pipeline that consumes these traces
- Trace retention/rotation policy
