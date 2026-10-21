"""Skill-run discovery: pruning, caching, labels, tenant keys, eval isolation."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nuvel import traces_cli
from nuvel.cli import main
from nuvel.eval.schema import SCORER_VERSION, ScoredRun

FIXTURE = Path(__file__).parent / "fixtures" / "traces_mixed"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(tmp_path / "no-feedback"))
    monkeypatch.delenv("TRACE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)


def _rec(run_id: str, tenant: str | None = None, outcome: str = "success") -> dict:
    rec = {
        "schema": "nuvel.skill_run/1",
        "run_id": run_id,
        "plugin": {"name": "p", "version": "1"},
        "skill": {"name": "s", "version": "1"},
        "started_at": "2026-10-21T08:00:00Z",
        "duration_s": 1,
        "outcome": outcome,
        "steps": [],
        "tools": [],
        "errors": [],
    }
    if tenant is not None:
        rec["tenant"] = tenant
    return rec


def _write(path: Path, *recs: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def test_walk_prunes_vendored_and_hidden_dirs(tmp_path: Path) -> None:
    root = tmp_path / "src"
    for junk in (".git", "node_modules", ".venv", "venv", "__pycache__", ".hidden"):
        (root / junk / "_runs" / "p.s").mkdir(parents=True)
    (root / "_runs" / "p.s").mkdir(parents=True)
    got = traces_cli._skill_run_dirs(root)
    assert got == [root / "_runs", root / "_runs" / "p.s"]


def test_discovery_cache_walks_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "src"
    (root / "_runs" / "p.s").mkdir(parents=True)
    calls = []
    real = os.walk
    monkeypatch.setattr(traces_cli.os, "walk", lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    with traces_cli.discovery_cache():
        traces_cli._discover_trace_dirs([str(root)])
        traces_cli._discover_trace_dirs([str(root)])
    assert len(calls) == 1
    traces_cli._discover_trace_dirs([str(root)])  # no cache outside the context
    assert len(calls) == 2


def test_eval_root_excludes_source_inside_runs_tree(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src / "_runs" / "p.s" / "2026-10-21.jsonl", _rec("r1"))
    assert traces_cli._discover_trace_dirs([str(src)], include_skill_runs=False) == [src]
    for s in (src / "_runs", src / "_runs" / "p.s"):
        assert traces_cli._discover_trace_dirs([str(s)], include_skill_runs=False) == [], s
    # ...while `traces` still reads them.
    assert traces_cli._collect_runs([str(src / "_runs" / "p.s")])


def test_agent_label_for_skill_run_file_is_plugin_skill(tmp_path: Path) -> None:
    f = tmp_path / "_runs" / "chief-of-staff.radar" / "2026-10-21.jsonl"
    assert traces_cli._agent_label_for(f) == "chief-of-staff:radar"
    assert traces_cli._agent_label_for(tmp_path / "traces" / "a.jsonl") == "(local)"


def test_same_run_id_different_tenants_are_distinct_runs(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _write(
        root / "_runs" / "p.s" / "2026-10-21.jsonl",
        _rec("dup", "acme"),
        _rec("dup", "globex"),
        _rec("dup"),
        _rec("dup", "acme"),  # true duplicate of the first: last wins
    )
    runs = traces_cli._collect_runs([str(root)])
    assert len(runs) == 3
    assert len({r.key for r in runs}) == 3
    assert {r.tenant for r in runs} == {"acme", "globex", None}
    assert all(r.session_id == "dup" for r in runs)


def _scored(trace_id: str, agent: str) -> str:
    return ScoredRun(
        trace_id=trace_id, agent=agent, scored_at="2026-10-21T08:00:00Z",
        scorer_version=SCORER_VERSION, rubric_version="1", overall=0.5,
        components={"success": 0.5},
    ).to_json_line() + "\n"


def test_eval_report_and_drift_ignore_skill_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "src"
    (src).mkdir()
    (src / "scored.jsonl").write_text(_scored("t-real", "real-agent"))
    d = src / "_runs" / "p.s"
    _write(d / "2026-10-21.jsonl", _rec("r1"))
    (d / "scored.jsonl").write_text(_scored("r1", "p:s"))
    fb = tmp_path / "fb"
    (fb / "_runs" / "p.s").mkdir(parents=True)
    (fb / "_runs" / "p.s" / "scored.jsonl").write_text(_scored("r2", "p:s"))
    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(fb))

    for argv in (
        ["eval", "report", "--source", str(src)],
        ["eval", "report", "--source", str(d)],
        ["eval", "worst", "--source", str(src)],
    ):
        main(argv)
        out = capsys.readouterr().out
        assert "p:s" not in out, argv
    main(["eval", "report", "--source", str(src)])
    assert "real-agent" in capsys.readouterr().out

    rc = main(["eval", "drift", "--source", str(src)])
    assert "p:s" not in capsys.readouterr().out
    assert rc in (0, None)
