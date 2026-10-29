"""Dashboard /recipes page."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from nuvel.dashboard.app import build_app
from nuvel.dashboard.loader import TraceLoader

RID = "11111111-1111-4111-8111-111111111111"


def _setup(root: Path, title: str, narrative: str = "Met {?} about {company}.") -> None:
    sd = root / "_recipes" / "signals"
    sd.mkdir(parents=True)
    (sd / "2026-10-27.jsonl").write_text(json.dumps({
        "schema": "nuvel.recipe_signal/2", "signal_id": "s1", "recipe_id": RID,
        "category": "triage", "status": "active", "at": "2026-10-27T10:00:00Z",
        "uses": 4, "successes": 3, "corrections": 1, "days_used": 2, "tenant": "tenant-secret",
    }) + "\n")
    rd = root / "_recipes" / "recipes" / "tenant-secret" / RID
    rd.mkdir(parents=True)
    (rd / "v1.json").write_text(json.dumps({
        "schema": "nuvel.recipe/3", "recipe_id": RID, "version": 2, "category": "triage",
        "steps": [
            {"action": "search", "tool_kind": "email", "slots": ["person"]},
            {"action": "summarise", "tool_kind": "docs", "slots": []},
            {"action": "draft", "tool_kind": "none", "slots": []},
        ],
        "stats": {"uses": 4, "successes": 3, "corrections": 1, "days_used": 2},
        "text": {
            "language": "en", "title": title, "when_to_use": "<b>soon</b>",
            "step_notes": ["Read <i>it</i> for {person}"],
            "pitfalls": ["<img src=x>"], "narrative": narrative,
        },
        "masking": {"lexicon": "en-test.1", "masked": 1, "words": 4, "dropped": []},
        "tenant": "tenant-secret",
    }))


def test_dashboard_recipes_page(tmp_path: Path) -> None:
    _setup(tmp_path, "<script>alert(1)</script>Triage")
    client = TestClient(build_app(TraceLoader([], skill_roots=[tmp_path])))
    r = client.get("/recipes")
    assert r.status_code == 200
    html = r.text
    assert "triage" in html and "75%" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;Triage" in html
    assert "<script>alert(1)" not in html
    assert "<b>soon</b>" not in html and "<img src=x>" not in html
    assert "tenant-secret" not in html
    assert "search → summarise → draft" in html
    assert "25%" in html
    assert '<details>' in html
    assert '<span class="chip chip-masked"' in html and '<span class="chip chip-slot">{company}</span>' in html
    assert "&lt;i&gt;it&lt;/i&gt;" in html and "<i>it</i>" not in html


def test_dashboard_recipes_empty(tmp_path: Path) -> None:
    client = TestClient(build_app(TraceLoader([], skill_roots=[tmp_path])))
    r = client.get("/recipes")
    assert r.status_code == 200 and "No recipe signals yet" in r.text


def test_dashboard_escapes_narrative(tmp_path: Path) -> None:
    _setup(tmp_path, "T", narrative="<script>alert(2)</script> {?} {<b>x</b>}")
    client = TestClient(build_app(TraceLoader([], skill_roots=[tmp_path])))
    html = client.get("/recipes").text
    assert "<script>alert(2)" not in html
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html
    assert '<span class="chip chip-masked"' in html
    assert "<b>x</b>" not in html


def test_dashboard_zero_words_and_malformed(tmp_path: Path) -> None:
    _setup(tmp_path, "T")
    f = next((tmp_path / "_recipes" / "recipes").rglob("v1.json"))
    doc = json.loads(f.read_text())
    doc["masking"]["words"] = 0
    f.write_text(json.dumps(doc))
    client = TestClient(build_app(TraceLoader([], skill_roots=[tmp_path])))
    assert "Masked: —" in client.get("/recipes").text
    doc["steps"] = "nope"
    f.write_text(json.dumps(doc))
    assert client.get("/recipes").status_code == 200


def test_dashboard_marks_placeholders_in_title_when_to_use_and_pitfalls(tmp_path: Path) -> None:
    _setup(tmp_path, "Propuesta para {company}")
    f = next((tmp_path / "_recipes" / "recipes").rglob("v1.json"))
    doc = json.loads(f.read_text())
    doc["text"]["when_to_use"] = "Tras una reunión con {person}"
    doc["text"]["pitfalls"] = ["No {?} antes de confirmar"]
    f.write_text(json.dumps(doc))
    html = TestClient(build_app(TraceLoader([], skill_roots=[tmp_path]))).get("/recipes").text
    assert 'Propuesta para <span class="chip chip-slot">{company}</span>' in html
    assert 'con <span class="chip chip-slot">{person}</span>' in html
    assert 'No <span class="chip chip-masked" title="masked by the server">{?}</span> antes' in html


def test_dashboard_empty_title_reads_masked(tmp_path: Path) -> None:
    _setup(tmp_path, "")
    html = TestClient(build_app(TraceLoader([], skill_roots=[tmp_path]))).get("/recipes").text
    assert "(title masked)" in html


def _strip_text(tmp_path: Path, lexicon) -> None:
    f = next((tmp_path / "_recipes" / "recipes").rglob("v1.json"))
    doc = json.loads(f.read_text())
    del doc["text"]
    doc["masking"] = {"lexicon": lexicon, "masked": 0, "words": 0, "dropped": []}
    f.write_text(json.dumps(doc))


def test_dashboard_structure_only_recipe(tmp_path: Path) -> None:
    _setup(tmp_path, "T")
    _strip_text(tmp_path, None)
    html = TestClient(build_app(TraceLoader([], skill_roots=[tmp_path]))).get("/recipes").text
    assert "search → summarise → draft" in html and "Structure only" in html
    assert "<details>" not in html and "Masked narrative" not in html and "text shared" not in html
    assert "None" not in html and "(title masked)" not in html
    assert "active" in html


def test_dashboard_fully_dropped_text_recipe(tmp_path: Path) -> None:
    _setup(tmp_path, "T")
    _strip_text(tmp_path, "es-test.1")
    html = TestClient(build_app(TraceLoader([], skill_roots=[tmp_path]))).get("/recipes").text
    assert "Structure only" in html and "Masked narrative" not in html and "None" not in html


def test_dashboard_shows_text_badge_when_text_present(tmp_path: Path) -> None:
    _setup(tmp_path, "Triage")
    html = TestClient(build_app(TraceLoader([], skill_roots=[tmp_path]))).get("/recipes").text
    assert "text shared" in html and "Masked narrative" in html
