---
name: adk-agent-patterns
description: >
  Agent architecture patterns for Google ADK 2.0 — when to reach for a single
  LlmAgent, a Workflow graph (new default for multi-step orchestration), or the
  shortcut classes SequentialAgent / LoopAgent / ParallelAgent. Load this skill
  when deciding the agent's top-level shape.
---

# ADK Agent Architecture Patterns

**Version 2.0** | Updated for `google-adk>=2.0.0`

Select the right architecture for any task using Google ADK primitives. ADK 2.0
introduced a graph-based **`Workflow`** model that subsumes Sequential / Loop /
Parallel into a single primitive. The old classes still exist as convenience
wrappers, but `Workflow` is the new idiomatic default for anything multi-step.

For the full graph API (nodes, edges, routing, fan-out, dynamic nodes,
human-in-the-loop), load the **`adk-workflow-graphs`** skill.

## Decision Tree

```
Is the task a single, well-defined purpose with clear tools?
  YES → LlmAgent (single agent)              [Pattern 1]
  NO  ↓

Is the task multi-step with branching, cycles, or fan-out?
  YES → Workflow graph                       [Pattern 2 — preferred]
  NO  ↓  (i.e., strictly linear, no branching)

Strictly fixed-order pipeline, ≤ ~3 steps, no routing?
  YES → SequentialAgent shortcut             [Pattern 3a]

Iterate-until-good-enough with a clear exit signal?
  YES → LoopAgent shortcut                   [Pattern 3b]

Independent tasks to run concurrently, no routing between them?
  YES → ParallelAgent shortcut               [Pattern 3c]

Specialized sub-agents picked dynamically by a router LLM?
  YES → Multi-agent hierarchy                [Pattern 4]
```

**Rule of thumb in ADK 2.0:** if you need any of {conditional branching,
revision loops, fan-in joins, dynamic node scheduling, mixing LLM steps with
plain Python steps}, **use `Workflow`**. The shortcut classes are fine when the
shape is trivially linear or trivially parallel and you want a one-liner.

## Pattern 1: Single LlmAgent

**When:** One clear purpose, well-defined tools, no multi-step pipeline. Covers
~70% of real use cases.

```python
from google.adk.agents import LlmAgent

agent = LlmAgent(
    name="data_analyst",
    model="gemini-2.5-flash",
    instruction="You are a data analyst. Analyze datasets using the provided tools...",
    tools=[query_tool, chart_tool, export_tool],
    output_key="analysis_result",
)
```

**Use single LlmAgent when:** one job, all tools serve the same purpose, no
iterative refinement, single user → single agent conversation.

## Pattern 2: Workflow graph (ADK 2.0, new default for multi-step)

**When:** Anything multi-step that isn't trivially linear or trivially parallel.
The graph is declared as a list of `edges`; ADK compiles it, validates it, and
schedules execution.

```python
from google.adk.workflow import Workflow, node
from google.adk.agents import LlmAgent

classifier = LlmAgent(name="classifier", model="gemini-2.5-flash",
                     instruction="Classify intent: return 'sql' or 'chart'.")
sql_writer = LlmAgent(name="sql_writer", model="gemini-2.5-flash",
                     instruction="Write the SQL query.", tools=[execute_sql])
chart_maker = LlmAgent(name="chart_maker", model="gemini-2.5-flash",
                      instruction="Make the chart.", tools=[chart_tool])

@node
def format_answer(node_input: str) -> str:
    return f"Done: {node_input}"

agent = Workflow(
    name="analysis_router",
    edges=[
        ('START', classifier),
        (classifier, {
            "sql":   sql_writer,
            "chart": chart_maker,
        }),
        ((sql_writer, chart_maker), format_answer),
    ],
)
```

**What you get for free:**
- Conditional routing via dict syntax — `(node, {"a": A, "b": B})`
- Fan-out via tuple in target — `(start, (A, B, C))`
- Fan-in via tuple in source — `((A, B, C), join)`
- Cycles for revision loops — `(reviewer, {"revise": drafter, "approve": send})`
- Plain Python steps as nodes via `@node`
- `LlmAgent` auto-wraps to output `str` (or parsed dict if `output_schema` set)

**Use Workflow when:** branching, cycles, fan-out/in, mixing LLM and pure-Python
steps, anything where you'd otherwise nest Sequential inside Loop inside
something. For the full reference, load `adk-workflow-graphs`.

## Pattern 3: Convenience shortcut classes (still supported in 2.0)

These remain in ADK 2.0 as shortcuts; under the hood they compile to the same
graph machinery. Reach for them when the shape is trivially one of these three.

### 3a. SequentialAgent — strictly linear pipeline

```python
from google.adk.agents import SequentialAgent, LlmAgent

pipeline = SequentialAgent(
    name="analysis_pipeline",
    sub_agents=[planner, executor, summarizer],
)
```

Equivalent `Workflow`: `edges=[('START', planner), (planner, executor), (executor, summarizer)]`.

### 3b. LoopAgent — iterate until exit

```python
from google.adk.agents import LoopAgent

research_loop = LoopAgent(
    name="research_loop",
    sub_agents=[researcher, reviewer],
    max_iterations=10,
)
```

Exits via `tool_context.actions.escalate = True` or `max_iterations`. Always set
`max_iterations`. For loop exit patterns, see `references/loop-patterns.md`.

### 3c. ParallelAgent — fan-out only

```python
from google.adk.agents import ParallelAgent

parallel_research = ParallelAgent(
    name="parallel_research",
    sub_agents=[market_analyst, tech_analyst],
)
```

Equivalent `Workflow`: `edges=[('START', (market_analyst, tech_analyst))]`. If
you also need fan-in (an aggregator that consumes both outputs), switch to
`Workflow` — `ParallelAgent` alone has no join step.

## Pattern 4: Multi-agent hierarchy (LLM-routed sub_agents)

**When:** A coordinator LLM picks which specialist handles each request. The
choice is made by the model, not by static edges.

```python
from google.adk.agents import LlmAgent

coordinator = LlmAgent(
    name="coordinator",
    model="gemini-2.5-pro",
    instruction="Route user requests to the appropriate specialist.",
    sub_agents=[sql_expert, viz_expert],
)
```

**Hierarchy vs. Workflow routing:**
- **Hierarchy (`sub_agents=`)** — the LLM decides routing at runtime based on
  the user's message. Flexible but opaque.
- **Workflow dict-routing** — a node emits an explicit route string, edges map
  it to a target. Auditable and testable.

Use hierarchy when routing is genuinely conversational. Use Workflow when the
routing is more like a state machine you can write down. They compose: a
Workflow node can itself be an `LlmAgent` with `sub_agents`.

For `AgentTool` and advanced hierarchies, see `references/multi-agent-patterns.md`.

## Combining patterns

```
coordinator (LlmAgent with sub_agents)
  ├── simple_agent (LlmAgent)
  ├── analysis_pipeline (Workflow)
  │     ├── classifier (LlmAgent node)
  │     ├── sql_writer / chart_maker (routed branches)
  │     └── format_answer (FunctionNode)
  └── research_loop (LoopAgent or a Workflow cycle)
```

**Rules:**
1. Start simple. Single `LlmAgent` first; reach for `Workflow` when you actually
   need branching, cycles, or fan-in.
2. Prefer `Workflow` over nesting `SequentialAgent`/`LoopAgent`/`ParallelAgent`
   — the graph is flatter and the routing is explicit.
3. Every sub-agent has one clear job.
4. Pass state between nodes via node output (auto-wired) or `output_key` /
   session state for cross-cutting values.
5. Bound every cycle — `max_iterations` on `LoopAgent`, or a route that
   eventually leaves the cycle in a `Workflow`.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Reaching for SequentialAgent for any multi-step task | If there's any branching, fan-out, or cycle, use `Workflow` |
| Using ParallelAgent then needing to aggregate | `ParallelAgent` has no join — use `Workflow` with fan-out + fan-in |
| Nesting `LoopAgent` inside `SequentialAgent` inside `ParallelAgent` | Collapse to a single `Workflow` with routed cycles and tuple fan-out |
| LoopAgent or workflow cycle without an exit | Always set `max_iterations` or ensure at least one route leaves the cycle |
| Two parallel agents writing to the same `output_key` | Each parallel branch needs a unique key, or fan into a join node |
| Over-engineering with multi-agent when one LlmAgent works | Profile first — extra agents add latency and cost |
| Forgetting `rerun_on_resume=True` on a node that calls `ctx.run_node()` | Required for dynamic-node parents — see `adk-workflow-graphs` |
