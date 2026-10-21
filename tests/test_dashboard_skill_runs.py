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


def test_malicious_step_id_is_escaped(tmp_path: Path) -> None:
    root = tmp_path / "traces"
    d = root / "_runs" / "chief-of-staff.radar"
    d.mkdir(parents=True)
    rec = _skill_run("failure", "error")
    rec["steps"][0]["id"] = "<script>alert(1)</script>"
    (d / "2026-10-21.jsonl").write_text(json.dumps(rec) + "\n")
    html = _client(root).get("/").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_skill_steps_table_is_capped_at_50(tmp_path: Path) -> None:
    root = tmp_path / "traces"
    d = root / "_runs" / "chief-of-staff.radar"
    d.mkdir(parents=True)
    rec = _skill_run("success", "ok")
    rec["steps"] = [{"id": f"step.{i:03d}", "status": "error" if i < 5 else "ok"} for i in range(60)]
    (d / "2026-10-21.jsonl").write_text(json.dumps(rec) + "\n")
    html = _client(root).get("/").text
    assert html.count("<code>step.") == 50
    assert "+10 more" in html
    assert "step.000" in html and "step.059" not in html


def test_same_run_id_in_two_tenants_both_reachable(tmp_path: Path) -> None:
    from urllib.parse import quote

    root = tmp_path / "traces"
    d = root / "_runs" / "chief-of-staff.radar"
    d.mkdir(parents=True)
    recs = []
    for tenant, outcome in (("acme", "failure"), ("globex/eu", "success"), (None, "blocked")):
        r = _skill_run(outcome, "ok")
        r["run_id"] = "dup"
        if tenant:
            r["tenant"] = tenant
        recs.append(r)
    (d / "2026-10-21.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    loader = _loader(root)
    runs = loader.runs()
    assert len(runs) == 3
    client = TestClient(build_app(loader))
    home = client.get("/").text
    seen = set()
    for run in runs:
        assert loader.find_by_id(run.trace_id).subtype == run.subtype
        href = "/run/" + quote(run.trace_id, safe="")
        assert f'href="{href}"' in home
        resp = client.get(href)
        assert resp.status_code == 200
        seen.add(run.subtype)
    assert seen == {"failure", "success", "blocked"}


def test_loader_and_watcher_pick_up_new_plugin_skill_dir(tmp_path: Path) -> None:
    import asyncio

    from nuvel.dashboard.watcher import RunWatcher
    from nuvel.traces_cli import _discover_trace_dirs

    root = tmp_path / "traces"
    (root / "_runs" / "chief-of-staff.radar").mkdir(parents=True)
    sources = _discover_trace_dirs([str(root)])
    loader = TraceLoader(sources, skill_roots=[root])
    assert loader.runs() == []
    new = root / "_runs" / "chief-of-staff.inbox"
    rec = _skill_run("success", "ok")
    rec["run_id"] = "late"
    new.mkdir()
    (new / "2026-10-21.jsonl").write_text(json.dumps(rec) + "\n")
    assert [r.session_id for r in loader.runs()] == ["late"]

    async def go() -> str:
        w = RunWatcher(sources, poll_seconds=0.1, skill_roots=[root])
        q: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(w.run(q))
        await asyncio.sleep(0.3)
        late = root / "_runs" / "chief-of-staff.later"
        late.mkdir()
        r2 = _skill_run("success", "ok")
        r2["run_id"] = "after-start"
        (late / "2026-10-21.jsonl").write_text(json.dumps(r2) + "\n")
        got = await asyncio.wait_for(q.get(), timeout=3.0)
        w.stop()
        await asyncio.wait_for(task, timeout=2.0)
        return got.session_id

    assert asyncio.run(go()) == "after-start"


def test_home_request_walks_each_skill_root_at_most_once(tmp_path: Path, monkeypatch) -> None:
    from nuvel import traces_cli

    root = tmp_path / "traces"
    d = root / "_runs" / "chief-of-staff.radar"
    d.mkdir(parents=True)
    (d / "2026-10-21.jsonl").write_text(json.dumps(_skill_run("success", "ok")) + "\n")
    calls: list[Path] = []
    real = traces_cli._walk_skill_run_dirs
    monkeypatch.setattr(traces_cli, "_walk_skill_run_dirs",
                        lambda r: (calls.append(r), real(r))[1])
    client = TestClient(build_app(TraceLoader([], skill_roots=[root])))
    assert "chief-of-staff:radar" in client.get("/").text
    assert len(calls) == 1
    client.get("/")  # nothing changed: no re-walk
    assert len(calls) == 1
    late = root / "_runs" / "chief-of-staff.inbox"
    late.mkdir()
    rec = _skill_run("success", "ok")
    rec["skill"]["name"] = "inbox"
    (late / "2026-10-21.jsonl").write_text(json.dumps(rec) + "\n")
    assert "chief-of-staff:inbox" in client.get("/").text
    assert len(calls) == 2
