"""Catalan recipes are masked like es and en; docs and source must not say otherwise."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAIM = re.compile(r"catalan|\(`ca`\)|`ca` today", re.I)
STALE = re.compile(r"structure.only|can mask free text only|has no lexicon", re.I)


def test_no_catalan_structure_only():
    files = list((ROOT / "docs").rglob("*.md"))
    files += list((ROOT / "packages/nuvel-mcp/src").rglob("*.py"))
    files += list((ROOT / "packages/nuvel-mcp/src").rglob("*.md"))
    files += [ROOT / "README.md", ROOT / "CHANGELOG.md", ROOT / "packages/nuvel-mcp/README.md"]
    bad = []
    for path in files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for para in re.split(r"\n\s*\n", text):
            for clause in re.split(r"[.;]\s", " ".join(para.split())):
                if CLAIM.search(clause) and STALE.search(clause):
                    bad.append(f"{path.relative_to(ROOT)}: {clause[:60]}")
    assert not bad, bad
