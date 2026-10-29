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
         category="triage", tenant="t1", status="active", days_used=1, **extra) -> dict:
    d = {
        "schema": "nuvel.recipe_signal/2", "signal_id": f"s-{rid}-{at}-{uses}",
        "recipe_id": rid, "category": category, "status": status, "at": at,
        "uses": uses, "successes": successes, "corrections": corrections, "days_used": days_used,
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


def _recipe_doc(rid: str, version: int, category: str, title: str, with_text: bool = True) -> dict:
    doc = {
        "schema": "nuvel.recipe/3", "recipe_id": rid, "version": version, "category": category,
        "steps": [
            {"action": "search", "tool_kind": "email", "slots": ["person"]},
            {"action": "summarise", "tool_kind": "docs", "slots": ["person", "document"]},
            {"action": "draft", "tool_kind": "none", "slots": []},
        ],
        "stats": {"uses": 4, "successes": 3, "corrections": 1, "days_used": 2},
        "tenant": "secret-tenant",
    }
    if with_text:
        doc["text"] = {
            "language": "en", "title": title, "when_to_use": "when asked",
            "step_notes": ["Find {person}"], "narrative": "Asked {company}, then {?} replied.",
        }
        doc["masking"] = {"lexicon": "en-test.1", "masked": 1, "words": 4, "dropped": []}
    else:
        doc["masking"] = {"lexicon": None, "masked": 0, "words": 0, "dropped": []}
    return doc


def _write_recipe(root: Path, rid: str, version: int, category: str, title: str,
                  mtime: int | None = None, tenant_key="t1", with_text: bool = True) -> Path:
    d = root / "_recipes" / "recipes" / tenant_key / rid
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"v{version}.json"
    f.write_text(json.dumps(_recipe_doc(rid, version, category, title, with_text)))
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
    assert c.days_used == 1
    assert abs(c.success_rate - 2 / 3) < 1e-9


def test_recipes_view_latest_snapshot_wins(tmp_path: Path) -> None:
    _write_signals(tmp_path, [
        _sig(RID1, "2026-10-20T10:00:00Z", uses=2, successes=1, status="draft"),
        _sig(RID1, "2026-10-27T10:00:00Z", uses=5, successes=4, status="active", days_used=3),
    ])
    [c] = load_recipe_candidates([tmp_path])
    assert (c.uses, c.successes, c.days_used, c.recipes) == (5, 4, 3, 1)
    assert c.statuses == {"active": 1}
    # the order of the lines does not matter: the greatest `at` wins
    other = tmp_path / "o"
    _write_signals(other, [_sig(RID1, "2026-10-27T10:00:00Z", uses=5, status="retired"),
                           _sig(RID1, "2026-10-20T10:00:00Z", uses=2, status="draft")])
    [c] = load_recipe_candidates([other])
    assert (c.uses, c.statuses) == (5, {"retired": 1})


def test_recipes_view_ignores_non_v2_signals(tmp_path: Path) -> None:
    old = _sig(RID1, uses=9) | {"schema": "nuvel.recipe_signal/1", "event": "used"}
    del old["status"], old["days_used"]
    _write_signals(tmp_path, [old, _sig(RID2, uses=2), _sig(RID2, "2026-10-28T10:00:00Z", uses=1, status="paused")])
    [c] = load_recipe_candidates([tmp_path])
    assert (c.uses, c.recipes) == (2, 1)


def test_recipes_view_structure_only_recipe(tmp_path: Path) -> None:
    _write_signals(tmp_path, [_sig(RID1, uses=3)])
    _write_recipe(tmp_path, RID1, 1, "triage", "", with_text=False)
    [c] = load_recipe_candidates([tmp_path])
    latest = c.latest
    assert latest["has_text"] is False and latest["narrative"] == "" and latest["title"] == ""
    assert latest["mask_ratio"] is None
    assert [(s["action"], s["tool_kind"], s["slots"]) for s in latest["steps"]] == [
        ("search", "email", ["person"]), ("summarise", "docs", ["person", "document"]), ("draft", "none", [])]
    assert all(s["note"] == "" for s in latest["steps"])


def test_recipes_view_shows_text_when_present(tmp_path: Path) -> None:
    _write_signals(tmp_path, [_sig(RID1, uses=3)])
    _write_recipe(tmp_path, RID1, 1, "triage", "Triage")
    [c] = load_recipe_candidates([tmp_path])
    assert c.latest["has_text"] is True and c.latest["narrative"] == "Asked {company}, then {?} replied."


def test_recipes_view_fully_dropped_text(tmp_path: Path) -> None:
    f = _write_recipe(tmp_path, RID1, 1, "triage", "", with_text=False)
    doc = json.loads(f.read_text())
    doc["masking"] = {"lexicon": "es-test.1", "masked": 0, "words": 0, "dropped": ["/text/title"]}
    f.write_text(json.dumps(doc))
    [c] = load_recipe_candidates([tmp_path])
    assert c.latest["has_text"] is False and c.latest["mask_ratio"] is None


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
    assert c.latest["steps"][1]["note"] == ""
    assert c.latest["narrative"] == "Asked {company}, then {?} replied."
    assert c.latest["mask_ratio"] == 0.25
    assert "install_id" not in c.latest


def test_mask_ratio_none_when_no_words(tmp_path: Path) -> None:
    f = _write_recipe(tmp_path, RID1, 1, "triage", "T")
    doc = json.loads(f.read_text())
    doc["masking"] = {"lexicon": "en", "masked": 0, "words": 0, "dropped": []}
    f.write_text(json.dumps(doc))
    [c] = load_recipe_candidates([tmp_path])
    assert c.latest["mask_ratio"] is None


def test_old_schema_files_skipped(tmp_path: Path) -> None:
    f = _write_recipe(tmp_path, RID1, 1, "triage", "Old")
    doc = json.loads(f.read_text())
    doc["schema"] = "nuvel.recipe/2"
    f.write_text(json.dumps(doc))
    _write_signals(tmp_path, [_sig(RID1, uses=2)])
    cands = load_recipe_candidates([tmp_path])
    assert cands.skipped_old == 1
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


def test_cli_recipes_empty_title_reads_masked(tmp_path: Path, monkeypatch, capsys) -> None:
    from nuvel.cli import main

    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(tmp_path / "empty"))
    _write_signals(tmp_path / "src", [_sig(RID1, uses=2)])
    _write_recipe(tmp_path / "src", RID1, 1, "triage", "")
    main(["traces", "recipes", "--source", str(tmp_path / "src")])
    assert "(title masked)" in capsys.readouterr().out


def test_cli_recipes_structure_only_label(tmp_path: Path, monkeypatch, capsys) -> None:
    from nuvel.cli import main

    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(tmp_path / "empty"))
    _write_signals(tmp_path / "src", [_sig(RID1, uses=2)])
    _write_recipe(tmp_path / "src", RID1, 1, "triage", "", with_text=False)
    main(["traces", "recipes", "--source", str(tmp_path / "src")])
    out = capsys.readouterr().out
    assert "(structure only)" in out and "None" not in out
