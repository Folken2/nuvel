"""normalize(), LocalTraceStore and load_trace_store."""

import json
import sys
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nuvel_mcp.traces import LocalTraceStore, load_trace_store, normalize

FIXTURE = Path(__file__).parent / "fixtures" / "skill_run_example.json"


@pytest.fixture
def example():
    return json.loads(FIXTURE.read_text())


# --- normalize ---------------------------------------------------------------


def test_normalize_truncates_to_minute_in_utc(example):
    t = normalize({**example, "started_at": "2026-10-20T23:59:40.123+02:00"}, tenant=None)
    assert t["started_at"] == "2026-10-20T21:59:00Z"
    assert "tenant" not in t


def test_normalize_stamps_tenant_and_does_not_mutate(example):
    original = json.loads(json.dumps(example))
    t = normalize(example, tenant="acme")
    assert t["tenant"] == "acme"
    assert example == original
    t["steps"][0]["status"] = "error"
    assert example["steps"][0]["status"] == "ok"


# --- LocalTraceStore ---------------------------------------------------------


def test_append_writes_utc_day_file(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    t = normalize({**example, "started_at": "2026-10-20T23:59:40+02:00"}, tenant=None)
    assert store.append(t) is True
    f = tmp_path / "_runs" / "chief-of-staff.radar" / "2026-10-20.jsonl"
    assert json.loads(f.read_text())["started_at"] == "2026-10-20T21:59:00Z"


def test_append_uses_utc_date_not_local_date(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    store.append(normalize({**example, "started_at": "2026-10-21T00:30:00+02:00"}, None))
    assert (tmp_path / "_runs" / "chief-of-staff.radar" / "2026-10-20.jsonl").is_file()


def test_duplicate_run_id_ignored(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    t = normalize(example, None)
    assert store.append(t) is True
    assert store.append(t) is False
    f = tmp_path / "_runs" / "chief-of-staff.radar" / "2026-10-20.jsonl"
    assert len(f.read_text().splitlines()) == 1


def test_concurrent_appends_stay_line_atomic(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    traces = [normalize({**example, "run_id": str(uuid.uuid4())}, None) for _ in range(50)]
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(store.append, traces))
    assert all(results)
    lines = next((tmp_path / "_runs").rglob("*.jsonl")).read_text().splitlines()
    assert len(lines) == 50 and all(json.loads(l) for l in lines)


def test_concurrent_duplicate_appends_record_once(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    t = normalize(example, None)
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(store.append, [t] * 20))
    assert results.count(True) == 1
    lines = next((tmp_path / "_runs").rglob("*.jsonl")).read_text().splitlines()
    assert len(lines) == 1


def _run(example, started_at, tenant=None, skill="radar"):
    return normalize(
        {**example, "run_id": str(uuid.uuid4()), "started_at": started_at,
         "skill": {"name": skill, "version": "0.2.0"}},
        tenant,
    )


def test_query_filters_by_since_skill_and_tenant(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    old = _run(example, "2026-10-01T10:00:00Z", tenant="acme")
    new_acme = _run(example, "2026-10-20T10:00:00Z", tenant="acme")
    new_other = _run(example, "2026-10-20T11:00:00Z", tenant="other")
    other_skill = _run(example, "2026-10-20T10:00:00Z", tenant="acme", skill="digest")
    for t in (old, new_acme, new_other, other_skill):
        store.append(t)
    since = datetime(2026, 10, 10, tzinfo=timezone.utc)

    all_runs = store.query("chief-of-staff", "radar", since, None)
    assert {r["run_id"] for r in all_runs} == {new_acme["run_id"], new_other["run_id"]}

    acme = store.query("chief-of-staff", "radar", since, "acme")
    assert [r["run_id"] for r in acme] == [new_acme["run_id"]]


def test_query_since_within_a_day(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    early = _run(example, "2026-10-20T08:00:00Z")
    late = _run(example, "2026-10-20T18:00:00Z")
    store.append(early)
    store.append(late)
    since = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    assert [r["run_id"] for r in store.query("chief-of-staff", "radar", since, None)] == [late["run_id"]]


def test_query_naive_since_is_utc(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    t = _run(example, "2026-10-20T18:00:00Z")
    store.append(t)
    assert store.query("chief-of-staff", "radar", datetime(2026, 10, 20, 17), None)


def test_query_missing_or_unsafe_names_return_empty(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert store.query("chief-of-staff", "radar", since, None) == []
    assert store.query("../etc", "radar", since, None) == []
    assert store.query("chief-of-staff", "radar\n", since, None) == []


# --- load_trace_store --------------------------------------------------------


def test_load_local(tmp_path):
    store = load_trace_store("local", tmp_path)
    assert isinstance(store, LocalTraceStore)


def test_load_module_factory(tmp_path, monkeypatch):
    mod = types.ModuleType("fake_trace_backend")
    sentinel = object()
    mod.make = lambda: sentinel
    monkeypatch.setitem(sys.modules, "fake_trace_backend", mod)
    assert load_trace_store("fake_trace_backend:make", tmp_path) is sentinel


@pytest.mark.parametrize("spec", ["", "nope", "fake_trace_backend:", ":make", "no_such_module_xyz:make"])
def test_load_bad_spec_raises(tmp_path, spec):
    with pytest.raises(ValueError):
        load_trace_store(spec, tmp_path)


def test_load_missing_factory_raises(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "fake_trace_backend", types.ModuleType("fake_trace_backend"))
    with pytest.raises(ValueError):
        load_trace_store("fake_trace_backend:make", tmp_path)
