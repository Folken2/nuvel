# Parallel Execution and Fan-out / Fan-in

`Workflow` expresses parallelism through tuple syntax in edges. ADK 2.0 also
keeps the older `ParallelAgent` class as a one-line shortcut, but the graph
form is more flexible (it can join the results).

## Fan-out — one source, many targets

Use a tuple on the **target** side:

```python
edges = [
    ('START', (analyze_text, translate_text, summarize_text)),
]
```

All three nodes start with the same upstream input and run concurrently. The
runner schedules them as parallel `asyncio.Task`s.

## Fan-in — many sources, one target

Use a tuple on the **source** side. Pair with `JoinNode` when you need a
single object combining all branch outputs:

```python
from google.adk.workflow import JoinNode

edges = [
    ('START', (analyze_text, translate_text, summarize_text)),
    ((analyze_text, translate_text, summarize_text), JoinNode(name="join")),
    (JoinNode, final_processor),
]
```

`JoinNode` waits for all upstream branches to complete, then emits a single
combined result (typically a dict or list) to the downstream node.

## Diamond pattern (fan-out then fan-in)

The classic shape:

```python
agent = Workflow(
    name="diamond",
    edges=[
        ('START', preprocess),
        (preprocess, (analyze, translate, summarize)),
        ((analyze, translate, summarize), join),
        (join, finalize),
    ],
)
```

This is exactly the shape `ParallelAgent` cannot do alone, because
`ParallelAgent` has no join step.

## `ParallelAgent` shortcut

Still valid in 2.0 for the common no-join case:

```python
from google.adk.agents import ParallelAgent

parallel = ParallelAgent(
    name="concurrent",
    sub_agents=[analyzer, translator, summarizer],
)
# Equivalent: Workflow(edges=[('START', (analyzer, translator, summarizer))])
```

Reach for `Workflow` the moment you need to aggregate the outputs.

## State writes in parallel branches

Each branch runs concurrently and may write to `ctx.state`. Two rules:

1. **Unique keys per branch.** Don't have two parallel branches write to the
   same `ctx.state` key — last-writer-wins is non-deterministic.
2. **Prefer edge-flow over state.** Pass each branch's output along the edge
   into the join node; let the join produce a single merged value. `ctx.state`
   is for cross-cutting values, not parallel result aggregation.

## Concurrency limits

The runner does not throttle by default — N parallel LLM agents launch N
parallel API calls. If you need a cap, gate calls inside the LLM agents'
tools, or use a semaphore in a function node that batches work.

## Common mistakes

| Mistake | Fix |
|---|---|
| Using `ParallelAgent` then needing to aggregate | Switch to `Workflow` with fan-out + `JoinNode` |
| Parallel branches writing the same state key | Give each branch a unique key or fan into a join |
| Wrapping branches in `asyncio.create_task()` manually | Just put them in a tuple — the runner schedules them in parallel |
| Forgetting that a tuple in `source` position means fan-in | A tuple in `target` = fan-out; a tuple in `source` = fan-in. Two different shapes. |
