---
name: adk-tool-creation
description: >-
  Building Google ADK function tools — ToolContext usage, proper signatures,
  error handling, return formats, type hints, and async patterns. Load this
  skill when creating custom tools for an agent.
---

# ADK Tool Creation

## Core Rules

1. Every tool is a plain Python function wrapped in `FunctionTool`.
2. Required signature pattern:
   ```python
   def my_tool(param1: str, param2: int, tool_context: ToolContext) -> dict:
   ```
3. Always return a `dict` with at minimum `"status"` and either `"message"` or `"data"` keys.
4. Handle errors gracefully — return `{"status": "error", "error": "description"}`. **Never raise exceptions** from a tool; the framework cannot recover from them gracefully.
5. Write a clear, specific docstring. The LLM reads the docstring to decide **when** and **how** to call the tool. Include:
   - A one-line summary of what the tool does.
   - An `Args:` section with each parameter described.
   - A `Returns:` section describing the response shape.
6. Use type hints for **every** parameter — the framework inspects them to build the tool schema sent to the model.
7. Access session state via `tool_context.state["key"]` for reading and writing.
8. To escalate (exit a legacy LoopAgent early): `tool_context.actions.escalate = True`. In a `Workflow`, exit a cycle through a routed edge instead (see `adk-workflow-graphs`).
9. To skip LLM summarisation of the tool result: `tool_context.actions.skip_summarization = True`.
10. Wrap the function: `my_tool_instance = FunctionTool(func=my_tool)`.
11. For long-running work, check `tool_context.is_aborted` between steps and return early — it turns `True` when the run is cancelled (`abort_signal`, or the HTTP client disconnecting from `/run_sse`).

## Quick-Start Template

```python
from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext


def do_something(query: str, tool_context: ToolContext) -> dict:
    """One-line summary of what this tool does.

    Args:
        query: Description of the query parameter.

    Returns:
        A dict with status and result data.
    """
    try:
        # ... implementation ...
        result = {"answer": "42"}
        return {"status": "success", "data": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


do_something_tool = FunctionTool(func=do_something)
```

## Parameter Guidelines

- Use `str`, `int`, `float`, `bool`, `list[str]`, `dict[str, Any]` — keep types JSON-serialisable.
- `tool_context` is **always the last parameter** and is injected by the framework (never supplied by the model).
- Optional parameters: use `param: str = "default"`. The model can omit them.
- Avoid `*args` / `**kwargs` — the framework cannot generate a schema for them.

## Return Format Convention

```python
# Success
{"status": "success", "message": "Created item #42", "data": {...}}

# Error
{"status": "error", "error": "Item not found"}

# Partial / warning
{"status": "warning", "message": "Created but with issues", "warnings": [...]}
```

## Registering Tools on an Agent

```python
from google.adk.agents import LlmAgent

agent = LlmAgent(
    name="my_agent",
    model="gemini-2.0-flash",
    instruction="You are a helpful assistant.",
    tools=[do_something_tool],
)
```

## A tool call may be denied before your function ever runs

In a generated agent, a tool doesn't execute unconditionally just because
the model called it. Three independent gates can intercept the call first:

- A command-safety classifier (`command_safety.classify`) can return
  `deny`/`ask` for a shell-executing tool's argv — but only if your own
  tool guard calls it; it's a library function, not something the scaffold
  wires in automatically.
- `exfil_guard`, a `before_tool_callback` wired unconditionally into every
  generated agent, can block a call whose arguments carry a secret-shaped
  value before the tool body runs.
- During a cron run, the headless approval policy (`CronIsolationPlugin`)
  can auto-deny a non-shell tool outright, since no human is present to
  approve it.

Design tools to expect this: surface a denial as a structured, non-fatal
result (e.g. `{"status": "error", "error": "..."}`, matching the Return
Format Convention above) rather than assuming the function body always
runs — the model should see a normal tool-error response it can react to,
not a crash. Load `adk-long-horizon-guardrails` for the command-safety
classifier and `exfil_guard`, and `adk-cron-isolation` for the headless
denial policy.

## Consulting a stronger model mid-task (`ModelConsultTool`)

Since `google-adk` 2.11, an agent on a fast, cheap model can escalate the hard
step of a turn to a stronger "advisor" model instead of running every turn on
the expensive one. Add `ModelConsultTool` next to your own tools; the model
gets a `model_consult(question=..., context=...)` tool plus an escalation
policy in its system instruction.

```python
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import ModelConsultTool

agent = LlmAgent(
    name="support_executor",
    model=LiteLlm(model="openrouter/google/gemini-2.5-flash"),
    instruction="...",
    tools=[
        lookup_order_tool,
        ModelConsultTool(
            # Always pass the advisor: the default is a Gemini model name,
            # which a generated agent (OpenRouter via LiteLlm) can't resolve.
            model=LiteLlm(model="openrouter/anthropic/claude-opus-4"),
            max_uses=2,          # consults per turn
            session_max_uses=5,  # consults per session
        ),
    ],
)
```

- The advisor reads the session (instructions, tool calls and results) with
  **tool calling disabled** — it returns guidance only; the executor still
  makes every tool call, so tool guards and approval gates still apply.
- It never raises into the agent loop: it returns `status` `"ok"`
  (with `guidance`), `"limit_reached"`, `"error"`, or `"invalid_request"`.
- The default policy makes the executor consult before committing to a
  decision, when stuck, and before declaring a task done, so most turns use
  one consult. Override it with `executor_instruction=`.
- **Advisor spend is invisible to `CostGuardPlugin`.** The advisor call
  bypasses the model callbacks, so the generated agent's per-session
  `COST_GUARD_BUDGET` never counts it. Bound it with `max_uses` /
  `session_max_uses`, and add ADK's `ModelConsultObservabilityPlugin`
  (`google.adk.tools.model_consult`, register it first) if you need the
  advisor's tokens and cost reported.
- A weak executor may skip consulting. `ForceEscalationPlugin`
  (`google.adk.tools.model_consult`) forces a consult on the first model turn
  and on repeated identical calls; add it to the App's plugins only if evals
  show the executor under-consults.

## References

- Load `tool-patterns` for complete CRUD, API wrapper, file-ops, and search tool implementations.
- Load `tool-context-api` for the full ToolContext API reference (state, actions, artifacts).
- Load `async-tool-examples` for async tool patterns with `aiohttp`, `asyncpg`, etc.
