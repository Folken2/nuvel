"""`nuvel traces` reads nuvel.skill_run/1 traces alongside ADK and CASDK ones."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nuvel import traces_cli
from nuvel.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "traces_mixed"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read the developer's real feedback store or cwd traces."""
    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(tmp_path / "no-feedback"))
    monkeypatch.delenv("TRACE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)


def _skill_run(steps: list[dict]) -> traces_cli.Run:
    return traces_cli.Run(agent="p:s", file=Path("x"), session_id="r", schema="skill_run", steps=steps)


def test_collect_runs_reads_all_three_schemas() -> None:
    runs = traces_cli._collect_runs([str(FIXTURE)])
    assert {r.schema for r in runs} == {"adk", "casdk", "skill_run"}
    assert len(runs) == 3
    sr = next(r for r in runs if r.schema == "skill_run")
    assert sr.agent == "chief-of-staff:radar"
    assert sr.duration_ms == 58000
    assert sr.tool_calls == 6
    assert sr.subtype == "success"
    assert sr.session_id == sr.trace_id == "3f0c2a9e-6b1d-4c8e-9a57-2d4e8f1b0c63"
    assert sr.started_at == "2026-10-20T17:43:00Z"
    assert [s["id"] for s in sr.steps][:2] == ["radar.1.load-state", "radar.2.email"]


def test_step_error_rates() -> None:
    ok = [{"id": "radar.1", "status": "ok"}, {"id": "radar.2.email", "status": "ok"}]
    bad = [{"id": "radar.1", "status": "ok"}, {"id": "radar.2.email", "status": "error"}]
    assert traces_cli.step_error_rates([_skill_run(ok), _skill_run(bad)]) == [
        ("radar.2.email", 2, 1),
        ("radar.1", 2, 0),
    ]


def test_step_error_rates_ignores_non_skill_runs() -> None:
    assert traces_cli.step_error_rates([traces_cli.Run(agent="a", file=Path("x"), session_id="s")]) == []


def test_discovery_reads_feedback_dir_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fb = tmp_path / "fb"
    d = fb / "_runs" / "chief-of-staff.radar"
    d.mkdir(parents=True)
    (d / "2026-10-20.jsonl").write_text(
        (FIXTURE / "_runs/chief-of-staff.radar/2026-10-20.jsonl").read_text()
    )
    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(fb))
    runs = traces_cli._collect_runs(None)
    assert [r.schema for r in runs] == ["skill_run"]


def test_discovery_missing_feedback_dir_is_fine() -> None:
    assert traces_cli._discover_trace_dirs(None) == []


def test_malformed_skill_run_line_is_skipped(tmp_path: Path) -> None:
    d = tmp_path / "src" / "_runs" / "p.s"
    d.mkdir(parents=True)
    good = (FIXTURE / "_runs/chief-of-staff.radar/2026-10-20.jsonl").read_text()
    (d / "2026-10-20.jsonl").write_text(
        "{not json\n" + json.dumps({"schema": "nuvel.skill_run/1"}) + "\n" + good
    )
    runs = traces_cli._collect_runs([str(tmp_path / "src")])
    assert [r.schema for r in runs] == ["skill_run"]


def test_stats_prints_skill_steps(capsys: pytest.CaptureFixture[str]) -> None:
    main(["traces", "stats", "--source", str(FIXTURE)])
    out = capsys.readouterr().out
    assert "Skill steps" in out
    assert "radar.2.email" in out


def test_stats_without_skill_runs_has_no_skill_steps(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "a.jsonl").write_text((FIXTURE / "casdk.jsonl").read_text())
    main(["traces", "stats", "--source", str(tmp_path / "t")])
    assert "Skill steps" not in capsys.readouterr().out


def test_source_that_is_a_runs_dir_reads_plugin_skill_subdirs() -> None:
    runs = traces_cli._collect_runs([str(FIXTURE / "_runs")])
    assert [r.schema for r in runs] == ["skill_run"]


# ── eval excludes skill runs ─────────────────────────────────────────


def _copy_fixture(dst: Path) -> Path:
    import shutil

    shutil.copytree(FIXTURE, dst)
    return dst


def _no_scored_under_runs(root: Path) -> bool:
    return not list(root.rglob("_runs/**/scored.jsonl"))


def test_eval_score_ignores_skill_runs_via_source(tmp_path: Path) -> None:
    import asyncio

    from nuvel.eval.scorer import ScoreSession

    src = _copy_fixture(tmp_path / "src")
    judged: list[str] = []

    async def fake_judge(run, rubric):  # pragma: no cover - must not be reached for skill runs
        judged.append(run.agent)
        raise AssertionError("judge called")

    report = asyncio.run(ScoreSession(sources=[str(src)], judge_fn=fake_judge, dry_run=False).run())
    assert _no_scored_under_runs(src)
    assert "chief-of-staff:radar" not in judged
    assert all("_runs" not in d for d in map(str, report.per_dir))


def test_eval_score_ignores_skill_runs_via_feedback_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from nuvel.eval.scorer import ScoreSession

    fb = tmp_path / "fb"
    _copy_fixture(tmp_path / "x")
    (fb / "_runs").mkdir(parents=True)
    import shutil

    shutil.copytree(FIXTURE / "_runs" / "chief-of-staff.radar", fb / "_runs" / "chief-of-staff.radar")
    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(fb))
    report = asyncio.run(ScoreSession().run())
    assert report.scored_count == 0
    assert _no_scored_under_runs(fb)


def test_eval_report_and_baseline_ignore_skill_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from nuvel.eval import cli as eval_cli
    from nuvel.eval.stats import compute_baseline_stats

    src = _copy_fixture(tmp_path / "src")
    # A stray scored.jsonl beside the skill runs must not be loaded by eval.
    (src / "_runs" / "chief-of-staff.radar" / "scored.jsonl").write_text("{}\n")
    dirs = [str(d) for d in eval_cli._discover_trace_dirs([str(src)], include_skill_runs=False)]
    assert not any("_runs" in d for d in dirs)
    runs = traces_cli._collect_runs([str(src)])
    stats = compute_baseline_stats(runs)
    assert all("chief-of-staff" not in k for k in stats)


def test_traces_list_still_shows_skill_runs(capsys: pytest.CaptureFixture[str]) -> None:
    main(["traces", "list", "--source", str(FIXTURE)])
    assert "chief-of-staff:radar" in capsys.readouterr().out
