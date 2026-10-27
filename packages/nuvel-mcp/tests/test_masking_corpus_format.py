"""Masking corpus: marker parsing, file validity, tune size and the evaluator."""

import json

import pytest

import masking_corpus as mc
from masking_corpus import Case, evaluate, fold, load_corpus, parse_line, tokens

LANGS = ("es", "en", "ca")

# Identifiers from plugins/chief-of-staff/references/recipe-categories.md.
CATEGORIES = {
    "client-proposal", "client-onboarding", "client-followup", "meeting-prep",
    "meeting-followup", "sales-pipeline", "partner-relations", "investor-update",
    "board-meeting", "monthly-close", "invoicing", "budget-planning", "hiring",
    "team-management", "reporting", "contract-review", "travel-planning",
    "event-planning", "inbox-triage", "document-drafting", "research",
    "weekly-planning", "other",
}


def test_markers_parse():
    case = parse_line({"category": "other", "text": "a [[B c]] d"})
    assert case.text == "a B c d"
    assert case.secrets == ["B", "c"]


@pytest.mark.parametrize("bad", ["a [[b d", "a b]] d", "a [[b [[c]] ]] d", "a [[]] d", "a ]]b[[ d"])
def test_bad_markers_rejected(bad):
    with pytest.raises(ValueError):
        parse_line({"category": "other", "text": bad})


def test_fold_and_tokens():
    assert fold("ÁlvaRO") == "alvaro"
    assert fold("Müller") == "muller"
    assert tokens("O'Brien d’Alcalà Roca-Pujol 20.000 a＠b.com") == [
        "O'Brien", "d’Alcalà", "Roca-Pujol", "a", "b", "com"]
    assert tokens("-x- 'y'") == ["x", "y"]


@pytest.mark.parametrize("lang", LANGS)
def test_every_line_valid(lang):
    path = mc.corpus_path("tune", lang)
    lines = [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines() if raw.strip()]
    assert lines
    for line in lines:
        assert set(line) == {"category", "text"}
        assert line["category"] in CATEGORIES
        case = parse_line(line)  # raises on nested or unbalanced markers
        assert case.secrets
    assert [c.category for c in load_corpus("tune", lang)] == [l["category"] for l in lines]


@pytest.mark.parametrize("lang", LANGS)
def test_tune_size_and_spread(lang):
    cases = load_corpus("tune", lang)
    assert len(cases) >= 90
    assert {c.category for c in cases} == CATEGORIES


def test_evaluate_counts():
    cases = load_corpus("tune", "es")
    report = evaluate(cases, lambda text: text)
    assert len(report.leaks) == sum(len(c.secrets) for c in cases)
    assert report.kept == report.total > 0
    assert all(isinstance(i, int) and isinstance(t, str) for i, t in report.leaks)


def test_evaluate_blank_mask_leaks_nothing_keeps_nothing():
    report = evaluate(load_corpus("tune", "en"), lambda text: "{?}")
    assert report.leaks == [] and report.kept == 0 and report.total > 0


def test_leak_is_folded_token_equality():
    cases = [Case(text="Hablé con Álvaro de Acme", secrets=["Álvaro", "Acme"], category="other")]
    assert evaluate(cases, lambda t: "hable con alvaro de {?}").leaks == [(0, "Álvaro")]
    # a substring is not a leak
    assert evaluate(cases, lambda t: "Acmeco").leaks == []


def test_kept_is_multiset_of_non_secret_tokens():
    cases = [Case(text="la factura de la Acme", secrets=["Acme"], category="other")]
    # non-secret tokens: la, factura, de, la (4). Output has "la" once: only one counted.
    report = evaluate(cases, lambda t: "la factura {?}")
    assert (report.kept, report.total) == (2, 4)
    assert evaluate(cases, lambda t: "LA la De FACTURA").kept == 4
