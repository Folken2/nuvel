# Dynamic Nodes

Static edges declare the graph at construction time. **Dynamic nodes** let a
running node decide — at runtime — which child node(s) to run next, using
ordinary Python control flow. Use them when the set of next steps depends on
data you only have once execution starts.

## The API: `ctx.run_node()`

Inside a node, call:

```python
result = await ctx.run_node(
    node_like,            # a function, LlmAgent, or BaseNode
    node_input=...,       # optional, defaults to None
    name="explicit-name", # optional, recommended for replay determinism
    use_as_output=True,   # optional, mark this as the parent's output
)
```

`ctx.run_node()` schedules the child and `await`s its result. The child shows
up in traces under the parent's path.

## When to use dynamic nodes

- **Data-driven branching.** "If the input has N records, fan out to N
  per-record analyzers, then aggregate."
- **Imperative loops.** "Keep refining until score > 0.9 — but the number of
  iterations isn't known up front and the body changes between iterations."
- **Recursive structure.** Tree-walks, hierarchical summarization, agentic
  planners that expand their own subgoals.
- **Conditional pipelines that don't fit dict-routing.** When you need full
  `if/else`/`try`/`for` semantics rather than a fixed route table.

If you can express it with edges + dict routing + cycles, **prefer static
edges** — they're auditable and the graph is visible up front. Reach for
dynamic nodes only when the shape itself is data-dependent.

## Minimal example

```python
from google.adk.workflow import node

@node(rerun_on_resume=True)
async def parent(ctx, node_input: str):
    result = await ctx.run_node(child_agent, node_input=node_input)
    return result
```

## The `rerun_on_resume=True` rule

**Any node that calls `ctx.run_node()` must declare `rerun_on_resume=True`.**

Why: dynamic children can be interrupted (human-in-the-loop pauses, errors,
deliberate stops). When the workflow resumes, the parent has to re-execute so
the child's eventual result can be wired back in. Without this flag, resume
silently drops the dynamic part of the parent's execution.

```python
@node(rerun_on_resume=True)          # required
async def expand(ctx, node_input: list):
    results = []
    for item in node_input:
        # deterministic names so replays match the original execution
        r = await ctx.run_node(per_item_analyzer, node_input=item, name=f"item-{item['id']}")
        results.append(r)
    return results
```

## Deterministic names

Pass `name=` to `ctx.run_node()` so replays match the original execution. If
you spawn N children in a loop, name them `f"item-{i}"` or by a stable id from
the input — not by a timestamp or random uuid. Non-deterministic names break
resume/replay because the runner can't match the new attempt's children to the
recorded ones.

## What you must not do inside a node

- **Don't wrap in `asyncio.create_task()`** — always `await` directly. The
  scheduler owns concurrency; manual tasks bypass replay.
- **Don't set `ctx.event_author` manually** — authorship is managed by the
  runner.
- **Don't catch and swallow `ctx.run_node()` errors silently** — let them
  propagate, or convert them into an explicit error route.

## Combining static and dynamic

A workflow can mix both. Static edges express the overall shape; dynamic nodes
expand a single position into a data-dependent fan-out:

```python
agent = Workflow(
    edges=[
        ('START', fetch_records),     # static: deterministic step
        (fetch_records, expand),      # static: hands off to dynamic parent
        (expand, aggregate),          # static: aggregates dynamic children's results
    ],
)
```

The `expand` node above is the dynamic one — it spawns one child per record at
runtime and returns the list of results to `aggregate`.

## Common mistakes

| Mistake | Fix |
|---|---|
| Forgetting `rerun_on_resume=True` on a parent calling `ctx.run_node()` | Always set it on dynamic-parent nodes |
| Naming dynamic children with timestamps or random uuids | Use stable, data-derived names so replay matches |
| Wrapping `ctx.run_node()` in `asyncio.create_task()` | Just `await` — the runner schedules parallelism |
| Using a dynamic node when static edges + routing would do | Static is auditable, visualizable, and resume-cheap — prefer it |
