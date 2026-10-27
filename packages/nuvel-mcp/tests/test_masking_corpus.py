"""Corpus gates for the packaged lexicons (spec §6).

For every language and both splits of the masking corpus:

* zero leaks: no marked secret token survives ``mask_text`` with the packaged
  lexicon; a language with no lexicon goes through the drop path, which keeps
  no free text and so cannot leak;
* readability: with a lexicon, at least ``READABILITY_FLOOR`` of the
  non-secret word tokens survive (a warning below ``READABILITY_TARGET``);
* every lexicon source is permissively licensed, and the name-list words the
  build removed are not in the shipped lexicon.
"""

import json
import re
import warnings
from pathlib import Path

import pytest

from masking_corpus import evaluate, load_corpus
from nuvel_mcp import masking
from nuvel_mcp.masking import READABILITY_FLOOR, READABILITY_TARGET, load_lexicon, mask_recipe, mask_text

LANGUAGES = ("es", "en", "ca")
SPLITS = ("tune", "holdout")

PKG = Path(__file__).parents[1]
LEXICON_DIR = PKG / "src" / "nuvel_mcp" / "lexicon"
SOURCE_DIR = PKG / "scripts" / "lexicon_src"

# SPDX-style ids that meet the licence rule (public domain / CC0, permissive
# OSI licences, CC BY without SA, NC or ND).
PERMISSIVE = frozenset({
    "CC0-1.0", "PDDL-1.0", "public-domain", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "Apache-2.0", "HPND", "CC-BY-2.0", "CC-BY-2.0-FR", "CC-BY-3.0", "CC-BY-4.0",
})
FORBIDDEN = re.compile(r"(?i)^(CC-BY-SA|CC-BY-NC|CC-BY-ND|GPL|LGPL|AGPL|MPL)")


def _drop_path(text: str) -> str:
    """What is stored for *text* when the language has no lexicon."""
    out, info = mask_recipe({"narrative": text}, None)
    assert info["dropped"] == ["/narrative"]
    return out.get("narrative", "")


def _mask_fn(lang: str):
    lexicon = load_lexicon(lang)
    if lexicon is None:
        return None, _drop_path
    return lexicon, lambda text: mask_text(text, lexicon).text


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("lang", LANGUAGES)
def test_zero_leaks(lang, split):
    _, fn = _mask_fn(lang)
    report = evaluate(load_corpus(split, lang), fn)
    # Counts only: the leaked tokens are corpus secrets and stay out of the output.
    assert len(report.leaks) == 0, f"{lang}/{split}: {len(report.leaks)} leaked tokens"


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("lang", LANGUAGES)
def test_readability(lang, split):
    lexicon, fn = _mask_fn(lang)
    if lexicon is None:
        pytest.skip(f"{lang} ships structure only (no lexicon)")
    report = evaluate(load_corpus(split, lang), fn)
    ratio = report.kept / report.total
    if ratio < READABILITY_TARGET:
        msg = f"{lang}/{split}: readability {ratio:.3f} is below the {READABILITY_TARGET} target"
        print(msg)
        warnings.warn(msg)
    assert ratio >= READABILITY_FLOOR, f"{lang}/{split}: readability {ratio:.3f}"


def test_shipped_languages_have_a_lexicon_file():
    shipped = {p.stem for p in LEXICON_DIR.glob("*.txt")} - {"abbreviations"}
    assert shipped <= set(LANGUAGES)
    assert shipped, "no language ships a lexicon"
    for lang in shipped:
        lexicon = load_lexicon(lang)
        assert lexicon is not None, lang
        assert lexicon.language == lang and lexicon.version.startswith(lang + "-")


def _sources_md_licences() -> dict[str, str]:
    """``{source id: licence}`` from the table in SOURCES.md."""
    rows = {}
    for line in (LEXICON_DIR / "SOURCES.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 4 and cells[0] not in ("id", "") \
                and not set(cells[0]) <= set("-: "):
            rows[cells[0]] = cells[2]
    return rows


def _sources_json() -> dict:
    return json.loads((SOURCE_DIR / "sources.json").read_text(encoding="utf-8"))


def test_lexicon_sources_permissive():
    md = _sources_md_licences()
    sources = _sources_json()["sources"]
    assert md and set(md) == {s["id"] for s in sources}
    for source in sources:
        licence = source["licence"]
        assert md[source["id"]] == licence, source["id"]
        assert not FORBIDDEN.match(licence), source["id"]
        assert licence in PERMISSIVE, (source["id"], licence)
        assert source["checked"] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", source["checked"])
        assert source["licence_url"].startswith("https://")
        for f in source["files"]:
            assert f["url"].startswith("https://") and re.fullmatch(r"[0-9a-f]{64}", f["sha256"])


@pytest.mark.parametrize("licence", ["CC-BY-SA-4.0", "cc-by-sa-3.0", "GPL-3.0-only", "LGPL-2.1",
                                     "MPL-2.0", "CC-BY-NC-4.0", "AGPL-3.0"])
def test_forbidden_licences_are_rejected(licence):
    assert FORBIDDEN.match(licence) and licence not in PERMISSIVE


def test_shipped_lexicons_name_their_sources():
    ids = {s["id"] for s in _sources_json()["sources"]}
    for path in LEXICON_DIR.glob("*.txt"):
        if path.stem == "abbreviations":
            continue
        lexicon = load_lexicon(path.stem)
        listed = {s.strip() for s in lexicon.sources.split(",") if s.strip()}
        assert listed and listed <= ids, path.stem


@pytest.mark.parametrize("lang", LANGUAGES)
def test_no_name_list_word_in_lexicon(lang):
    lexicon = load_lexicon(lang)
    if lexicon is None:
        pytest.skip(f"{lang} ships structure only (no lexicon)")
    sample = SOURCE_DIR / f"removed-sample-{lang}.txt"
    removed = [line.strip() for line in sample.read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.startswith("#")]
    assert 0 < len(removed) <= 500
    assert not {masking.fold(w) for w in removed} & lexicon.words


@pytest.mark.parametrize("lang", LANGUAGES)
def test_single_letter_entries_are_elisions_only(lang):
    lexicon = load_lexicon(lang)
    if lexicon is None:
        pytest.skip(f"{lang} ships structure only (no lexicon)")
    allowed = {"ca": set("ldsnmt"), "en": set("stmdi"), "es": set()}[lang]
    singles = {w for section in (lexicon.words, lexicon.numbers, lexicon.dates, lexicon.currency)
               for w in section if len(w) == 1}
    assert singles <= allowed
