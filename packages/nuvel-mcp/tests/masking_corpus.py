"""Masking test corpus: loader, evaluator and folding. Test helper, not shipped.

Corpus lines are JSON objects ``{"category": ..., "text": ...}`` where the text
marks each secret inline with ``[[...]]``. ``load_corpus`` strips the markers and
keeps the word tokens that were inside them.
"""

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "masking"

# A word token is a run of Unicode letters; ' (U+0027), U+2019 and - may join letters.
_WORD = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")
_MARKER = re.compile(r"\[\[(.*?)\]\]", re.S)


@dataclass(frozen=True)
class Case:
    text: str
    secrets: list[str]
    category: str


@dataclass
class Report:
    leaks: list[tuple[int, str]] = field(default_factory=list)
    kept: int = 0
    total: int = 0


def fold(s: str) -> str:
    """Strip combining marks (accents), then casefold."""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def tokens(s: str) -> list[str]:
    return _WORD.findall(s)


def corpus_path(split: str, lang: str) -> Path:
    return FIXTURES / split / f"{lang}.jsonl"


def parse_line(obj: dict) -> Case:
    """Build a Case from one corpus object; ValueError on malformed markers."""
    raw = obj["text"]
    spans = _MARKER.findall(raw)
    rest = _MARKER.sub("", raw)
    if "[[" in rest or "]]" in rest or any("[[" in s or "]]" in s for s in spans):
        raise ValueError("unbalanced or nested markers")
    if any(not s.strip() for s in spans):
        raise ValueError("empty marker")
    secrets = [t for s in spans for t in tokens(s)]
    return Case(text=_MARKER.sub(lambda m: m.group(1), raw), secrets=secrets, category=obj["category"])


def load_corpus(split: str, lang: str) -> list[Case]:
    lines = corpus_path(split, lang).read_text(encoding="utf-8").splitlines()
    return [parse_line(json.loads(line)) for line in lines if line.strip()]


def evaluate(cases, mask_fn) -> Report:
    """Run ``mask_fn`` over every case.

    A leak is (case index, secret token) whose folded form equals a folded word
    token of the output. ``kept`` counts non-secret word tokens of the input whose
    folded form appears among the output's folded word tokens, as a multiset;
    ``total`` is the number of non-secret word tokens.
    """
    report = Report()
    for i, case in enumerate(cases):
        out = Counter(fold(t) for t in tokens(mask_fn(case.text)))
        for secret in case.secrets:
            if fold(secret) in out:
                report.leaks.append((i, secret))
        rest = Counter(fold(t) for t in case.secrets)
        for tok in tokens(case.text):
            f = fold(tok)
            if rest[f] > 0:  # this occurrence is the secret one
                rest[f] -= 1
                continue
            report.total += 1
            if out[f] > 0:
                out[f] -= 1
                report.kept += 1
    return report
