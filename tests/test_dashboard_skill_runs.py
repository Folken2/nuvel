"""Dashboard rendering of nuvel.skill_run/1 traces."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from nuvel.dashboard.app import _run_has_error, build_app
from nuvel.dashboard.loader import TraceLoader

FIXTURE = Path(__file__).parent / "fixtures" / "traces_mixed"


def _skill_run(outcome: str, step_status: str) -> dict:
    return {
        "schema": "nuvel.skill_run/1",
        "run_id": f"run-{outcome}",
        "plugin": {"name": "chief-of-staff", "version": "0.2.0"},
        "skill": {"name": "radar", "version": "0.2.0"},
        "started_at": "2026-10-21T08:00:00Z",
        "duration_s": 5,
        "outcome": outcome,
        "steps": [{"id": "radar.2.email", "status": step_status}],
        "tools": [{"name": "gmail.search_threads", "calls": 2, "errors": 1}],
        "errors": [],
    }


def _loader(root: Path) -> TraceLoader:
    # `nuvel dashboard` hands the loader the discovered dirs, `_runs/*` included.
    from nuvel.traces_cli import _discover_trace_dirs

    return TraceLoader(_discover_trace_dirs([str(root)]))


def _client(root: Path) -> TestClient:
    return TestClient(build_app(_loader(root)))


def test_home_lists_skill_runs_and_step_table(tmp_path: Path) -> None:
    root = tmp_path / "traces"
    shutil.copytree(FIXTURE, root)
    d = root / "_runs" / "chief-of-staff.radar"
    (d / "2026-10-21.jsonl").write_text(json.dumps(_skill_run("failure", "error")) + "\n")
    html = _client(root).get("/").text
    assert "chief-of-staff:radar" in html
    assert "Skill steps" in html
    assert "radar.2.email" in html
    assert 'class="pill err"' in html


def test_run_has_error_for_failure_and_blocked_only(tmp_path: Path) -> None:
    root = tmp_path / "traces"
    d = root / "_runs" / "chief-of-staff.radar"
    d.mkdir(parents=True)
    lines = [_skill_run(o, "ok") for o in ("failure", "blocked", "success")]
    (d / "2026-10-21.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    runs = _loader(root).runs()
    flags = {r.subtype: _run_has_error(r) for r in runs}
    assert flags == {"failure": True, "blocked": True, "success": False}


def test_no_skill_steps_heading_without_skill_runs(tmp_path: Path) -> None:
    root = tmp_path / "traces"
    root.mkdir()
    (root / "a.jsonl").write_text(
        json.dumps({"trace_id": "t1", "session_id": "s1", "event": "run_start", "agent": "a"}) + "\n"
    )
    assert "Skill steps" not in _client(root).get("/").text
