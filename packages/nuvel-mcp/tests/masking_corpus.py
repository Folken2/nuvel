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

    Leaks are multiset-based. For each folded secret token ``f``:
    ``min(secret count, max(0, output count - non-secret input count))``
    entries ``(case index, token)`` are recorded, so a secret that shares its
    spelling with a non-secret word (a ``com`` in an email and a Catalan "com")
    is a leak only when more copies survive than the non-secret text explains.

    ``kept`` is independent of the leaks: it counts non-secret word tokens of the
    input whose folded form appears among the output's folded word tokens, as a
    multiset; ``total`` is the number of non-secret word tokens.
    """
    report = Report()
    for i, case in enumerate(cases):
        out = Counter(fold(t) for t in tokens(mask_fn(case.text)))
        secret_count = Counter(fold(t) for t in case.secrets)
        nonsecret = Counter(fold(t) for t in tokens(case.text))
        nonsecret.subtract(secret_count)
        nonsecret = +nonsecret  # drop zero and negative counts
        # leaks
        first_form: dict[str, str] = {}
        for secret in case.secrets:
            first_form.setdefault(fold(secret), secret)
        for f, n in secret_count.items():
            for _ in range(min(n, max(0, out[f] - nonsecret[f]))):
                report.leaks.append((i, first_form[f]))
        # kept (own copy of the output counts)
        avail = Counter(out)
        for f, n in nonsecret.items():
            report.total += n
            take = min(n, avail[f])
            report.kept += take
    return report
