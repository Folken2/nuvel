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
