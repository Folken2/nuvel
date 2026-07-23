---
name: adk-task-delegation
description: >
  Delegate work to sub-agents with the ADK 2.0 Task API — `mode='task'`,
  `mode='single_turn'`, `mode='chat'` on `LlmAgent`, the auto-attached
  `finish_task` tool, and typed contracts via `input_schema` / `output_schema`.
  Load this skill when one agent needs to hand a bounded unit of work to
  another and get a validated result back, or when migrating off
  SequentialAgent / ParallelAgent / LoopAgent.
---

# ADK 2.0 Task Delegation

**Version 1.0** | Requires `google-adk>=2.0.0`

In ADK 2.0 an `LlmAgent` declares *how it is reachable* through its `mode`
field. The framework reads that field on the sub-agents of a parent (and on
`LlmAgent` nodes inside a `Workflow`) and wires the right delegation machinery
automatically — no `AgentTool`, no second `Runner`, no manual plumbing.

For the graph primitive these agents plug into, load `adk-workflow-graphs`.
For choosing the top-level shape of an agent, load `adk-agent-patterns`.

## The three modes

```python
mode: Literal['chat', 'task', 'single_turn'] | None
```

| Mode | Talks to the user? | Reached via | Finishes when |
|---|---|---|---|
| `chat` | yes | `transfer_to_agent` — control moves to it | it transfers back / the turn ends |
| `task` | yes (can ask for clarification) | a delegation tool call from the parent | it calls `finish_task` |
| `single_turn` | no | a normal tool call from the parent | its first response completes |

**Defaults (do not set `mode` unless you want to override):**

- as a sub-agent of an `LlmAgent` → `chat`
- as a node inside a `Workflow` → `single_turn`

Only `task`, `single_turn`, and `chat` are legal for an `LlmAgent` used as a
workflow node; anything else raises at graph-build time.

## Picking a mode

```
Does the sub-agent need to come back to the user mid-work
(clarifying questions, approvals, several turns)?
  YES → mode='task'
  NO  ↓

Is it one bounded transform — input in, structured result out?
  YES → mode='single_turn'
  NO  ↓

Should the user effectively be handed over to it for a while
(a specialist that owns the conversation)?
  YES → mode='chat'
```

Rule of thumb: **`single_turn` is the default you want** for pipeline steps.
Reach for `task` only when the sub-agent legitimately needs multiple turns or
user input. Use `chat` for router/specialist hierarchies where the user should
notice they are now talking to the specialist.

## What each mode wires up

Set on the **sub-agent**; the **parent** does the wiring in `model_post_init`:

```python
from google.adk.agents import LlmAgent

researcher = LlmAgent(
    model=FAST_MODEL,
    name="researcher",
    description="Researches a topic and returns sourced findings.",
    instruction="...",
    mode="task",                 # ← declared here
    output_schema=Findings,      # ← typed contract
)

coordinator = LlmAgent(
    model=FAST_MODEL,
    name="coordinator",
    instruction="Delegate research to the researcher, then summarize.",
    sub_agents=[researcher],     # ← parent picks up mode automatically
)
```

- `mode='task'` on `researcher` → the parent gets a delegation tool named
  `researcher`; calling it runs the sub-agent **inline in the parent's
  session** via `ctx.run_node()`.
- `mode='task'` also causes `FinishTaskTool` to be appended to
  `researcher.tools` — the sub-agent gets a `finish_task` tool it did not
  declare.
- `mode='single_turn'` → the parent gets a plain tool that runs the sub-agent
  once and returns its output.
- `mode='chat'` → no tool; the sub-agent stays a `transfer_to_agent` target.

**Do not wrap sub-agents in `AgentTool` for this.** `AgentTool` spins up a
separate runner and an isolated session; the mode-based path keeps everything
in the parent's session, so events, artifacts, state, and plugins all flow
through one place. `AgentTool` is explicitly discouraged in ADK 2.0.

## `finish_task`

Auto-attached to every `mode='task'` agent. Its parameters are generated from
the agent's `output_schema`; with no `output_schema` it falls back to a single
required `result: str`.

The framework also injects an instruction telling the model not to call it
prematurely, and to call it alone with no accompanying text.

```python
class Findings(BaseModel):
    summary: str
    sources: list[str]
    confidence: float

researcher = LlmAgent(..., mode="task", output_schema=Findings)
# → finish_task(summary=..., sources=[...], confidence=...)
```

If the model's arguments fail validation, `finish_task` returns an error
string describing the `ValidationError` instead of completing — the model sees
it as a tool result and retries. The task is only finished on a *successful*
call. Validation is therefore the enforcement point for the contract: a task
agent cannot end without producing schema-valid output.

## Typed contracts

| Field | On | Shapes |
|---|---|---|
| `output_schema` | task / single_turn sub-agent | `finish_task` params; the value handed back to the parent |
| `input_schema` | task / single_turn sub-agent | the parameters of the delegation tool the parent calls |

With no `input_schema`, the defaults are `{goal, background}` for `task` mode
and a single `request: str` for `single_turn`. Declaring your own is how you
stop the parent from passing vague prose:

```python
class ResearchRequest(BaseModel):
    topic: str
    depth: Literal["shallow", "deep"]
    must_cover: list[str] = []

researcher = LlmAgent(
    ..., mode="task", input_schema=ResearchRequest, output_schema=Findings,
)
```

Both schemas are ordinary Pydantic models, so the contract is unit-testable
without running a model.

## Task agents in a Workflow

An `LlmAgent` used as a graph node defaults to `single_turn`; set
`mode='task'` when the node must be able to come back to the user.
`single_turn` nodes also get `include_contents='none'` by default — they see
only their `node_input`, not the conversation history. Set `include_contents`
explicitly to override.

```python
from google.adk.workflow import Workflow

root_agent = Workflow(
    name="research_pipeline",
    edges=[
        ("START", planner),        # LlmAgent, mode='single_turn', output_schema=Plan
        (planner, researcher),     # LlmAgent, mode='task',        output_schema=Findings
        (researcher, writer),      # LlmAgent, mode='single_turn'
    ],
)
```

A node's output — the validated `output_schema` value for a task node —
becomes the next node's `node_input`.

## Migrating off Sequential / Parallel / Loop

`SequentialAgent`, `ParallelAgent`, and `LoopAgent` still exist as
convenience wrappers but are no longer the idiomatic way to compose steps.
Their replacement is a `Workflow` whose nodes are mode-declaring `LlmAgent`s.
See `references/workflow-migration.md` for the three mechanical rewrites.

## References

| Resource | Load when |
|----------|-----------|
| `references/task-mode-examples.md` | You need full runnable code — coordinator + task sub-agent, workflow with task nodes, `finish_task` retry behavior, testing contracts |
| `references/workflow-migration.md` | Rewriting a `SequentialAgent` / `ParallelAgent` / `LoopAgent` into a `Workflow` + task modes |

Load a reference with
`load_skill_resource("adk-task-delegation", "<resource>.md")`.

## Common mistakes

| Mistake | Fix |
|---|---|
| Wrapping a sub-agent in `AgentTool` to call it | Set `mode='single_turn'` (or `'task'`) and put it in `sub_agents=[...]` |
| Adding a `finish_task` tool by hand | It's auto-attached to `mode='task'` agents; declaring your own shadows it |
| `mode='task'` for a one-shot transform | Use `single_turn` — `task` costs extra turns and can stall waiting on the user |
| Expecting a task agent to end after one reply | It runs until it calls `finish_task`; give it an `output_schema` so "done" is well-defined |
| Relying on prose to constrain sub-agent output | Put the contract in `output_schema` — `finish_task` validates it |
| A `single_turn` node that needs conversation history | Set `include_contents='default'` explicitly, or make it `mode='task'` |
| Calling the delegation tool in parallel with other tools | The declaration says not to; run it alone |
| A routed edge that maps both a named route and `__DEFAULT__` to the same node | Graph validation rejects duplicate edges between a pair of nodes — pick one key |
