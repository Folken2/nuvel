"""Dashboard /recipes page."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from nuvel.dashboard.app import build_app
from nuvel.dashboard.loader import TraceLoader

RID = "11111111-1111-4111-8111-111111111111"


def _setup(root: Path, title: str) -> None:
    sd = root / "_recipes" / "signals"
    sd.mkdir(parents=True)
    (sd / "2026-10-27.jsonl").write_text(json.dumps({
        "schema": "nuvel.recipe_signal/1", "signal_id": "s1", "recipe_id": RID,
        "category": "triage", "event": "used", "at": "2026-10-27T10:00:00Z",
        "uses": 4, "successes": 3, "corrections": 1, "tenant": "tenant-secret",
    }) + "\n")
    rd = root / "_recipes" / "recipes" / "tenant-secret" / RID
    rd.mkdir(parents=True)
    (rd / "v1.json").write_text(json.dumps({
        "schema": "nuvel.recipe/1", "recipe_id": RID, "version": 1, "category": "triage",
        "title": title, "when_to_use": "<b>soon</b>",
        "steps": [{"action": "read", "tool_kind": "email", "text": "Read <i>it</i>"}],
        "pitfalls": ["<img src=x>"], "tenant": "tenant-secret",
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


def test_dashboard_recipes_empty(tmp_path: Path) -> None:
    client = TestClient(build_app(TraceLoader([], skill_roots=[tmp_path])))
    r = client.get("/recipes")
    assert r.status_code == 200 and "No recipe signals yet" in r.text
