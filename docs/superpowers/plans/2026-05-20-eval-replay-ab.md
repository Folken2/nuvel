# Eval Replay A/B v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `nuvel eval replay` / `compare` / `variants` — replay a declarative YAML config variant against the `user_input` of historical traces, score each replay with the existing judge/rubric, and diff the aggregate against the `scored.jsonl` baseline.

**Architecture:** A new `nuvel/eval/replay/` subpackage layered strictly on top of the existing eval machinery. `schema.py` holds the wire formats (`Variant`, `ReplayResult`) plus their JSONL persistence. `variant.py` loads + discovers YAML variants. `runner.py` owns the litellm chat adapter, the per-trace `replay_run` (which builds a *synthetic* `Run` and reuses `score_run`), and the `ReplayRunner` batch driver (idempotency + cost budget + concurrency, mirroring `ScoreSession`). `compare.py` is pure functions diffing baseline `ScoredRun`s against variant `ReplayResult`s. The CLI orchestrates and renders. Nothing in the existing eval modules is modified except `cli.py` (new subcommands) and `report.py` (one render function).

**Tech Stack:** Python 3.11+, `dataclasses`, `pyyaml`, `litellm` (mocked in all unit tests via injection seams), `pytest` (async-auto mode — no `@pytest.mark.asyncio` needed), `argparse`.

---

## File Structure

- **Create** `nuvel/eval/replay/__init__.py` — package exports.
- **Create** `nuvel/eval/replay/schema.py` — `REPLAY_VERSION`, `Variant`, `ReplayResult` dataclasses + `to_json_line`/`from_json_line` + `append_replay`/`load_replay_index`. (Folding persistence in beside the wire format keeps the spec's 4-module layout; `ScoredRun` does the split into `writer.py`, but replay's writer is two tiny functions, so co-locating is the simpler single-responsibility choice. Documented deviation from the spec's module list.)
- **Create** `nuvel/eval/replay/variant.py` — `load_variant(path)`, `discover_variants(agent_filter)`.
- **Create** `nuvel/eval/replay/runner.py` — `_call_litellm_chat` adapter, `build_synthetic_run`, `replay_run`, `ReplayRunner`, `ReplayReport`.
- **Create** `nuvel/eval/replay/compare.py` — `AgentComparison`, `ComparisonReport`, `compare()`.
- **Modify** `nuvel/eval/report.py` — add `render_comparison(report)` and `render_variants(rows)`.
- **Modify** `nuvel/eval/cli.py` — register `variants`, `replay`, `compare` subcommands.
- **Create** tests: `tests/test_eval_replay_schema.py`, `tests/test_eval_replay_variant.py`, `tests/test_eval_replay_runner.py`, `tests/test_eval_replay_compare.py`, `tests/test_eval_replay_cli.py`, `tests/test_eval_replay_integration.py`.

Key real signatures this plan builds on (verified against the codebase):
- `Run` dataclass (`nuvel/traces_cli.py`): fields `agent, file, session_id, trace_id, ..., completion_tokens, user_input, schema="adk", events=[]`.
- `score_run(run, *, rubric=DEFAULT_RUBRIC, baseline=None, judge_fn=None, judge_disabled=False) -> ScoredRun` (async, `nuvel/eval/scorer.py:100`).
- `apply_heuristics` early-exits with `skip_judge=True` if no `run_end` event or `completion_tokens == 0` (`nuvel/eval/heuristics.py:113-130`) — the synthetic run must avoid both.
- `Rubric.resolved_model()` chain: `rubric → EVAL_JUDGE_MODEL → DEFAULT_FAST_MODEL` (`nuvel/eval/rubric.py:38-45`).
- `ScoredRun` (`nuvel/eval/schema.py:52`): `trace_id, agent, scored_at, scorer_version, rubric_version, overall, components, flags, judge, skipped_judge`; `to_json_line`/`from_json_line`.
- `judge._extract_cost(response, model)` and `judge._quiet_litellm_once()` are importable helpers (`nuvel/eval/judge.py:145,164`).
- `_iter_trace_files`, `_parse_file_runs`, `_agent_label_for`, `_parse_since` (`nuvel/traces_cli.py`).

---

## Task 1: Replay wire formats + persistence (`schema.py`)

**Files:**
- Create: `nuvel/eval/replay/__init__.py`
- Create: `nuvel/eval/replay/schema.py`
- Test: `tests/test_eval_replay_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_replay_schema.py
"""Wire-format round-trips and JSONL persistence for replay results."""
from __future__ import annotations

from pathlib import Path

from nuvel.eval.replay.schema import (
    REPLAY_VERSION,
    ReplayResult,
    Variant,
    append_replay,
    load_replay_index,
)


def _result(trace_id: str = "t1", version: str = "v-1.0") -> ReplayResult:
    return ReplayResult(
        trace_id=trace_id,
        agent="outlook-king",
        variant_name="friendlier-tone",
        variant_version=version,
        replayed_at="2026-05-21T12:00:00+00:00",
        model="openrouter/anthropic/claude-haiku-4.5",
        output_text="Hey! Happy to help.",
        replay_cost_usd=0.00041,
        scored={"trace_id": trace_id, "overall": 0.81, "components": {"quality": 0.9}},
    )


def test_replay_result_json_round_trip() -> None:
    r = _result()
    line = r.to_json_line()
    assert "\n" not in line
    back = ReplayResult.from_json_line(line)
    assert back == r


def test_variant_resolved_model_priority(monkeypatch) -> None:
    monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
    # explicit model wins
    assert Variant(version="v1", name="n", system_prompt="p", model="x/y").resolved_model() == "x/y"
    # env fallback
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "env/model")
    assert Variant(version="v1", name="n", system_prompt="p").resolved_model() == "env/model"
    # default fallback
    monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
    from nuvel._defaults import DEFAULT_FAST_MODEL
    assert Variant(version="v1", name="n", system_prompt="p").resolved_model() == DEFAULT_FAST_MODEL


def test_append_and_load_index_last_write_wins(tmp_path: Path) -> None:
    path = tmp_path / "friendlier-tone.jsonl"
    append_replay(path, _result(version="v-1.0"))
    append_replay(path, _result(version="v-2.0"))  # rescored same trace
    idx = load_replay_index(path)
    assert set(idx) == {"t1"}
    assert idx["t1"].variant_version == "v-2.0"  # last wins


def test_load_index_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_replay_index(tmp_path / "nope.jsonl") == {}


def test_replay_version_is_a_string() -> None:
    assert isinstance(REPLAY_VERSION, str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_replay_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nuvel.eval.replay'`.

- [ ] **Step 3: Create the package init**

```python
# nuvel/eval/replay/__init__.py
"""Eval replay A/B: replay config variants against historical traces.

Layered on top of the eval harness — reuses judge, rubric, scorer, and the
ScoredRun wire format wholesale. See
docs/superpowers/specs/2026-05-20-eval-replay-ab-v1-design.md.
"""
from nuvel.eval.replay.schema import (
    REPLAY_VERSION,
    ReplayResult,
    Variant,
    append_replay,
    load_replay_index,
)

__all__ = [
    "REPLAY_VERSION",
    "ReplayResult",
    "Variant",
    "append_replay",
    "load_replay_index",
]
```

- [ ] **Step 4: Write the schema module**

```python
# nuvel/eval/replay/schema.py
"""Wire formats for replay A/B + their append-only JSONL persistence.

``Variant`` is the declarative config-delta authored as YAML. ``ReplayResult``
is one replayed-and-scored trace, written one-per-line to
``traces/replays/<variant-name>.jsonl``. ``REPLAY_VERSION`` versions the replay
*machinery* (message assembly / synthetic-run shape); the variant's own
``version`` field is the idempotency key for rescoring.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nuvel._defaults import DEFAULT_FAST_MODEL

logger = logging.getLogger(__name__)

# Bump only when replay assembly/synthetic-run logic changes — NOT for variant tweaks.
REPLAY_VERSION = "1.0"


@dataclass
class Variant:
    """A config delta to A/B against the baseline. Loaded from YAML."""

    version: str          # idempotency key; bumping forces a rescore
    name: str
    system_prompt: str
    description: str = ""
    model: str | None = None
    temperature: float = 0.0
    max_tokens: int = 600

    def resolved_model(self) -> str:
        """Priority chain: variant.model → EVAL_JUDGE_MODEL → DEFAULT_FAST_MODEL.

        Mirrors ``Rubric.resolved_model`` so model selection is consistent
        across scoring and replay.
        """
        if self.model:
            return self.model
        env_model = os.getenv("EVAL_JUDGE_MODEL")
        if env_model:
            return env_model
        return DEFAULT_FAST_MODEL


@dataclass
class ReplayResult:
    """One replayed trace: the variant's output plus its ScoredRun blob."""

    trace_id: str
    agent: str
    variant_name: str
    variant_version: str
    replayed_at: str          # ISO 8601 UTC
    model: str
    output_text: str
    replay_cost_usd: float
    scored: dict[str, Any] = field(default_factory=dict)  # asdict(ScoredRun)

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json_line(cls, line: str) -> "ReplayResult":
        return cls(**json.loads(line))


def append_replay(path: Path, result: ReplayResult) -> None:
    """Append one ``ReplayResult`` as a single JSON line. Parent dir must exist."""
    with path.open("a", encoding="utf-8") as f:
        f.write(result.to_json_line() + "\n")


def load_replay_index(path: Path) -> dict[str, ReplayResult]:
    """Return ``{trace_id: latest ReplayResult}``. Last occurrence wins.

    Mirrors ``nuvel.eval.writer.load_scored_index`` — tolerant of malformed
    lines (warn + skip), returns empty dict for a missing file.
    """
    out: dict[str, ReplayResult] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("could not read %s: %s", path, exc)
        return out
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            result = ReplayResult.from_json_line(line)
        except Exception as exc:  # noqa: BLE001 — tolerant load
            logger.warning("skipping malformed line %s:%d: %s", path, i, exc)
            continue
        out[result.trace_id] = result
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_eval_replay_schema.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add nuvel/eval/replay/__init__.py nuvel/eval/replay/schema.py tests/test_eval_replay_schema.py
git commit -m "feat(eval): replay wire formats (Variant, ReplayResult) + JSONL persistence"
```

---

## Task 2: Variant YAML loader + discovery (`variant.py`)

**Files:**
- Create: `nuvel/eval/replay/variant.py`
- Test: `tests/test_eval_replay_variant.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_replay_variant.py
"""YAML loading + cross-agent discovery of variants."""
from __future__ import annotations

from pathlib import Path

import pytest

from nuvel.eval.replay.variant import discover_variants, load_variant

_GOOD = """\
version: friendlier-tone-1.0
name: friendlier-tone
description: warmer greeting prompt
system_prompt: |
  Hey! I'm your agent.
model: openrouter/anthropic/claude-haiku-4.5
temperature: 0.2
max_tokens: 500
"""


def test_load_variant_parses_all_fields(tmp_path: Path) -> None:
    p = tmp_path / "friendlier-tone.yaml"
    p.write_text(_GOOD, encoding="utf-8")
    v = load_variant(p)
    assert v.version == "friendlier-tone-1.0"
    assert v.name == "friendlier-tone"
    assert v.system_prompt.strip() == "Hey! I'm your agent."
    assert v.model == "openrouter/anthropic/claude-haiku-4.5"
    assert v.temperature == 0.2
    assert v.max_tokens == 500


def test_load_variant_minimal_uses_defaults(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text("version: v1\nname: m\nsystem_prompt: hi\n", encoding="utf-8")
    v = load_variant(p)
    assert v.model is None
    assert v.temperature == 0.0
    assert v.max_tokens == 600


@pytest.mark.parametrize("body,msg", [
    ("name: x\nsystem_prompt: p\n", "version"),
    ("version: v1\nsystem_prompt: p\n", "name"),
    ("version: v1\nname: x\n", "system_prompt"),
    ("just a string", "mapping"),
])
def test_load_variant_missing_required_fails_fast(tmp_path: Path, body: str, msg: str) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=msg):
        load_variant(p)


def test_load_variant_bad_yaml_fails_fast(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("version: v1\n  : : :\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML parse error"):
        load_variant(p)


def test_discover_variants_finds_and_filters(tmp_path: Path, monkeypatch) -> None:
    # Build generated-agents/<agent>/evals/variants/<name>.yaml for two agents.
    for agent in ("outlook-king", "ppt-king"):
        vdir = tmp_path / "generated-agents" / agent / "evals" / "variants"
        vdir.mkdir(parents=True)
        (vdir / "friendlier-tone.yaml").write_text(_GOOD, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    all_rows = discover_variants()
    assert {r.agent for r in all_rows} == {"outlook-king", "ppt-king"}
    # traces_dir is derived as the sibling of evals/, not evals/variants/
    row = next(r for r in all_rows if r.agent == "outlook-king")
    assert row.traces_dir == tmp_path / "generated-agents" / "outlook-king" / "traces"

    filtered = discover_variants(agent_filter="ppt")
    assert {r.agent for r in filtered} == {"ppt-king"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_replay_variant.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nuvel.eval.replay.variant'`.

- [ ] **Step 3: Write the loader + discovery**

```python
# nuvel/eval/replay/variant.py
"""Load + discover variant YAML files.

Variants live at ``generated-agents/<agent>/evals/variants/<name>.yaml`` —
the same ``evals/`` convention as ``rubric.yaml``. Loading fails fast on
missing required fields or malformed YAML so a typo never silently degrades
to a no-op replay.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from nuvel.eval.replay.schema import Variant


def load_variant(path: Path) -> Variant:
    """Parse one variant YAML. Raises ``ValueError`` on any problem."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: YAML parse error: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{path}: variant root must be a mapping, got {type(data).__name__}")

    for required in ("version", "name", "system_prompt"):
        if not data.get(required):
            raise ValueError(f"{path}: missing required field '{required}'")

    return Variant(
        version=str(data["version"]),
        name=str(data["name"]),
        system_prompt=str(data["system_prompt"]),
        description=str(data.get("description") or ""),
        model=data.get("model"),
        temperature=float(data.get("temperature", 0.0)),
        max_tokens=int(data.get("max_tokens", 600)),
    )


@dataclass
class DiscoveredVariant:
    """A variant plus the agent it targets and that agent's traces dir."""

    agent: str
    variant: Variant
    path: Path
    traces_dir: Path


def discover_variants(agent_filter: str | None = None) -> list[DiscoveredVariant]:
    """Scan ``generated-agents/*/evals/variants/*.yaml`` from the cwd.

    ``agent_filter`` is a case-insensitive substring match on the agent dir
    name. Malformed variant files are skipped silently here (listing must not
    crash on one bad file); ``replay`` re-loads the chosen variant and will
    surface the error then.
    """
    rows: list[DiscoveredVariant] = []
    gen = Path.cwd() / "generated-agents"
    if not gen.is_dir():
        return rows
    for agent_dir in sorted(gen.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent = agent_dir.name
        if agent_filter and agent_filter.lower() not in agent.lower():
            continue
        vdir = agent_dir / "evals" / "variants"
        if not vdir.is_dir():
            continue
        for yml in sorted(vdir.glob("*.yaml")):
            try:
                variant = load_variant(yml)
            except ValueError:
                continue
            rows.append(
                DiscoveredVariant(
                    agent=agent,
                    variant=variant,
                    path=yml,
                    traces_dir=agent_dir / "traces",
                )
            )
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_replay_variant.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add nuvel/eval/replay/variant.py tests/test_eval_replay_variant.py
git commit -m "feat(eval): variant YAML loader + cross-agent discovery"
```

---

## Task 3: Per-trace replay — synthetic run + score (`runner.py` part 1)

**Files:**
- Create: `nuvel/eval/replay/runner.py`
- Test: `tests/test_eval_replay_runner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_replay_runner.py
"""Per-trace replay: synthetic-run construction, scoring, retry, skips."""
from __future__ import annotations

from pathlib import Path

import pytest

from nuvel.eval.replay.runner import build_synthetic_run, replay_run
from nuvel.eval.replay.schema import Variant
from nuvel.eval.schema import JudgeResult
from nuvel.traces_cli import Run


def _src_run(user_input: str | None = "Summarize my inbox") -> Run:
    return Run(
        agent="outlook-king",
        file=Path("/tmp/outlook-king/traces/2026-05-20.jsonl"),
        session_id="s1",
        trace_id="t1",
        user_input=user_input,
    )


def _variant() -> Variant:
    return Variant(version="v-1.0", name="friendlier", system_prompt="Be warm.", model="m/x")


async def _fake_judge(run, rubric) -> JudgeResult:
    # Asserts the synthetic run reaches the judge with the replayed output.
    assert run.user_input == "Summarize my inbox"
    return JudgeResult(model="judge/x", success=1.0, quality=0.9, cost_usd=0.0002)


def test_build_synthetic_run_is_judgeable() -> None:
    """The synthetic run MUST avoid heuristics' skip_judge early-exit:
    it needs a run_end event AND completion_tokens > 0."""
    from nuvel.eval.heuristics import apply_heuristics
    run = build_synthetic_run(_src_run(), "Here is your summary.")
    assert run.schema == "adk"
    assert run.completion_tokens > 0
    assert any(ev.get("event") == "run_end" for ev in run.events)
    res = apply_heuristics(run)
    assert res.skip_judge is False  # the whole point — judge must run


async def test_replay_run_produces_scored_result() -> None:
    async def fake_chat(model, system, user, *, temperature, max_tokens):
        assert system == "Be warm."
        assert user == "Summarize my inbox"
        assert model == "m/x"
        return ("Sure — 3 unread, all low priority.", 0.0004)

    result = await replay_run(
        _src_run(), _variant(), _call=fake_chat, judge_fn=_fake_judge,
    )
    assert result.trace_id == "t1"
    assert result.agent == "outlook-king"
    assert result.variant_version == "v-1.0"
    assert result.model == "m/x"
    assert result.output_text == "Sure — 3 unread, all low priority."
    assert result.replay_cost_usd == 0.0004
    assert result.scored["components"]["quality"] == 0.9
    assert result.scored["trace_id"] == "t1"


async def test_replay_run_retries_chat_once_then_succeeds() -> None:
    calls = {"n": 0}

    async def flaky_chat(model, system, user, *, temperature, max_tokens):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient 503")
        return ("ok", 0.0)

    result = await replay_run(_src_run(), _variant(), _call=flaky_chat, judge_fn=_fake_judge)
    assert calls["n"] == 2
    assert result.output_text == "ok"


async def test_replay_run_raises_after_second_chat_failure() -> None:
    async def dead_chat(model, system, user, *, temperature, max_tokens):
        raise RuntimeError("still down")

    with pytest.raises(RuntimeError, match="still down"):
        await replay_run(_src_run(), _variant(), _call=dead_chat, judge_fn=_fake_judge)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_replay_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nuvel.eval.replay.runner'`.

- [ ] **Step 3: Write the runner part 1 (adapter + replay_run)**

```python
# nuvel/eval/replay/runner.py
"""Replay orchestration.

``_call_litellm_chat`` is the litellm boundary (system+user → output). It is
NOT the judge's adapter: replay generates prose, so it sends no
``response_format`` hint and therefore needs no empty-content fallback (that
fallback exists only because the judge requests JSON). ``replay_run`` does one
trace; ``ReplayRunner`` (Task 4) is the batch driver.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from dataclasses import asdict
from typing import Awaitable, Callable

from nuvel.eval.replay.schema import ReplayResult, Variant
from nuvel.eval.rubric import DEFAULT_RUBRIC, Rubric
from nuvel.eval.scorer import JudgeFn, score_run
from nuvel.traces_cli import Run

logger = logging.getLogger(__name__)

# (model, system, user, *, temperature, max_tokens) -> (output_text, cost_usd)
ChatFn = Callable[..., Awaitable[tuple[str, float]]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _call_litellm_chat(
    model: str,
    system_prompt: str,
    user_input: str,
    *,
    temperature: float,
    max_tokens: int,
) -> tuple[str, float]:
    """Thin litellm adapter for replay. Returns (output_text, cost_usd)."""
    import litellm

    from nuvel.eval.judge import _extract_cost, _quiet_litellm_once

    _quiet_litellm_once()
    response = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = (response.choices[0].message.content or "").strip()
    return content, _extract_cost(response, model)


def build_synthetic_run(src: Run, output_text: str) -> Run:
    """Shape a ``Run`` the scorer can judge from a single replayed output.

    CRITICAL: ``apply_heuristics`` early-exits with ``skip_judge=True`` if the
    run has no ``run_end`` event (incomplete trace) or ``completion_tokens == 0``
    (no assistant output). Both must be satisfied or the judge never runs and
    quality silently stays 0.0. Hence the explicit ``run_end`` event and
    ``completion_tokens=1``.
    """
    return Run(
        agent=src.agent,
        file=src.file,
        session_id=src.session_id,
        trace_id=src.trace_id,
        user_input=src.user_input,
        completion_tokens=1,
        schema="adk",
        events=[
            {"event": "run_start", "user_input": src.user_input or ""},
            {"event": "llm_response", "response_text": output_text},
            {"event": "run_end"},
        ],
    )


async def replay_run(
    src: Run,
    variant: Variant,
    *,
    rubric: Rubric | None = None,
    _call: ChatFn = _call_litellm_chat,
    judge_fn: JudgeFn | None = None,
) -> ReplayResult:
    """Replay one trace under ``variant`` and score the output.

    Assumes ``src.user_input`` is non-empty (caller filters). Retries the chat
    call once on exception (mirrors the judge's retry); re-raises on the second
    failure so the batch driver can record ``replay.error`` and skip the trace.
    """
    model = variant.resolved_model()
    user_input = src.user_input or ""

    last_exc: Exception | None = None
    output_text = ""
    replay_cost = 0.0
    for attempt in (1, 2):
        try:
            output_text, replay_cost = await _call(
                model, variant.system_prompt, user_input,
                temperature=variant.temperature, max_tokens=variant.max_tokens,
            )
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001 — litellm surface is wide
            last_exc = exc
            logger.warning("replay chat failed (attempt %d): %s", attempt, exc)
    if last_exc is not None:
        raise last_exc

    synthetic = build_synthetic_run(src, output_text)
    scored = await score_run(synthetic, rubric=rubric or DEFAULT_RUBRIC, judge_fn=judge_fn)

    return ReplayResult(
        trace_id=src.trace_id or src.session_id or "",
        agent=src.agent,
        variant_name=variant.name,
        variant_version=variant.version,
        replayed_at=_now_iso(),
        model=model,
        output_text=output_text,
        replay_cost_usd=replay_cost,
        scored=asdict(scored),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_replay_runner.py -v`
Expected: PASS (4 tests). The `test_build_synthetic_run_is_judgeable` test guards the skip_judge trap.

- [ ] **Step 5: Commit**

```bash
git add nuvel/eval/replay/runner.py tests/test_eval_replay_runner.py
git commit -m "feat(eval): per-trace replay_run with judgeable synthetic run + retry"
```

---

## Task 4: Batch driver — idempotency, budget, concurrency (`runner.py` part 2)

**Files:**
- Modify: `nuvel/eval/replay/runner.py` (append `ReplayReport` + `ReplayRunner`)
- Modify: `nuvel/eval/replay/__init__.py` (export `ReplayRunner`, `ReplayReport`, `replay_run`)
- Test: `tests/test_eval_replay_runner.py` (append)

- [ ] **Step 1: Write the failing test (append to the runner test file)**

```python
# --- append to tests/test_eval_replay_runner.py ---
import json

from nuvel.eval.replay.runner import ReplayRunner
from nuvel.eval.replay.schema import ReplayResult, load_replay_index


def _write_traces(traces_dir: Path, n: int = 3) -> None:
    """Write one ADK trace file with n complete runs carrying user_input."""
    traces_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(n):
        sid = f"s{i}"
        lines.append({"event": "run_start", "session_id": sid, "trace_id": f"t{i}",
                      "agent": traces_dir.parent.name, "user_input": f"question {i}"})
        lines.append({"event": "llm_response", "session_id": sid, "response_text": "orig"})
        lines.append({"event": "run_end", "session_id": sid})
    (traces_dir / "2026-05-20.jsonl").write_text(
        "\n".join(json.dumps(d) for d in lines) + "\n", encoding="utf-8")


def _runner(traces_dir: Path, **kw):
    async def fake_chat(model, system, user, *, temperature, max_tokens):
        return (f"variant reply to {user}", 0.0001)

    async def fake_judge(run, rubric):
        from nuvel.eval.schema import JudgeResult
        return JudgeResult(model="j", success=1.0, quality=0.8, cost_usd=0.0002)

    defaults = dict(
        variant=_variant(),
        traces_dir=traces_dir,
        agent=traces_dir.parent.name,
        chat_fn=fake_chat,
        judge_fn=fake_judge,
    )
    defaults.update(kw)
    return ReplayRunner(**defaults)


async def test_runner_writes_one_result_per_trace(tmp_path: Path) -> None:
    traces = tmp_path / "outlook-king" / "traces"
    _write_traces(traces, n=3)
    report = await _runner(traces).run()
    assert report.replayed == 3
    idx = load_replay_index(traces / "replays" / "friendlier.jsonl")
    assert len(idx) == 3
    assert all(r.output_text.startswith("variant reply") for r in idx.values())


async def test_runner_is_idempotent_on_same_version(tmp_path: Path) -> None:
    traces = tmp_path / "outlook-king" / "traces"
    _write_traces(traces, n=2)
    await _runner(traces).run()
    second = await _runner(traces).run()
    assert second.replayed == 0
    assert second.skipped_existing == 2


async def test_runner_force_rescore(tmp_path: Path) -> None:
    traces = tmp_path / "outlook-king" / "traces"
    _write_traces(traces, n=2)
    await _runner(traces).run()
    forced = await _runner(traces, force=True).run()
    assert forced.replayed == 2


async def test_runner_skips_traces_without_user_input(tmp_path: Path) -> None:
    traces = tmp_path / "outlook-king" / "traces"
    traces.mkdir(parents=True)
    (traces / "2026-05-20.jsonl").write_text(json.dumps(
        {"event": "run_start", "session_id": "s0", "trace_id": "t0",
         "agent": "outlook-king"}) + "\n" + json.dumps(
        {"event": "run_end", "session_id": "s0"}) + "\n", encoding="utf-8")
    report = await _runner(traces).run()
    assert report.replayed == 0
    assert report.skipped_no_input == 1


async def test_runner_stops_at_cost_budget(tmp_path: Path) -> None:
    traces = tmp_path / "outlook-king" / "traces"
    _write_traces(traces, n=10)
    # each trace = 0.0001 (chat) + 0.0002 (judge) = 0.0003; budget 0.0005 ⇒ ~2 traces
    report = await _runner(traces, max_cost_usd=0.0005).run()
    assert report.budget_exhausted is True
    assert report.replayed < 10


async def test_runner_dry_run_writes_nothing(tmp_path: Path) -> None:
    traces = tmp_path / "outlook-king" / "traces"
    _write_traces(traces, n=2)
    report = await _runner(traces, dry_run=True).run()
    assert report.replayed == 2
    assert not (traces / "replays" / "friendlier.jsonl").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_replay_runner.py -v`
Expected: FAIL — `ImportError: cannot import name 'ReplayRunner'`.

- [ ] **Step 3: Append the batch driver to `runner.py`**

Add these imports to the top of `nuvel/eval/replay/runner.py`:

```python
import asyncio
from dataclasses import dataclass, field
from datetime import datetime as _datetime
from pathlib import Path

from nuvel.eval.replay.schema import append_replay, load_replay_index
from nuvel.traces_cli import _agent_label_for, _iter_trace_files, _parse_file_runs
```

Append to the end of the module:

```python
@dataclass
class ReplayReport:
    """Summary of one ``ReplayRunner.run()``."""

    replayed: int = 0
    skipped_existing: int = 0
    skipped_no_input: int = 0
    replay_errors: int = 0
    total_cost_usd: float = 0.0
    budget_exhausted: bool = False


@dataclass
class ReplayRunner:
    """Batch driver: replay a variant across one agent's traces dir.

    Mirrors ``ScoreSession`` — idempotency on ``(trace_id, variant.version)``,
    a shared cost budget across BOTH the replay chat call and the judge call,
    and bounded concurrency.
    """

    variant: Variant
    traces_dir: Path
    agent: str
    since: "_datetime | None" = None
    max_cost_usd: float = 1.0
    concurrency: int = 5
    force: bool = False
    dry_run: bool = False
    rubric: Rubric | None = None
    chat_fn: ChatFn = _call_litellm_chat          # injection seam for tests
    judge_fn: JudgeFn | None = None               # injection seam for tests

    def _replay_path(self) -> Path:
        return self.traces_dir / "replays" / f"{self.variant.name}.jsonl"

    def _collect_runs(self) -> list[Run]:
        runs: list[Run] = []
        for f in _iter_trace_files([self.traces_dir]):
            for r in _parse_file_runs(f, keep_events=True):
                if self.agent and _agent_label_for(r.file) != self.agent:
                    continue
                if self.since is not None and r.started_at:
                    try:
                        ts = _datetime.fromisoformat(r.started_at.replace("Z", "+00:00"))
                        if ts < self.since:
                            continue
                    except ValueError:
                        pass
                runs.append(r)
        return runs

    async def run(self) -> ReplayReport:
        report = ReplayReport()
        runs = self._collect_runs()
        if not runs:
            return report

        replay_path = self._replay_path()
        existing = load_replay_index(replay_path)

        budget_lock = asyncio.Lock()
        budget = {"spent": 0.0, "exhausted": False}
        write_lock = asyncio.Lock()
        sem = asyncio.Semaphore(self.concurrency)

        async def _one(r: Run) -> None:
            tid = r.trace_id or r.session_id or ""
            if not (r.user_input or "").strip():
                report.skipped_no_input += 1
                return
            if not self.force:
                prior = existing.get(tid)
                if prior is not None and prior.variant_version == self.variant.version:
                    report.skipped_existing += 1
                    return
            async with budget_lock:
                if budget["exhausted"]:
                    return
            async with sem:
                try:
                    result = await replay_run(
                        r, self.variant,
                        rubric=self.rubric,
                        _call=self.chat_fn,
                        judge_fn=self.judge_fn,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("replay failed for %s: %s", tid, exc)
                    report.replay_errors += 1
                    return

            judge_cost = float((result.scored.get("judge") or {}).get("cost_usd") or 0.0)
            call_cost = result.replay_cost_usd + judge_cost

            if not self.dry_run:
                replay_path.parent.mkdir(parents=True, exist_ok=True)
                async with write_lock:
                    append_replay(replay_path, result)
            report.replayed += 1
            report.total_cost_usd += call_cost

            async with budget_lock:
                budget["spent"] += call_cost
                if budget["spent"] >= self.max_cost_usd:
                    budget["exhausted"] = True

        await asyncio.gather(*(asyncio.create_task(_one(r)) for r in runs))
        report.budget_exhausted = budget["exhausted"]
        return report
```

- [ ] **Step 4: Export from the package init**

In `nuvel/eval/replay/__init__.py`, add to the imports and `__all__`:

```python
from nuvel.eval.replay.runner import ReplayReport, ReplayRunner, replay_run
```

Add `"ReplayReport"`, `"ReplayRunner"`, `"replay_run"` to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_eval_replay_runner.py -v`
Expected: PASS (all runner tests). Note: the budget test asserts `replayed < 10` rather than an exact count — concurrency means a few in-flight tasks may complete after the budget trips, which is the documented "in-flight replays complete" behavior.

- [ ] **Step 6: Commit**

```bash
git add nuvel/eval/replay/runner.py nuvel/eval/replay/__init__.py tests/test_eval_replay_runner.py
git commit -m "feat(eval): ReplayRunner batch driver — idempotency, cost budget, concurrency"
```

---

## Task 5: Baseline-vs-variant comparison (`compare.py`)

**Files:**
- Create: `nuvel/eval/replay/compare.py`
- Test: `tests/test_eval_replay_compare.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_replay_compare.py
"""Pure-function comparison of baseline ScoredRuns vs variant ReplayResults."""
from __future__ import annotations

from nuvel.eval.replay.compare import compare
from nuvel.eval.replay.schema import ReplayResult
from nuvel.eval.schema import ScoredRun


def _baseline(trace_id: str, overall: float, quality: float, success: float,
              agent: str = "outlook-king") -> ScoredRun:
    return ScoredRun(
        trace_id=trace_id, agent=agent, scored_at="2026-05-20T00:00:00+00:00",
        scorer_version="1.0", rubric_version="default-1.0", overall=overall,
        components={"success": success, "quality": quality},
    )


def _variant(trace_id: str, overall: float, quality: float, success: float,
             agent: str = "outlook-king") -> ReplayResult:
    return ReplayResult(
        trace_id=trace_id, agent=agent, variant_name="v", variant_version="v-1.0",
        replayed_at="2026-05-21T00:00:00+00:00", model="m", output_text="o",
        replay_cost_usd=0.0,
        scored={"overall": overall, "components": {"quality": quality, "success": success}},
    )


def test_compare_pairs_and_computes_deltas() -> None:
    base = [_baseline("t1", 0.70, 0.6, 0.8), _baseline("t2", 0.80, 0.9, 0.7)]
    var = [_variant("t1", 0.80, 0.8, 0.8), _variant("t2", 0.75, 0.8, 0.7)]
    report = compare(base, var)
    row = report.agents[0]
    assert row.agent == "outlook-king"
    assert row.n == 2
    assert round(row.baseline_overall_mean, 4) == 0.75
    assert round(row.variant_overall_mean, 4) == 0.775
    assert round(row.d_overall, 4) == 0.025      # mean of (+0.10, -0.05)
    assert round(row.d_quality, 4) == 0.05        # mean of (+0.2, -0.1)
    assert row.wins == 1 and row.losses == 1 and row.ties == 0


def test_compare_only_pairs_traces_in_both() -> None:
    base = [_baseline("t1", 0.7, 0.6, 0.8), _baseline("t2", 0.8, 0.9, 0.7)]
    var = [_variant("t1", 0.8, 0.8, 0.8)]  # t2 missing
    report = compare(base, var)
    assert report.agents[0].n == 1


def test_compare_small_sample_flag() -> None:
    base = [_baseline(f"t{i}", 0.7, 0.6, 0.8) for i in range(5)]
    var = [_variant(f"t{i}", 0.7, 0.6, 0.8) for i in range(5)]
    report = compare(base, var)
    assert report.agents[0].small_sample is True


def test_compare_regression_flag() -> None:
    base = [_baseline(f"t{i}", 0.9, 0.9, 0.9) for i in range(3)]
    var = [_variant(f"t{i}", 0.7, 0.7, 0.7) for i in range(3)]  # Δ overall = -0.2
    report = compare(base, var)
    assert report.regressed is True


def test_compare_groups_by_agent() -> None:
    base = [_baseline("t1", 0.7, 0.6, 0.8, agent="a"),
            _baseline("t2", 0.7, 0.6, 0.8, agent="b")]
    var = [_variant("t1", 0.8, 0.7, 0.9, agent="a"),
           _variant("t2", 0.6, 0.5, 0.7, agent="b")]
    report = compare(base, var)
    assert {row.agent for row in report.agents} == {"a", "b"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_replay_compare.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nuvel.eval.replay.compare'`.

- [ ] **Step 3: Write the comparison module**

```python
# nuvel/eval/replay/compare.py
"""Pure-function diff: baseline ScoredRuns vs variant ReplayResults.

Pairs by ``trace_id`` (a trace must exist in both to count). Deltas are means
of per-trace differences. No I/O — the CLI loads the inputs and renders the
output.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nuvel.eval.replay.schema import ReplayResult
from nuvel.eval.schema import ScoredRun

_SMALL_SAMPLE_N = 30
_REGRESSION_THRESHOLD = -0.05


@dataclass
class AgentComparison:
    """One agent's paired baseline-vs-variant aggregate."""

    agent: str
    n: int
    baseline_overall_mean: float
    variant_overall_mean: float
    d_overall: float
    d_quality: float
    d_success: float
    wins: int
    ties: int
    losses: int

    @property
    def small_sample(self) -> bool:
        return self.n < _SMALL_SAMPLE_N


@dataclass
class ComparisonReport:
    """All per-agent comparisons + a top-level regression flag."""

    agents: list[AgentComparison] = field(default_factory=list)

    @property
    def regressed(self) -> bool:
        return any(a.d_overall < _REGRESSION_THRESHOLD for a in self.agents)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _component(scored: dict, key: str) -> float:
    return float((scored.get("components") or {}).get(key) or 0.0)


def compare(
    baseline: list[ScoredRun], variant: list[ReplayResult]
) -> ComparisonReport:
    """Diff variant replays against the baseline, grouped per agent."""
    base_by_id = {b.trace_id: b for b in baseline}
    # Group variant results by agent, keeping only traces present in baseline.
    by_agent: dict[str, list[tuple[ScoredRun, ReplayResult]]] = {}
    for v in variant:
        b = base_by_id.get(v.trace_id)
        if b is None:
            continue
        by_agent.setdefault(v.agent, []).append((b, v))

    report = ComparisonReport()
    for agent in sorted(by_agent):
        pairs = by_agent[agent]
        d_overall, d_quality, d_success = [], [], []
        wins = ties = losses = 0
        base_overall, var_overall = [], []
        for b, v in pairs:
            vs = v.scored
            bo, vo = b.overall, float(vs.get("overall") or 0.0)
            base_overall.append(bo)
            var_overall.append(vo)
            do = vo - bo
            d_overall.append(do)
            d_quality.append(_component(vs, "quality") - float(b.components.get("quality", 0.0)))
            d_success.append(_component(vs, "success") - float(b.components.get("success", 0.0)))
            if do > 0:
                wins += 1
            elif do < 0:
                losses += 1
            else:
                ties += 1
        report.agents.append(
            AgentComparison(
                agent=agent,
                n=len(pairs),
                baseline_overall_mean=_mean(base_overall),
                variant_overall_mean=_mean(var_overall),
                d_overall=_mean(d_overall),
                d_quality=_mean(d_quality),
                d_success=_mean(d_success),
                wins=wins,
                ties=ties,
                losses=losses,
            )
        )
    return report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_replay_compare.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add nuvel/eval/replay/compare.py tests/test_eval_replay_compare.py
git commit -m "feat(eval): baseline-vs-variant comparison (pure functions)"
```

---

## Task 6: CLI subcommands + rendering (`cli.py`, `report.py`)

**Files:**
- Modify: `nuvel/eval/report.py` (add `render_variants`, `render_comparison`)
- Modify: `nuvel/eval/cli.py` (register `variants`, `replay`, `compare`)
- Test: `tests/test_eval_replay_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_replay_cli.py
"""CLI wiring for variants/replay/compare. Uses the real argparse tree."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nuvel.eval.replay.compare import AgentComparison, ComparisonReport
from nuvel.eval.report import render_comparison, render_variants
from nuvel.eval.replay.variant import DiscoveredVariant
from nuvel.eval.replay.schema import Variant


def test_render_variants_lists_name_and_agent() -> None:
    rows = [DiscoveredVariant(
        agent="outlook-king",
        variant=Variant(version="v-1.0", name="friendlier", system_prompt="hi",
                        description="warm"),
        path=Path("x.yaml"),
        traces_dir=Path("t"),
    )]
    out = render_variants(rows)
    assert "friendlier" in out and "outlook-king" in out and "v-1.0" in out


def test_render_comparison_has_columns_and_warning() -> None:
    report = ComparisonReport(agents=[AgentComparison(
        agent="outlook-king", n=5, baseline_overall_mean=0.74, variant_overall_mean=0.81,
        d_overall=0.07, d_quality=0.1, d_success=0.04, wins=3, ties=1, losses=1)])
    out = render_comparison(report)
    assert "outlook-king" in out
    assert "+0.07" in out or "0.07" in out
    assert "sample" in out.lower()  # N<30 warning present


def _build_cli():
    import argparse
    from nuvel.eval.cli import register
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    register(sub)
    return parser


def test_cli_variants_parses() -> None:
    args = _build_cli().parse_args(["eval", "variants", "--agent", "outlook"])
    assert args.eval_command == "variants"
    assert args.agent == "outlook"


def test_cli_replay_parses_all_flags() -> None:
    args = _build_cli().parse_args(
        ["eval", "replay", "friendlier", "--agent", "outlook", "--since", "7d",
         "--max-cost-usd", "0.50", "--force", "--dry-run"])
    assert args.eval_command == "replay"
    assert args.variant_name == "friendlier"
    assert args.max_cost_usd == 0.50
    assert args.force is True and args.dry_run is True


def test_cli_compare_parses() -> None:
    args = _build_cli().parse_args(["eval", "compare", "friendlier", "--agent", "outlook"])
    assert args.eval_command == "compare"
    assert args.variant_name == "friendlier"


def test_cli_replay_unknown_variant_exits_1(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)  # no generated-agents → no variants
    args = _build_cli().parse_args(["eval", "replay", "ghost"])
    rc = args.func(args)
    assert rc == 1
    assert "no variant" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_replay_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_comparison'` (and CLI subcommands absent).

- [ ] **Step 3: Add renderers to `report.py`**

Append to `nuvel/eval/report.py`:

```python
def render_variants(rows: list) -> str:
    """List discovered variants: name, version, agent, description."""
    if not rows:
        return "No variants found (looked for generated-agents/*/evals/variants/*.yaml)."
    lines = ["Variants:", ""]
    for r in rows:
        v = r.variant
        desc = (v.description or "").splitlines()[0] if v.description else ""
        lines.append(f"  {v.name:<24} {v.version:<22} {r.agent:<16} {desc}")
    return "\n".join(lines)


def render_comparison(report) -> str:
    """Per-agent baseline-vs-variant table with sample-size + regression notes."""
    if not report.agents:
        return "No paired traces to compare (run `nuvel eval score` and `nuvel eval replay` first)."
    header = (
        f"  {'Agent':<16} {'N':>4} {'Base':>6} {'Var':>6} {'Δover':>7} "
        f"{'Δqual':>7} {'Δsucc':>7} {'W':>4} {'T':>4} {'L':>4}"
    )
    lines = [header, "  " + "-" * (len(header) - 2)]
    warn = False
    for a in report.agents:
        lines.append(
            f"  {a.agent:<16} {a.n:>4} {a.baseline_overall_mean:>6.2f} "
            f"{a.variant_overall_mean:>6.2f} {a.d_overall:>+7.2f} "
            f"{a.d_quality:>+7.2f} {a.d_success:>+7.2f} "
            f"{a.wins:>4} {a.ties:>4} {a.losses:>4}"
        )
        if a.small_sample:
            warn = True
    if warn:
        lines.append("")
        lines.append("  ⚠ sample too small (N<30) for reliable conclusions — "
                     "collect more traces or run `nuvel eval score` to fill gaps.")
    if report.regressed:
        lines.append("  ⚠ regression detected (Δ overall < -0.05 for at least one agent).")
    return "\n".join(lines)
```

- [ ] **Step 4: Add the subcommands to `cli.py`**

Add imports near the top of `nuvel/eval/cli.py`:

```python
from nuvel.eval.replay.compare import compare
from nuvel.eval.replay.runner import ReplayRunner
from nuvel.eval.replay.schema import load_replay_index
from nuvel.eval.replay.variant import discover_variants, load_variant
from nuvel.eval.report import render_comparison, render_variants
```

Add these command handlers (place above `register`):

```python
def _resolve_variant(name: str, agent: str | None):
    """Return the single DiscoveredVariant matching name (+optional agent), or None."""
    matches = [r for r in discover_variants(agent_filter=agent) if r.variant.name == name]
    return matches[0] if len(matches) == 1 else (None, matches)[0] if matches else None


def _cmd_variants(args: argparse.Namespace) -> int:
    print(render_variants(discover_variants(agent_filter=args.agent)))
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    matches = [r for r in discover_variants(agent_filter=args.agent)
               if r.variant.name == args.variant_name]
    if not matches:
        print(f"no variant named '{args.variant_name}' found "
              f"(looked in generated-agents/*/evals/variants/).")
        return 1
    if len(matches) > 1:
        agents = ", ".join(sorted(m.agent for m in matches))
        print(f"variant '{args.variant_name}' exists for multiple agents ({agents}); "
              f"disambiguate with --agent.")
        return 1
    row = matches[0]
    # Re-load to surface any YAML error that discovery swallowed.
    variant = load_variant(row.path)
    since = _parse_since(getattr(args, "since", "") or "")
    runner = ReplayRunner(
        variant=variant,
        traces_dir=row.traces_dir,
        agent=row.agent,
        since=since,
        max_cost_usd=args.max_cost_usd,
        concurrency=args.concurrency,
        force=args.force,
        dry_run=args.dry_run,
    )
    report = asyncio.run(runner.run())
    print(f"Replayed: {report.replayed}  (variant '{variant.name}' @ {variant.version})")
    print(f"  skipped (already replayed at this version): {report.skipped_existing}")
    print(f"  skipped (no user_input on source trace):    {report.skipped_no_input}")
    print(f"  replay errors: {report.replay_errors}")
    print(f"  total cost (replay + judge): ${report.total_cost_usd:.4f}")
    if report.budget_exhausted:
        print(f"  ⚠ budget of ${args.max_cost_usd:.2f} exhausted — remaining traces not replayed")
    print("\nNote: v1 replays the single LLM call (variant system prompt + historical "
          "user_input). Tool use, memory recall, and follow-up turns are NOT re-executed.")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    matches = [r for r in discover_variants(agent_filter=args.agent)
               if r.variant.name == args.variant_name]
    if not matches:
        print(f"no variant named '{args.variant_name}' found.")
        return 1
    baseline = []
    variant_results = []
    for row in matches:
        baseline.extend(load_scored_index(row.traces_dir / "scored.jsonl").values())
        replay_path = row.traces_dir / "replays" / f"{row.variant.name}.jsonl"
        variant_results.extend(load_replay_index(replay_path).values())
    if not baseline:
        print("no baseline scored.jsonl found — run `nuvel eval score` first, then re-run compare.")
        return 1
    report = compare(baseline, variant_results)
    print(render_comparison(report))
    return 2 if report.regressed else 0
```

Register them inside `register()` (after the existing `p_drift` block):

```python
    p_variants = sub.add_parser("variants", help="List discovered replay variants.")
    p_variants.add_argument("--agent", "-a", default=None, help="Filter by agent (substring).")
    p_variants.set_defaults(func=_cmd_variants)

    p_replay = sub.add_parser("replay", help="Replay a variant against historical traces.")
    p_replay.add_argument("variant_name", help="Variant name (see `nuvel eval variants`).")
    p_replay.add_argument("--agent", "-a", default=None, help="Disambiguate by agent (substring).")
    p_replay.add_argument("--since", default=None, help="Only traces at/after this date (YYYY-MM-DD, ISO, or Nd).")
    p_replay.add_argument("--max-cost-usd", type=float, default=1.0,
                          help="Stop past this total spend across replay + judge (default $1.00).")
    p_replay.add_argument("--concurrency", type=int, default=5, help="Max simultaneous replays (default 5).")
    p_replay.add_argument("--force", action="store_true", help="Re-replay even if already at this version.")
    p_replay.add_argument("--dry-run", action="store_true", help="Replay + score but write nothing.")
    p_replay.set_defaults(func=_cmd_replay)

    p_compare = sub.add_parser("compare", help="Diff a variant's replays against the baseline.")
    p_compare.add_argument("variant_name", help="Variant name to compare.")
    p_compare.add_argument("--agent", "-a", default=None, help="Filter by agent (substring).")
    p_compare.set_defaults(func=_cmd_compare)
```

Remove the unused `_resolve_variant` helper if you prefer — the inline matching in `_cmd_replay`/`_cmd_compare` is the canonical path. (Listed above only as an illustration; do not ship dead code.)

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_eval_replay_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add nuvel/eval/cli.py nuvel/eval/report.py tests/test_eval_replay_cli.py
git commit -m "feat(eval): nuvel eval variants/replay/compare CLI + rendering"
```

---

## Task 7: End-to-end integration test

**Files:**
- Test: `tests/test_eval_replay_integration.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/test_eval_replay_integration.py
"""End-to-end: traces + scored.jsonl + variant YAML → replay → compare.

Exercises discovery, the batch runner (with mocked LLM seams), persistence,
and the comparison table together — the wiring no single unit test covers.
"""
from __future__ import annotations

import json
from pathlib import Path

from nuvel.eval.replay.compare import compare
from nuvel.eval.replay.runner import ReplayRunner
from nuvel.eval.replay.schema import load_replay_index
from nuvel.eval.replay.variant import discover_variants
from nuvel.eval.schema import JudgeResult, ScoredRun
from nuvel.eval.writer import append_scored


def _seed(root: Path) -> Path:
    agent_dir = root / "generated-agents" / "outlook-king"
    traces = agent_dir / "traces"
    traces.mkdir(parents=True)
    # Two complete ADK runs with user_input.
    lines = []
    for i in range(2):
        sid = f"s{i}"
        lines += [
            {"event": "run_start", "session_id": sid, "trace_id": f"t{i}",
             "agent": "outlook-king", "user_input": f"q{i}"},
            {"event": "llm_response", "session_id": sid, "response_text": "orig"},
            {"event": "run_end", "session_id": sid},
        ]
    (traces / "2026-05-20.jsonl").write_text(
        "\n".join(json.dumps(d) for d in lines) + "\n", encoding="utf-8")
    # Baseline scored.jsonl.
    for i in range(2):
        append_scored(traces / "scored.jsonl", ScoredRun(
            trace_id=f"t{i}", agent="outlook-king", scored_at="2026-05-20T00:00:00+00:00",
            scorer_version="1.0", rubric_version="default-1.0", overall=0.60,
            components={"success": 0.6, "quality": 0.5}))
    # Variant YAML.
    vdir = agent_dir / "evals" / "variants"
    vdir.mkdir(parents=True)
    (vdir / "friendlier.yaml").write_text(
        "version: friendlier-1.0\nname: friendlier\nsystem_prompt: Be warm.\n", encoding="utf-8")
    return traces


async def test_replay_then_compare(tmp_path: Path, monkeypatch) -> None:
    traces = _seed(tmp_path)
    monkeypatch.chdir(tmp_path)

    async def fake_chat(model, system, user, *, temperature, max_tokens):
        return (f"warm reply to {user}", 0.0001)

    async def fake_judge(run, rubric):
        return JudgeResult(model="j", success=0.9, quality=0.9, cost_usd=0.0002)

    row = next(r for r in discover_variants() if r.variant.name == "friendlier")
    report = await ReplayRunner(
        variant=row.variant, traces_dir=row.traces_dir, agent=row.agent,
        chat_fn=fake_chat, judge_fn=fake_judge,
    ).run()
    assert report.replayed == 2

    replays = list(load_replay_index(traces / "replays" / "friendlier.jsonl").values())
    assert len(replays) == 2

    from nuvel.eval.writer import load_scored_index
    baseline = list(load_scored_index(traces / "scored.jsonl").values())
    cmp_report = compare(baseline, replays)
    arow = cmp_report.agents[0]
    assert arow.agent == "outlook-king"
    assert arow.n == 2
    # Variant scored higher (0.9/0.9 vs baseline 0.6) ⇒ positive Δ, all wins.
    assert arow.d_overall > 0
    assert arow.wins == 2
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_eval_replay_integration.py -v`
Expected: PASS.

- [ ] **Step 3: Run the full suite + the eval slice**

Run: `make test` (or `pytest tests/ -v`)
Expected: all green, no regressions in existing eval tests.

- [ ] **Step 4: Commit**

```bash
git add tests/test_eval_replay_integration.py
git commit -m "test(eval): end-to-end replay → compare integration"
```

---

## Task 8: Docs sync (README + nuvel SKILL.md)

Per CLAUDE.md: the CLI table in the README and `.claude/skills/nuvel/SKILL.md` must stay in sync with CLI flag changes.

**Files:**
- Modify: `README.md` (CLI surface table — add `eval variants/replay/compare`)
- Modify: `.claude/skills/nuvel/SKILL.md` (if it enumerates `nuvel eval` subcommands)

- [ ] **Step 1: Locate the eval CLI documentation**

Run: `grep -rn "nuvel eval" README.md .claude/skills/nuvel/SKILL.md`
Expected: find the existing `nuvel eval` rows (score/report/worst/drift).

- [ ] **Step 2: Add the three subcommands**

In the README CLI table, beneath the existing `nuvel eval` entries, add rows describing:
- `nuvel eval variants [--agent X]` — list discovered replay variants.
- `nuvel eval replay <name> [--agent X] [--since 7d] [--max-cost-usd N] [--force] [--dry-run]` — replay a variant against historical traces.
- `nuvel eval compare <name> [--agent X]` — diff a variant's replays against the baseline (exit 2 on regression).

Match the surrounding table's exact column format. If `SKILL.md` lists eval subcommands, mirror the same three lines there; if it only references `nuvel eval` generically, no change is needed.

- [ ] **Step 3: Commit**

```bash
git add README.md .claude/skills/nuvel/SKILL.md
git commit -m "docs: document nuvel eval replay/compare/variants"
```

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- Variant model (spec §"Variant Model") → Task 1 (`Variant`) + Task 2 (loader).
- Replay semantics + synthetic-run scoring (spec §"Replay Semantics") → Task 3 (`replay_run`, `build_synthetic_run`).
- ReplayResult schema (spec §"ReplayResult Schema") → Task 1.
- Comparison semantics, N<30, regression exit (spec §"Comparison Semantics") → Task 5 + Task 6 (`render_comparison`, exit 2).
- CLI surface — `variants`/`replay`/`compare` (spec §"CLI Surface") → Task 6.
- Cost budgeting across both call types (spec §"Cost Budgeting") → Task 4 (`ReplayRunner`, `call_cost = replay + judge`).
- Idempotency / versioning, `(trace_id, variant_version)` key, `REPLAY_VERSION` (spec §"Idempotency & Versioning") → Task 1 (`REPLAY_VERSION`) + Task 4 (skip logic).
- Storage layout `traces/replays/<name>.jsonl` (spec §"Storage Layout") → Task 4 (`_replay_path`). Spec's corrected note: no `_RESERVED_TRACE_SIBLINGS` change needed — honored (no such task exists, by design).
- Error handling table (spec §"Error Handling") → Task 3 (chat retry/raise), Task 4 (no-input skip, error count), Task 6 (invalid YAML fail-fast via `load_variant`, missing baseline hint).
- Testing (spec §"Testing") → Tasks 1-7 mirror the spec's unit + integration breakdown; `_fake_response` idiom adapted to injection seams.

**2. Placeholder scan** — no TBD/TODO; every code step is complete and runnable. The one illustrative `_resolve_variant` helper is explicitly flagged as not-to-ship.

**3. Type consistency** — verified across tasks: `Variant`, `ReplayResult`, `DiscoveredVariant`, `ReplayReport`, `AgentComparison`, `ComparisonReport` names and fields are identical everywhere referenced; `replay_run(src, variant, *, rubric, _call, judge_fn)` and `ReplayRunner(chat_fn=, judge_fn=)` seam names match their tests; `ChatFn`/`JudgeFn` aliases consistent; `score_run(..., judge_fn=)` matches the real scorer signature; `_iter_trace_files`/`_parse_file_runs`/`_agent_label_for`/`_parse_since`/`load_scored_index` match real `traces_cli`/`writer` exports.

---

## Notes / deviations from spec (intentional)

1. **Replay persistence lives in `schema.py`, not a separate `writer.py`.** Two tiny functions beside their wire format; keeps the spec's 4-module `replay/` layout.
2. **No empty-content fallback in `_call_litellm_chat`.** That fallback in `judge._call_litellm` exists solely for the `response_format=json_object` hint, which replay does not send. The spec's instruction to reuse it is inapplicable; a plain retry-once (per the spec's error table) is the correct mechanism.
3. **Synthetic run carries an explicit `run_end` event and `completion_tokens=1`.** Required to clear `apply_heuristics`' `skip_judge` early-exit — the spec's claim that heuristics are "mostly NO-OP" on the synthetic run is false, and `test_build_synthetic_run_is_judgeable` guards against regressing this.
