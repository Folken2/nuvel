---
name: adk-workflow-graphs
description: >
  Build graph-based agents with ADK 2.0 `Workflow` — declare nodes and edges,
  route conditionally, fan-out/fan-in in parallel, run dynamic nodes at runtime,
  and add human-in-the-loop revision cycles. Load this skill whenever the agent
  needs anything beyond a strictly linear or trivially parallel pipeline.
---

# ADK 2.0 Workflow Graphs

**Version 1.0** | Requires `google-adk>=2.0.0`

ADK 2.0 introduced **`google.adk.workflow.Workflow`** — a graph-based
orchestration primitive that subsumes `SequentialAgent`, `LoopAgent`, and
`ParallelAgent` into one model. Nodes are units of work (functions, `LlmAgent`s,
or other `BaseNode`s); edges declare how output flows and how control routes.

This skill is the API reference. For *when to choose* a Workflow vs. a single
`LlmAgent` vs. one of the shortcut classes, see `adk-agent-patterns`.

## Minimal example

```python
from google.adk.workflow import Workflow

def greet(node_input: str) -> str:
    return f"Hello! You said: {node_input}"

root_agent = Workflow(
    name="my_workflow",
    edges=[
        ('START', greet),
    ],
)
```

Run with `adk run my_agent/` (CLI) or `adk web my_agent/` (web UI).

**The `'START'` sentinel** is the workflow's entry point. `('START', greet)`
means "when the workflow begins, pass the user input to `greet`."

## References

| Resource | Description | Load when |
|----------|-------------|-----------|
| function-nodes | `@node` decorator and `FunctionNode`; param resolution from `ctx.state`; async/generator nodes | A graph step is plain Python — transform, validate, branch logic |
| llm-agent-nodes | `_LlmAgentWrapper` behavior; `output_schema` for parsed dicts; tools-in-workflow; emitting routes from LLM output | A graph step is an `LlmAgent` and you need to compose its output cleanly |
| routing-and-conditions | `Event(route=...)`, dict-syntax edges, `__DEFAULT__` fallback, cyclical edges with validation rules | Branching on a node's result, revision loops, or a state-machine-shaped flow |
| parallel-and-fanout | Tuple syntax for fan-out/fan-in, `JoinNode`, diamond patterns, parallel-state-write rules | Running nodes concurrently — especially when you also need to aggregate results |
| dynamic-nodes | `ctx.run_node()` API, `rerun_on_resume=True`, deterministic naming for replay, mixing static + dynamic | The set of next steps depends on runtime data (data-driven fan-out, recursion) |

Load a reference with `load_skill_resource("adk-workflow-graphs", "<resource>.md")`.

## Core mental model

A `Workflow` is itself a `BaseNode`. Its body is a list of `edges`, where each
edge is a tuple `(source, target)` and either side can be:

- a single node (function, `LlmAgent`, `FunctionNode`, or nested `Workflow`)
- a tuple of nodes — fan-out (in `target`) or fan-in (in `source`)
- a dict mapping route strings to nodes — conditional routing (in `target`)
- the sentinel `'START'` — the entry point (in `source` only)

```python
edges = [
    ('START', classifier),                       # entry
    (classifier, {                               # routed branches
        "sql":   sql_writer,
        "chart": chart_maker,
        "__DEFAULT__": fallback,
    }),
    ((sql_writer, chart_maker, fallback), join), # fan-in
    (join, format_answer),                       # linear step
]
```

Output flows along edges automatically: a node's return value becomes the next
node's `node_input`. State that should be visible across the whole graph goes
through `ctx.state` (read in nodes, written by setting `ctx.state[key] = val`).

## Cheat sheet

```python
from google.adk.workflow import Workflow, node, FunctionNode, JoinNode
from google.adk import Event

# 1. Function node (decorator)
@node
def transform(node_input: str) -> str:
    return node_input.upper()

# 2. Function node (explicit, for naming or reuse)
clean = FunctionNode(lambda x: x.strip(), name="clean")

# 3. Routing — return Event(output=..., route=...)
@node
def classify(node_input: str):
    return Event(output=node_input, route="error" if "fail" in node_input else "ok")

# 4. Dict-syntax routing in edges
edges = [
    ('START', classify),
    (classify, {"ok": transform, "error": handle_error}),
]

# 5. Fan-out (tuple on target side)
edges = [('START', (analyze, translate, summarize))]

# 6. Fan-in (tuple on source side, often with JoinNode)
edges = [
    ((analyze, translate, summarize), JoinNode(name="join")),
    (JoinNode, final_step),
]

# 7. Cycle (revision loop) — must include at least one routed exit
edges = [
    ('START', drafter),
    (drafter, reviewer),
    (reviewer, {"revise": drafter, "approve": send}),
]

# 8. Dynamic child node from inside a parent
@node(rerun_on_resume=True)
async def parent(ctx, node_input: str):
    result = await ctx.run_node(child_agent, node_input=node_input)
    return result
```

## Migration from 1.x shortcut classes

`SequentialAgent`, `LoopAgent`, and `ParallelAgent` still work in 2.0 — they are
now thin wrappers that compile to the same graph machinery. Migrate to
`Workflow` when:

- You need to branch or route based on a step's result.
- You need to fan out *and* aggregate (`ParallelAgent` alone has no join).
- You're nesting two or more of the shortcut classes.
- You want a plain Python step (`FunctionNode`) inline with LLM steps.

Stay on the shortcut classes for trivially linear (3 steps, no routing) or
trivially parallel (no aggregation) cases — they're one line and equivalent.

## Critical rules

1. **`rerun_on_resume=True`** on any node that calls `ctx.run_node()`. Without
   it, an interruption (HITL pause, error retry) won't replay the parent and
   the dynamic child's result won't be wired back in.
2. **Cycles must have a routed edge.** `(reviewer, drafter)` alone is rejected
   at graph validation — there'd be no way out. Use a dict so at least one
   route exits the cycle.
3. **Use deterministic names for dynamic children** — pass `name=` to
   `ctx.run_node()` so replays match the original execution.
4. **Don't wrap with `asyncio.create_task()`** inside a node — always `await`
   directly. The scheduler handles concurrency; manual tasks break replay.
5. **Don't set `ctx.event_author` manually** — the runner manages authorship.

## Common mistakes

| Mistake | Fix |
|---------|-----|
| Forgetting the `'START'` sentinel | Every workflow needs `('START', first_node)` |
| Returning raw `types.Content` from an LLM node | In 2.0 the wrapper auto-extracts `str`; just `return` text or set `output_schema=` for dict |
| Unconditional cycle | Add a routed exit: `(reviewer, {"revise": drafter, "done": next})` |
| Manual `asyncio.create_task()` inside a node | Use `await ctx.run_node(...)` — the scheduler handles parallelism |
| Two parallel branches writing to the same `ctx.state` key | Give each branch a unique key, or fan into a `JoinNode` that merges them |
| Using `ctx.state` for per-step output passing | Output flows along edges automatically as `node_input`; reserve `ctx.state` for cross-cutting values |
