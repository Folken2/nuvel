---
name: adk-workflow-graphs
description: >
  Build graph-based agents with ADK 2.0 `Workflow` — declare nodes and edges,
  route conditionally, fan-out/fan-in in parallel, run dynamic nodes at runtime,
  route on a typed classifier answer, gate nodes behind human confirmation,
  and add human-in-the-loop revision cycles. Load this skill whenever the
  agent needs more than one step.
---

# ADK 2.0 Workflow Graphs

**Version 1.1** | Requires `google-adk>=2.13.0`

ADK 2.0 introduced **`google.adk.workflow.Workflow`** — a graph-based
orchestration primitive that replaces the now-deprecated `SequentialAgent`,
`LoopAgent`, and `ParallelAgent` with one model. Nodes are units of work (functions, `LlmAgent`s,
or other `BaseNode`s); edges declare how output flows and how control routes.

This skill is the API reference. For *when to choose* a Workflow vs. a single
`LlmAgent`, and how to hang a Workflow under a coordinator LLM (as a tool, not
a sub-agent), see `adk-agent-patterns`.

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
| classifier-routing | `ClassifierNode` with `Choice`/`Binary` questions, `LlmClassifier` / `TypesafeClassifier`, `threshold`, `answer_key`, build-time route checks | The branch is a judgment about the input (triage, urgency, intent) — replaces an LLM-labels-then-function-routes pair |
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

# 9. Route on a typed classifier answer (experimental, 2.13+)
from google.adk.labs.classifiers import Choice, ClassifierNode, LlmClassifier
triage = ClassifierNode(
    name="triage",
    classifier=LlmClassifier(model=llm),
    question=Choice(criteria={"billing": "Charges, refunds", "technical": "Bugs, outages"}),
)
edges = [('START', triage), (triage, {"billing": billing, "technical": tech})]

# 10. Ask the user before a side-effecting node runs (2.12+)
@node(require_confirmation=True)  # or a predicate over the node's args:
                                  # lambda node_input: node_input["amount"] > 100
def issue_refund(node_input: dict) -> dict:
    ...
```

## Confirmation gates and stopping a run

**`require_confirmation`** (2.12+) on `@node(...)` / `node(...)` pauses the
workflow and asks the user to approve before the node runs — the same
`RequestInput` pause an `LlmAgent` uses for tool confirmation. Pass `True`, or
a predicate that receives the node's own arguments by name (plus `node_input`
and `ctx` if it declares them) to confirm only some inputs.
It turns on `rerun_on_resume=True` for you. Since 2.11, tool nodes whose tool
asks for confirmation pause the same way instead of passing an error
downstream. For the approval UX on the client side, see `adk-callbacks-hitl`.

**`abort_signal`** (2.11+): pass an `asyncio.Event` to
`runner.run_async(..., abort_signal=sig)` and `sig.set()` to stop a run
cleanly. The `Workflow` cancels its in-flight node tasks, dangling tool calls
get synthetic responses so the session stays valid for the next turn, and the
stream ends with an `INVOCATION_ABORTED` event instead of raising. Long tools
and nodes can poll `ctx.is_aborted` to stop early. ADK's own `/run` and
`/run_sse` (what a generated agent's `run_adk.py` serves via
`get_fast_api_app`) already trip it when the HTTP client disconnects.

## Migration from the deprecated shortcut classes

`SequentialAgent`, `LoopAgent`, and `ParallelAgent` still run, but they are
deprecated in favor of `Workflow` (a `DeprecationWarning` on construction) and
will be removed in a future release. Write new pipelines as a `Workflow`, even
trivially linear ones, and migrate existing ones — the step-by-step rewrite lives
in the `workflow-migration.md` reference in `adk-task-delegation`.

One thing a `Workflow` can't do yet: be an `LlmAgent` **sub-agent**
(`sub_agents=[workflow]` fails validation). Give it to the coordinator as a
**tool** instead — `LlmAgent(tools=[workflow])` wraps it in a `NodeTool`.

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
