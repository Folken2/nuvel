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
    # Two complete ADK runs with user_input. We deliberately omit the per-event
    # "agent" field so the run's agent label is derived purely from the path
    # ("outlook-king"), matching the baseline scored.jsonl seeded below. In real
    # usage baseline and replay both flow through _parse_file_runs, so their
    # labels always agree; hand-seeding a divergent baseline label would be an
    # artificial mismatch, not a real-world condition.
    lines = []
    for i in range(2):
        sid = f"s{i}"
        lines += [
            {"event": "run_start", "session_id": sid, "trace_id": f"t{i}",
             "user_input": f"q{i}"},
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
