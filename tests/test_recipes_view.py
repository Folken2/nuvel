"""Recipe candidates view over the MCP recipe store layout."""

from __future__ import annotations

import json
import os
from pathlib import Path

from nuvel.recipes_view import load_recipe_candidates
from nuvel.traces_cli import _discover_trace_dirs  # noqa: F401  (import sanity)

RID1 = "11111111-1111-4111-8111-111111111111"
RID2 = "22222222-2222-4222-8222-222222222222"


def _sig(rid="x", at="2026-10-27T10:00:00Z", uses=1, successes=0, corrections=0,
         category="triage", tenant="t1", **extra) -> dict:
    d = {
        "schema": "nuvel.recipe_signal/1", "signal_id": f"s-{rid}-{at}-{uses}",
        "recipe_id": rid, "category": category, "event": "used", "at": at,
        "uses": uses, "successes": successes, "corrections": corrections,
    }
    if tenant is not None:
        d["tenant"] = tenant
    d.update(extra)
    return d


def _write_signals(root: Path, lines: list, day="2026-10-27") -> None:
    d = root / "_recipes" / "signals"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{day}.jsonl").open("a") as f:
        for ln in lines:
            f.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")


def _write_recipe(root: Path, rid: str, version: int, category: str, title: str,
                  mtime: int | None = None, tenant_key="t1") -> Path:
    d = root / "_recipes" / "recipes" / tenant_key / rid
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"v{version}.json"
    f.write_text(json.dumps({
        "schema": "nuvel.recipe/2", "recipe_id": rid, "version": version,
        "category": category, "title": title, "when_to_use": "when asked",
        "steps": [
            {"action": "search", "tool_kind": "email", "slots": ["person"], "note": "Find {person}"},
            {"action": "summarise", "tool_kind": "docs", "slots": ["person", "document"]},
            {"action": "draft", "tool_kind": "none", "slots": []},
        ],
        "narrative": "Asked {company}, then {?} replied.",
        "masking": {"lexicon": "en", "masked": 1, "words": 4, "dropped": 0},
        "tenant": "secret-tenant",
    }))
    if mtime is not None:
        os.utime(f, (mtime, mtime))
    return f


def test_counters_use_latest_signal_per_recipe(tmp_path: Path) -> None:
    _write_signals(tmp_path, [
        _sig(RID1, "2026-10-27T10:00:00Z", uses=1, successes=1),
        _sig(RID1, "2026-10-27T11:00:00Z", uses=3, successes=2, corrections=1),
    ])
    [c] = load_recipe_candidates([tmp_path])
    assert (c.uses, c.successes, c.corrections, c.recipes, c.tenants) == (3, 2, 1, 1, 1)
    assert abs(c.success_rate - 2 / 3) < 1e-9


def test_tie_on_at_last_line_wins_and_sums_across_recipes_and_tenants(tmp_path: Path) -> None:
    _write_signals(tmp_path, [
        _sig(RID1, uses=1), _sig(RID1, uses=2),
        _sig(RID1, uses=5, tenant="t2"),
        _sig(RID2, uses=4, successes=4, tenant="t2"),
    ])
    [c] = load_recipe_candidates([tmp_path])
    assert c.uses == 2 + 5 + 4
    assert (c.recipes, c.tenants) == (3, 2)


def test_duplicate_signal_lines_not_double_counted(tmp_path: Path) -> None:
    line = json.dumps(_sig(RID1, uses=3, successes=3))
    _write_signals(tmp_path, [line, line])
    [c] = load_recipe_candidates([tmp_path])
    assert (c.uses, c.recipes) == (3, 1)


def test_groups_unknown_category_and_skips_malformed(tmp_path: Path) -> None:
    s = _sig(RID1, uses=2)
    del s["category"]
    _write_signals(tmp_path, ["not json", "[1]", json.dumps({"recipe_id": RID1}), s,
                              json.dumps(_sig(RID2, uses="x"))])
    (tmp_path / "_recipes" / "signals" / "bad.jsonl").write_bytes(b"\xff\xfe\n")
    [c] = load_recipe_candidates([tmp_path])
    assert c.category == "unknown" and c.uses == 2 and c.latest is None
    assert c.success_rate == 0.0


def test_zero_uses_rate_and_sort_by_uses(tmp_path: Path) -> None:
    _write_signals(tmp_path, [
        _sig(RID1, category="a", uses=0),
        _sig(RID2, category="b", uses=5, successes=5),
    ])
    cands = load_recipe_candidates([tmp_path])
    assert [c.category for c in cands] == ["b", "a"]
    assert cands[1].success_rate == 0.0


def test_latest_is_newest_mtime_and_tenant_stripped(tmp_path: Path) -> None:
    _write_signals(tmp_path, [_sig(RID1, uses=3)])
    _write_recipe(tmp_path, RID1, 2, "triage", "Newer", mtime=2_000_000_000)
    _write_recipe(tmp_path, RID2, 9, "triage", "Older", mtime=1_000_000_000, tenant_key="t2")
    [c] = load_recipe_candidates([tmp_path])
    assert c.latest["title"] == "Newer"
    assert "tenant" not in c.latest


def test_finds_recipes_dir_nested_or_direct_and_dedupes(tmp_path: Path) -> None:
    _write_signals(tmp_path / "a" / "b", [_sig(RID1, uses=3)])
    roots = [tmp_path, tmp_path / "a" / "b", tmp_path / "a" / "b" / "_recipes"]
    [c] = load_recipe_candidates(roots)
    assert c.uses == 3


def test_missing_root_is_empty(tmp_path: Path) -> None:
    assert load_recipe_candidates([tmp_path / "nope"]) == []


def test_cli_recipes_lists_categories(tmp_path: Path, monkeypatch, capsys) -> None:
    from nuvel.cli import main

    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(tmp_path / "empty"))
    _write_signals(tmp_path / "src", [_sig(RID1, uses=7, successes=7, tenant="tenant-secret")])
    _write_recipe(tmp_path / "src", RID1, 1, "triage", "Triage mail", tenant_key="tenant-secret")
    rc = main(["traces", "recipes", "--source", str(tmp_path / "src")])
    out = capsys.readouterr().out
    assert rc in (0, None)
    assert "triage" in out and "7" in out and "Triage mail" in out
    assert "search → summarise → draft" in out
    assert "tenant-secret" not in out and "secret-tenant" not in out


def test_latest_has_structure_and_narrative(tmp_path: Path) -> None:
    _write_signals(tmp_path, [_sig(RID1, uses=3)])
    _write_recipe(tmp_path, RID1, 1, "triage", "T")
    [c] = load_recipe_candidates([tmp_path])
    assert c.latest["actions"] == ["search", "summarise", "draft"]
    assert c.latest["slots"] == ["person", "document"]
    assert c.latest["steps"][0]["note"] == "Find {person}"
    assert c.latest["narrative"] == "Asked {company}, then {?} replied."
    assert c.latest["mask_ratio"] == 0.25
    assert "install_id" not in c.latest


def test_mask_ratio_none_when_no_words(tmp_path: Path) -> None:
    f = _write_recipe(tmp_path, RID1, 1, "triage", "T")
    doc = json.loads(f.read_text())
    doc["masking"] = {"lexicon": "en", "masked": 0, "words": 0, "dropped": 0}
    f.write_text(json.dumps(doc))
    [c] = load_recipe_candidates([tmp_path])
    assert c.latest["mask_ratio"] is None


def test_v1_files_skipped(tmp_path: Path) -> None:
    f = _write_recipe(tmp_path, RID1, 1, "triage", "Old")
    doc = json.loads(f.read_text())
    doc["schema"] = "nuvel.recipe/1"
    f.write_text(json.dumps(doc))
    _write_signals(tmp_path, [_sig(RID1, uses=2)])
    cands = load_recipe_candidates([tmp_path])
    assert cands.skipped_v1 == 1
    assert cands[0].latest is None and cands[0].uses == 2


def test_non_list_steps_do_not_500(tmp_path: Path) -> None:
    for bad in ("oops", {"a": 1}, None, 7):
        f = _write_recipe(tmp_path, RID1, 1, "triage", "Bad")
        doc = json.loads(f.read_text())
        doc["steps"] = bad
        f.write_text(json.dumps(doc))
        _write_signals(tmp_path, [_sig(RID1, uses=1)])
        cands = load_recipe_candidates([tmp_path])
        assert cands[0].latest is None
