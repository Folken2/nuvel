"""Held-out masking corpus: size, line validity and oracle check (written blind)."""

import json
import re
from collections import Counter

import pytest

import masking_corpus as mc
from masking_corpus import evaluate, fold, load_corpus, parse_line, tokens
from test_masking_corpus_format import CATEGORIES

LANGS = ("es", "en", "ca")


def _raw(lang):
    path = mc.corpus_path("holdout", lang)
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.mark.parametrize("lang", LANGS)
def test_holdout_size(lang):
    assert len(load_corpus("holdout", lang)) >= 60


@pytest.mark.parametrize("lang", LANGS)
def test_every_holdout_line_valid(lang):
    lines = _raw(lang)
    assert lines
    for line in lines:
        assert set(line) == {"category", "text"}
        assert line["category"] in CATEGORIES
        assert parse_line(line).secrets
    assert {l["category"] for l in lines} == CATEGORIES


@pytest.mark.parametrize("lang", LANGS)
def test_holdout_secrets_are_tokens_of_text(lang):
    for case in load_corpus("holdout", lang):
        assert not (Counter(fold(t) for t in case.secrets) - Counter(fold(t) for t in tokens(case.text)))


@pytest.mark.parametrize("lang", LANGS)
def test_holdout_oracle_masker_scores_zero_leaks(lang):
    raw = {parse_line(l).text: re.sub(r"\[\[.*?\]\]", "{?}", l["text"]) for l in _raw(lang)}
    report = evaluate(load_corpus("holdout", lang), lambda text: raw[text])
    assert report.leaks == []
    assert report.kept > 0.5 * report.total


@pytest.mark.parametrize("lang", LANGS)
def test_holdout_distinct_from_tune(lang):
    tune = {c.text for c in load_corpus("tune", lang)}
    assert not tune & {c.text for c in load_corpus("holdout", lang)}
