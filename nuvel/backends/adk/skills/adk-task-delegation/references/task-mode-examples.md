# Task Mode — Worked Examples

Runnable shapes for the ADK 2.0 Task API. Every example assumes:

```python
from typing import Literal

from google.adk.agents import LlmAgent
from google.adk.workflow import Workflow
from pydantic import BaseModel, Field
```

---

## 1. Coordinator delegating to a task sub-agent

The parent stays in `chat` mode (the user talks to it). The sub-agent is
`mode='task'`, so the parent receives a delegation tool named after it.

```python
class ResearchRequest(BaseModel):
    topic: str = Field(description="What to research.")
    depth: Literal["shallow", "deep"] = "shallow"
    must_cover: list[str] = Field(default_factory=list)


class Findings(BaseModel):
    summary: str
    sources: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


researcher = LlmAgent(
    model=FAST_MODEL,
    name="researcher",
    description="Researches a topic and returns sourced findings.",
    instruction=(
        "Research the requested topic using your tools. Cover every item in "
        "must_cover. If the topic is ambiguous, ask the user before working. "
        "When done, call finish_task with the findings."
    ),
    mode="task",
    input_schema=ResearchRequest,
    output_schema=Findings,
    tools=[web_search],
)

coordinator = LlmAgent(
    model=FAST_MODEL,
    name="coordinator",
    description="Fields user requests and delegates research.",
    instruction=(
        "When the user asks a research question, delegate it to the "
        "researcher, then present the findings in plain language with the "
        "sources listed."
    ),
    sub_agents=[researcher],
)

root_agent = coordinator
```

What the framework does at construction time:

1. `researcher` gets a `finish_task` tool appended to its `tools`, with
   parameters generated from `Findings`.
2. `coordinator` gets a delegation tool named `researcher`, with parameters
   generated from `ResearchRequest`.
3. Calling that tool runs `researcher` **inline in the coordinator's
   session** — same events stream, same artifacts, same plugins.

Without `input_schema`, the delegation tool's parameters default to
`{goal, background}`. Without `output_schema`, `finish_task` takes a single
required `result: str`.

---

## 2. `finish_task` and the validation loop

`finish_task` is not a formality — it is where the contract is enforced.

```python
class Ticket(BaseModel):
    title: str
    severity: Literal["low", "medium", "high"]
    steps_to_reproduce: list[str]


triager = LlmAgent(
    model=FAST_MODEL,
    name="triager",
    instruction="Turn the user's bug report into a ticket.",
    mode="task",
    output_schema=Ticket,
)
```

The model calls:

```
finish_task(title="Login fails", severity="critical", steps_to_reproduce=[...])
```

`severity="critical"` isn't in the literal, so the tool returns an error
result quoting the `ValidationError` and instructing the model to retry with
correct types. The task does **not** finish. The next call with
`severity="high"` succeeds, and the validated `Ticket` becomes the task's
output.

Consequences worth designing around:

- A task agent can never terminate with off-schema output.
- A too-strict schema turns into a retry loop. Keep constraints to what you
  can actually explain in the instruction.
- Don't declare your own tool named `finish_task` — it collides with the
  auto-attached one.

---

## 3. `single_turn` for bounded transforms

Most pipeline steps don't need to talk to the user. `single_turn` runs the
sub-agent once and returns its output as the tool result.

```python
class Summary(BaseModel):
    headline: str
    bullets: list[str]


summarizer = LlmAgent(
    model=FAST_MODEL,
    name="summarizer",
    description="Compresses a document into a headline plus bullets.",
    instruction="Summarize the provided text. Be terse.",
    mode="single_turn",
    output_schema=Summary,
)

assistant = LlmAgent(
    model=FAST_MODEL,
    name="assistant",
    instruction="Use the summarizer whenever the user hands you a long document.",
    sub_agents=[summarizer],
)
```

With no `input_schema`, the tool takes a single `request: str`.

`single_turn` agents used as workflow nodes get `include_contents='none'` by
default: they see their `node_input` and nothing else. That is usually what
you want — it keeps the step deterministic and cheap. Override explicitly if
the step genuinely needs history:

```python
summarizer = LlmAgent(..., mode="single_turn", include_contents="default")
```

---

## 4. Task nodes inside a Workflow

An `LlmAgent` node in a `Workflow` defaults to `single_turn`. Declare
`mode='task'` on the nodes that must be able to come back to the user.

```python
class Plan(BaseModel):
    steps: list[str]
    needs_approval: bool


class Outcome(BaseModel):
    completed: list[str]
    notes: str


planner = LlmAgent(
    model=REASONING_MODEL,
    name="planner",
    description="Turns a request into an ordered plan.",
    instruction="Break the request into concrete steps.",
    mode="single_turn",
    output_schema=Plan,
)

executor = LlmAgent(
    model=FAST_MODEL,
    name="executor",
    description="Executes a plan, asking the user when a step is ambiguous.",
    instruction=(
        "Work through the plan's steps in order. Ask the user before anything "
        "destructive. Call finish_task when every step is done or explicitly "
        "skipped."
    ),
    mode="task",
    output_schema=Outcome,
    tools=[...],
)

root_agent = Workflow(
    name="plan_and_execute",
    edges=[
        ("START", planner),
        (planner, executor),
    ],
)
```

`planner`'s validated `Plan` becomes `executor`'s `node_input`.
`executor`'s validated `Outcome` is the workflow's output.

---

## 5. Routing on a task result

Route strings must be deterministic, so translate the typed output in a small
function node rather than routing on LLM prose.

```python
from google.adk import Event
from google.adk.workflow import node


@node
def gate(node_input: Plan):
    route = "approve" if node_input.needs_approval else "run"
    return Event(output=node_input, route=route)


root_agent = Workflow(
    name="gated_execution",
    edges=[
        ("START", planner),
        (planner, gate),
        (gate, {
            "approve": approver,      # LlmAgent, mode='task' — asks the user
            "__DEFAULT__": executor,  # the fallthrough *is* the run branch
        }),
        ((approver, executor), reporter),   # fan-in
    ],
)
```

See `adk-workflow-graphs` → `routing-and-conditions.md` for the full routing
rules (cycles must contain at least one routed edge, `__DEFAULT__` handling,
fan-in semantics).

---

## 6. Testing the contracts

The schemas are plain Pydantic, so the expensive half of a delegation is
testable without a model:

```python
import pytest
from pydantic import ValidationError


def test_findings_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        Findings(summary="x", sources=[], confidence=1.5)


def test_researcher_is_wired_for_task_mode():
    assert researcher.mode == "task"
    assert researcher.output_schema is Findings
    # finish_task is appended at construction time
    assert any(getattr(t, "name", None) == "finish_task" for t in researcher.tools)


def test_coordinator_sees_the_delegate():
    assert any(getattr(t, "name", None) == "researcher" for t in coordinator.tools)
```

For end-to-end runs, drive the parent through a `Runner` and assert on the
event stream — the sub-agent's events appear in the *parent's* session, since
task and single_turn sub-agents run inline rather than in their own runner.
