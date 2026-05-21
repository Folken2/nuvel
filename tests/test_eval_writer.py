"""Writer + index loader."""
from __future__ import annotations

from pathlib import Path

from nuvel.eval.schema import SCORER_VERSION, ScoredRun
from nuvel.eval.writer import append_scored, load_scored_index


def _mk(trace_id: str, overall: float = 0.5) -> ScoredRun:
    return ScoredRun(
        trace_id=trace_id,
        agent="a",
        scored_at="2026-05-20T00:00:00+00:00",
        scorer_version=SCORER_VERSION,
        rubric_version="r",
        overall=overall,
        components={"success": overall},
    )


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_scored_index(tmp_path / "missing.jsonl") == {}


def test_append_then_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "scored.jsonl"
    append_scored(path, _mk("t1", 0.7))
    append_scored(path, _mk("t2", 0.3))
    out = load_scored_index(path)
    assert set(out) == {"t1", "t2"}
    assert out["t1"].overall == 0.7


def test_load_last_occurrence_wins(tmp_path: Path) -> None:
    path = tmp_path / "scored.jsonl"
    append_scored(path, _mk("t1", 0.3))
    append_scored(path, _mk("t1", 0.9))
    out = load_scored_index(path)
    assert out["t1"].overall == 0.9


def test_load_tolerates_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "scored.jsonl"
    append_scored(path, _mk("good", 0.5))
    # Inject a garbage line in the middle.
    with path.open("a") as f:
        f.write("not json at all\n")
        f.write("\n")  # empty line OK
    append_scored(path, _mk("good2", 0.5))
    out = load_scored_index(path)
    assert set(out) == {"good", "good2"}


def test_append_creates_no_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "scored.jsonl"
    append_scored(path, _mk("t1"))
    text = path.read_text()
    # Exactly one trailing newline, no blank lines.
    assert text.count("\n") == 1
    assert "\n\n" not in text
