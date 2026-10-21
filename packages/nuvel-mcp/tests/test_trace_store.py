"""normalize(), LocalTraceStore and load_trace_store."""

import json
import sys
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from nuvel_mcp import traces as traces_mod
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


DAY = Path("_runs") / "chief-of-staff.radar" / "2026-10-20.jsonl"


@pytest.mark.parametrize("first,second,stored", [
    ("acme", "other", True),   # another tenant's identical run_id is stored
    ("acme", "acme", False),   # same tenant's repeat is a duplicate
    (None, "acme", True),      # no tenant is its own key
    ("acme", None, True),
    (None, None, False),
])
def test_dedupe_keys_on_tenant_and_run_id(tmp_path, example, first, second, stored):
    store = LocalTraceStore(tmp_path)
    assert store.append(normalize(example, first)) is True
    assert store.append(normalize(example, second)) is stored
    assert len((tmp_path / DAY).read_text().splitlines()) == (2 if stored else 1)


def test_dedupe_window_is_the_utc_day_file(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    assert store.append(normalize({**example, "started_at": "2026-10-20T10:00:00Z"}, None)) is True
    assert store.append(normalize({**example, "started_at": "2026-10-21T10:00:00Z"}, None)) is True


def test_append_reads_only_new_bytes(tmp_path, example, monkeypatch):
    store = LocalTraceStore(tmp_path)
    offsets = []
    real = traces_mod._read_complete_lines

    def spy(path, offset):
        offsets.append(offset)
        return real(path, offset)

    monkeypatch.setattr(traces_mod, "_read_complete_lines", spy)
    monkeypatch.setattr(traces_mod, "_read_jsonl", lambda p: pytest.fail("append rescanned the file"))
    sizes_before = []
    for _ in range(5):
        path = tmp_path / DAY
        sizes_before.append(path.stat().st_size if path.exists() else 0)
        store.append(normalize({**example, "run_id": str(uuid.uuid4())}, None))
    # Each append reads from where the previous append's own line began: one
    # line, never the whole file (the first append has no file to read).
    assert offsets == sizes_before[:-1] and offsets[1:] and all(offsets[1:])


def test_index_sees_lines_appended_by_another_process(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    assert store.append(normalize({**example, "run_id": str(uuid.uuid4())}, None)) is True
    foreign = normalize(example, "acme")
    with (tmp_path / DAY).open("a", encoding="utf-8") as fh:  # what another process does
        fh.write(json.dumps(foreign) + "\n")
    assert store.append(foreign) is False
    assert store.append(normalize(example, "other")) is True


def test_index_waits_for_a_partial_trailing_line_to_complete(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    store.append(normalize({**example, "run_id": str(uuid.uuid4())}, None))
    foreign = json.dumps(normalize(example, None))
    path = tmp_path / DAY
    with path.open("a", encoding="utf-8") as fh:  # another process, mid-write
        fh.write(foreign[:20])
    # The index is read while the line is still partial: it must stop before it.
    keys = store._keys_for(path.resolve())
    assert (None, example["run_id"]) not in keys
    assert store._index[path.resolve()].offset < path.stat().st_size
    with path.open("a", encoding="utf-8") as fh:  # ... and the write completes
        fh.write(foreign[20:] + "\n")
    assert store.append(normalize(example, None)) is False


def test_index_does_not_consume_a_partial_line(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    store.append(normalize({**example, "run_id": str(uuid.uuid4())}, None))
    path = tmp_path / DAY
    complete = path.stat().st_size
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"run_id": "half')
    entries, consumed = traces_mod._read_complete_lines(path, complete)
    assert entries == [] and consumed == 0


def test_index_skips_malformed_lines(tmp_path, example):
    path = tmp_path / DAY
    path.parent.mkdir(parents=True)
    path.write_text('not json\n[1]\n{"run_id": ["x"]}\n{"run_id": "a", "tenant": {"x": 1}}\n\xff\n',
                    encoding="latin-1")
    store = LocalTraceStore(tmp_path)
    assert store.append(normalize(example, None)) is True
    assert store.append(normalize(example, None)) is False


def test_index_rebuilds_when_the_file_is_replaced(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    kept = normalize({**example, "run_id": str(uuid.uuid4())}, None)
    dropped = normalize({**example, "run_id": str(uuid.uuid4())}, None)
    store.append(kept)
    store.append(dropped)
    path = tmp_path / DAY
    replacement = path.with_suffix(".tmp")
    replacement.write_text(json.dumps(kept) + "\n", encoding="utf-8")
    replacement.replace(path)  # rotated / compacted by another process
    assert store.append(dropped) is True
    assert store.append(kept) is False


def _with_id(example, run_id):
    return normalize({**example, "run_id": run_id}, None)


def _line(trace):
    return json.dumps(trace, ensure_ascii=False, separators=(",", ":")) + "\n"


def test_index_rebuilds_when_the_file_is_rewritten_in_place_larger(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    first, second, later = (_with_id(example, c * 36) for c in "abc")
    store.append(first)
    store.append(second)  # the index now trusts an offset past the first line
    path = tmp_path / DAY
    inode = path.stat().st_ino
    # Same inode, larger size; the bytes before the old offset changed.
    with path.open("r+", encoding="utf-8") as fh:
        fh.write(_line(later) + _line(first) + _line(second))
    assert path.stat().st_ino == inode
    assert store.append(later) is False


def test_index_rebuilds_when_the_file_is_rewritten_in_place_same_size(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    first, second, other = (_with_id(example, c * 36) for c in "abc")
    store.append(first)
    store.append(second)
    assert store.append(second) is False  # the index has read the whole file
    path = tmp_path / DAY
    size, inode = path.stat().st_size, path.stat().st_ino
    path.write_text(_line(other) + _line(second), encoding="utf-8")
    assert (path.stat().st_size, path.stat().st_ino) == (size, inode)
    assert store.append(other) is False
    assert store.append(first) is True


def test_index_rebuilds_when_the_file_shrinks(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    t = normalize(example, None)
    store.append(t)
    (tmp_path / DAY).write_text("", encoding="utf-8")  # truncated in place
    assert store.append(t) is True


def test_index_is_bounded_and_evicted_entries_reload_from_disk(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    first = normalize({**example, "started_at": "2026-01-01T10:00:00Z"}, None)
    store.append(first)
    for i in range(LocalTraceStore.MAX_INDEXED_FILES + 10):
        day = date(2026, 3, 1) + timedelta(days=i)
        store.append(normalize({**example, "run_id": str(uuid.uuid4()),
                                "started_at": f"{day.isoformat()}T10:00:00Z"}, None))
    assert len(store._index) <= LocalTraceStore.MAX_INDEXED_FILES
    assert store.append(first) is False


def test_lock_registry_is_bounded(tmp_path):
    store = LocalTraceStore(tmp_path)
    paths = [tmp_path / f"{i}.jsonl" for i in range(1000)]
    locks = {id(store._lock_for(p)) for p in paths}
    assert len(locks) <= LocalTraceStore.LOCK_STRIPES
    assert store._lock_for(paths[0]) is store._lock_for(Path(str(paths[0])))


def test_concurrent_mixed_appends_keep_index_exact(tmp_path, example):
    run_ids = [str(uuid.uuid4()) for _ in range(10)]
    tenants = [None, "acme", "other"]
    work = [normalize({**example, "run_id": r}, t) for r in run_ids for t in tenants] * 4
    stores = [LocalTraceStore(tmp_path) for _ in range(3)]  # separate indexes, shared file
    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(lambda ix: stores[ix[0] % 3].append(ix[1]), enumerate(work)))
    assert results.count(True) == len(run_ids) * len(tenants)
    lines = [json.loads(l) for l in (tmp_path / DAY).read_text().splitlines()]
    keys = [(l.get("tenant"), l["run_id"]) for l in lines]
    assert len(keys) == len(set(keys)) == len(run_ids) * len(tenants)


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


def test_query_separates_dotted_names_sharing_a_directory(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    ab_c = {**_run(example, "2026-10-20T10:00:00Z", skill="c"),
            "plugin": {"name": "a.b", "version": "0.2.0"}}
    a_bc = {**_run(example, "2026-10-20T11:00:00Z", skill="b.c"),
            "plugin": {"name": "a", "version": "0.2.0"}}
    store.append(ab_c)
    store.append(a_bc)
    assert (tmp_path / "_runs" / "a.b.c").is_dir()  # both share it
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert [r["run_id"] for r in store.query("a.b", "c", since, None)] == [ab_c["run_id"]]
    assert [r["run_id"] for r in store.query("a", "b.c", since, None)] == [a_bc["run_id"]]


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
