"""Catalan recipes are masked like es and en; docs and source must not say otherwise."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nuvel_mcp.masking import load_lexicon, mask_text

ROOT = Path(__file__).resolve().parents[1]
CLAIM = re.compile(r"catalan|\(`ca`\)|`ca` today", re.I)
STALE = re.compile(r"structure.only|dropped|drops|can mask free text only|has no lexicon", re.I)


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


# Every place that lists the masked languages, with the phrase that must name Catalan
# (whitespace is normalised, so wrapped lines match).
LANGUAGE_LISTS = {
    "docs/how-to/deploy-skills-mcp.md": "Spanish, English and Catalan are masked",
    "docs/reference/skill-run-traces.md": "Spanish (`es`), English (`en`) and Catalan (`ca`",
    "CHANGELOG.md": "Spanish, English and Catalan ship lexicons",
}


@pytest.mark.parametrize("name, phrase", sorted(LANGUAGE_LISTS.items()))
def test_masked_language_lists_name_catalan(name, phrase):
    text = " ".join((ROOT / name).read_text(encoding="utf-8").split())
    assert phrase in text, name


# The words the privacy wording gives as examples of a name that can pass
# through. Each must really pass the masker for its language, so the docs
# cannot drift from the shipped lexicons.
RESIDUAL_EXAMPLES = {"March": "en", "Sala": "ca"}
RESIDUAL_DOCS = ("CHANGELOG.md", "docs/reference/skill-run-traces.md")


@pytest.mark.parametrize("word, lang", sorted(RESIDUAL_EXAMPLES.items()))
def test_residual_examples_pass_the_masker(word, lang):
    lexicon = load_lexicon(lang)
    assert lexicon is not None, lang
    assert mask_text(word, lexicon).text == word                       # sentence start
    assert mask_text(f"ok {word.lower()}", lexicon).text.endswith(" " + word.lower())


@pytest.mark.parametrize("name", RESIDUAL_DOCS)
def test_residual_examples_are_the_ones_docs_use(name):
    text = " ".join((ROOT / name).read_text(encoding="utf-8").split())
    assert "e.g. March, or Sala in Catalan" in text or "example March, or Sala in Catalan" in text, name
    assert "Baker" not in text, name
