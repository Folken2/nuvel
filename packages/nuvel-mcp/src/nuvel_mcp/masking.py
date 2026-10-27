"""Allowlist masker for recipe free text (``nuvel.recipe/2``, spec §4).

Stdlib only, deterministic, no I/O apart from reading the packaged lexicons.
Every word that is not on the lexicon for the recipe's language becomes
``{?}``; typed patterns (emails, URLs, phones, ids, amounts, numbers, dates)
become ``{type}`` placeholders. The masker is fail-safe: on any internal error
a field is dropped, never returned unmasked, and no exception carries input
text. ``invariant_hits`` is the independent third layer (spec §4.5).
"""

from __future__ import annotations

import functools
import importlib.resources
import re
import string
import sys
import unicodedata
from dataclasses import dataclass
from typing import Iterator

from .recipes import (
    ACTIONS,
    CATEGORIES,
    CURRENCY_WORDS,
    DIGIT_RUN_RE,
    DOMAIN_RE,
    EMAIL_RE,
    IBAN_RE,
    PHONE_RE,
    PII_PATTERNS,
    PLUGIN_NAMES,
    URL_RE,
)

DROP_THRESHOLD = 0.40
READABILITY_TARGET = 0.85
READABILITY_FLOOR = 0.75

MASK = "{?}"
PLACEHOLDER_TYPES = (
    "person", "company", "team", "product", "project", "document", "file",
    "folder", "amount", "number", "date", "time", "period", "place", "email",
    "phone", "url", "id", "topic", "other",
)
_KNOWN = frozenset(PLACEHOLDER_TYPES)

# Longest text the masker will look at (free text is at most 2000 characters
# by schema). Anything longer fails safe instead of risking slow regexes.
_MAX_CHARS = 8000


@dataclass(frozen=True)
class Lexicon:
    words: frozenset[str]
    numbers: frozenset[str]
    dates: frozenset[str]
    currency: frozenset[str]
    version: str
    sources: str = ""
    language: str = ""  # selects the elision rules; empty → the version's language tag


@dataclass(frozen=True)
class MaskResult:
    text: str
    masked: int
    words: int


_FAILSAFE = MaskResult(MASK, 1, 1)  # ratio 1.0: always dropped by mask_recipe


def fold(s: str) -> str:
    """Strip combining marks (accents), then casefold."""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


# --- Lexicon loading -----------------------------------------------------------

_LANG_RE = re.compile(r"[a-z]{2,3}(?:-[a-z]{2,4})?")
_VERSION_TAIL_RE = re.compile(r"\d{4}\.\d{2}\.\d+")
_SECTION_RE = re.compile(r"#\s*\[([^\]]*)\]\s*")
_SECTIONS = ("words", "numbers", "dates", "currency")
_VERSION_RE = re.compile(r"#\s*version:\s*(\S+)\s*")
_SOURCES_RE = re.compile(r"#\s*sources:\s*(.*?)\s*")
_INVALID_LEXICON = "nuvel-mcp: lexicon file invalid; that language ships structure only"


class _InvalidLexicon(ValueError):
    pass


def _lexicon_dir():
    return importlib.resources.files("nuvel_mcp").joinpath("lexicon")


def _parse_lexicon(text: str, language: str | None) -> Lexicon:
    version = None
    sources = ""
    section = None
    sets: dict[str, set[str]] = {name: set() for name in _SECTIONS}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if m := _SECTION_RE.fullmatch(line):
                if m.group(1) not in _SECTIONS:
                    raise _InvalidLexicon("unknown section")
                section = m.group(1)
            elif section is None and (m := _VERSION_RE.fullmatch(line)):
                if version is not None:
                    raise _InvalidLexicon("two versions")
                version = m.group(1)
            elif section is None and (m := _SOURCES_RE.fullmatch(line)):
                sources = m.group(1)
            continue
        if section is None:
            raise _InvalidLexicon("entry before the first section")
        if any(c in line for c in "-'’") or any(c.isspace() for c in line):
            raise _InvalidLexicon("entry is not a single word")  # lookups are per part
        sets[section].add(fold(line))
    if version is None:
        raise _InvalidLexicon("no version")
    lang, sep, tail = version.rpartition("-")
    if not sep or not _LANG_RE.fullmatch(lang) or not _VERSION_TAIL_RE.fullmatch(tail):
        raise _InvalidLexicon("bad version")
    if language is not None and lang != language:
        raise _InvalidLexicon("wrong language")
    return Lexicon(
        words=frozenset(sets["words"]),
        numbers=frozenset(sets["numbers"]),
        dates=frozenset(sets["dates"]),
        currency=frozenset(sets["currency"]),
        version=version,
        sources=sources,
        language=lang,
    )


def _load_lexicon_from(path, language: str | None = None) -> Lexicon | None:
    """Parse one lexicon file (a ``Path`` or ``Traversable``). ``None`` if it is
    missing, or (logged with a constant message, never file content) if it is
    unreadable or invalid: no valid ``# version: <lang>-<YYYY.MM.N>``, an entry
    before the first section, an unknown section, or an entry holding ``-``,
    an apostrophe or whitespace."""
    try:
        if not path.is_file():
            return None
    except Exception:
        return None
    try:
        return _parse_lexicon(path.read_text(encoding="utf-8"), language)
    except Exception:
        print(_INVALID_LEXICON, file=sys.stderr, flush=True)
        return None


@functools.lru_cache(maxsize=32)
def _load_lexicon_cached(language: str) -> Lexicon | None:
    return _load_lexicon_from(_lexicon_dir().joinpath(f"{language}.txt"), language)


def load_lexicon(language: str) -> Lexicon | None:
    """The packaged lexicon ``lexicon/{language}.txt``, or ``None`` (structure only)."""
    try:
        if not isinstance(language, str) or not _LANG_RE.fullmatch(language):
            return None
        return _load_lexicon_cached(language)
    except Exception:
        return None


def _load_abbreviations_from(path) -> frozenset[str]:
    """One entry per line, ``#`` comments; folded. Missing or unreadable → empty."""
    try:
        if not path.is_file():
            return frozenset()
        lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
        return frozenset(fold(line) for line in lines if line and not line.startswith("#"))
    except Exception:
        return frozenset()


def _load_abbreviations() -> frozenset[str]:
    try:
        return _load_abbreviations_from(_lexicon_dir().joinpath("abbreviations.txt"))
    except Exception:
        return frozenset()


ABBREVIATIONS: frozenset[str] = _load_abbreviations()


# --- Step 1: normalisation -----------------------------------------------------

def _normalise(text: str) -> str:
    """NFKC, then drop format (Cf), control, surrogate, private-use, unassigned
    and combining-mark characters. ``\\n`` and ``\\t`` stay; ``\\r`` and the
    line/paragraph separators become ``\\n``."""
    out = []
    for c in unicodedata.normalize("NFKC", text):
        if c in "\n\t":
            out.append(c)
            continue
        if c == "\r":
            out.append("\n")
            continue
        cat = unicodedata.category(c)
        if cat in ("Zl", "Zp"):
            out.append("\n")
        elif cat[0] == "C" or cat[0] == "M":
            continue  # Cc, Cf, Cs, Co, Cn; Mn, Mc, Me (marks with no precomposed form)
        else:
            out.append(c)
    return "".join(out)


# --- Step 2: typed patterns ----------------------------------------------------

# Server placeholders are written as NUL + a private-use code point + NUL while
# the text is processed. Step 1 removes NUL and private-use characters, so a
# client cannot forge one; they match none of the typed regexes below.
_SENTINEL_BASE = 0xE000


def _sentinel(kind: str) -> str:
    return "\x00" + chr(_SENTINEL_BASE + PLACEHOLDER_TYPES.index(kind)) + "\x00"


_NUM = r"(?:\d{1,3}(?:[ .,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_AMOUNT_RE = re.compile(
    r"[€$£¥]\s?" + _NUM
    + r"|" + _NUM + r"\s?(?:[€$£¥]|" + CURRENCY_WORDS + r")"
    + r"|\b(?:eur|usd|gbp)\s?" + _NUM,
    re.IGNORECASE,
)
# Any remaining alphanumeric run containing a digit (``15th``, ``v2``, ``14:30``),
# together with a hyphen-joined letter prefix and any joined tail
# (``PO-55102``, ``F-2026-0412``, ``55102/a``): an id is one token. The prefix
# group is possessive, so each start fails without backtracking into it; over
# all the hyphen-part starts the worst case is quadratic, which the
# ``_MAX_CHARS`` cap bounds (about 0.1 s at 8000 characters).
_DIGITS_RE = re.compile(r"(?<![^\W_])(?:[^\W\d_]+-)*+[^\W_]*\d(?:[^\W_]|[.,:/\-](?=[^\W_]))*")
# The same shape with at least one letter prefix is an id (``PO-55102``,
# ``F-2026-0412``); it runs before phones so the prefix is not left behind.
_PREFIXED_ID_RE = re.compile(r"(?<![^\W_])(?:[^\W\d_]+-)++[^\W_]*\d(?:[^\W_]|[.,:/\-](?=[^\W_]))*")

# Domains with Unicode letters (``rosá.com``): letters.letters, last label >= 2 letters.
_IDN_RE = re.compile(
    r"(?<![^\W_])[^\W\d_]+(?:-[^\W\d_]+)*(?:\.[^\W\d_]+(?:-[^\W\d_]+)*)*\.[^\W\d_]{2,}(?:[/:?#]\S*)?"
)

_LEAD_TRIM = "([{\"'«“‘¿¡<"
_TRAIL_TRIM = ".,;:!?)]}\"'»”’>"

# (placeholder type, regex, trim punctuation at the edges). Spec order, except
# that the IBAN regex runs before phones, amounts before phones and digit
# runs, and letter-prefixed ids before phones, so ``ES91 2100 …`` is an
# ``{id}``, ``125.000 €`` an ``{amount}`` and ``F-2026-0412`` an ``{id}``
# rather than a phone or id. Every digit is replaced either way.
_TYPED = (
    # Anchored to a token start: same leftmost matches as EMAIL_RE, linear time.
    ("email", re.compile(r"(?<![^\s@])" + EMAIL_RE.pattern), True),
    ("url", re.compile(URL_RE.pattern + r"\S*", re.IGNORECASE), True),
    ("url", re.compile(DOMAIN_RE.pattern + r"[\w.\-]*(?:[/:?#]\S*)?", re.IGNORECASE), True),
    ("url", _IDN_RE, True),
    ("id", IBAN_RE, True),
    ("amount", _AMOUNT_RE, False),
    ("id", _PREFIXED_ID_RE, False),
    ("phone", PHONE_RE, False),
    ("id", DIGIT_RUN_RE, False),
    ("number", _DIGITS_RE, False),
)


def _replace(text: str, kind: str, regex: re.Pattern, trim: bool) -> tuple[str, int]:
    pieces, pos, count = [], 0, 0
    for m in regex.finditer(text):
        start, end = m.span()
        if trim:
            while start < end and text[start] in _LEAD_TRIM:
                start += 1
            while end > start and (text[end - 1] in _TRAIL_TRIM or text[end - 1].isspace()):
                end -= 1
        if end <= start:
            continue
        pieces.append(text[pos:start])
        pieces.append(_sentinel(kind))
        pos = end
        count += 1
    if not count:
        return text, 0
    pieces.append(text[pos:])
    return "".join(pieces), count


def _replace_typed(text: str) -> str:
    """Apply the typed regexes until nothing changes (a replacement can expose
    a new match, e.g. a domain that a digit used to shield)."""
    for _ in range(5):
        changed = 0
        for kind, regex, trim in _TYPED:
            if kind == "email" and "@" not in text:
                continue
            text, n = _replace(text, kind, regex, trim)
            changed += n
        if not changed:
            break
    else:
        raise ValueError("typed replacement did not settle")
    if any(c.isdigit() for c in text):
        raise ValueError("digit left after typed replacement")
    return text


# --- Step 3/4: tokens ------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"\x00([-])\x00"                  # server placeholder
    r"|\{([a-z_?]{1,20})\}"                       # client placeholder (known or not)
    r"|([^\W\d_]+(?:['’\-][^\W\d_]+)*)"           # word
)
_PART_SPLIT = re.compile(r"['’\-]")
_HSPACE = re.compile(r"[ \t]*")
_BREAK_RE = re.compile(r"\n|[.!?:][\"'”’»)\]]*\s")

# Joiners: inside a run of number words ("veinte y cinco", "vint-i-cinc",
# "hundred and five"), and between a number and a month or currency word
# ("15 de marzo", "mil de euros", "fifteenth of march", "15 d'abril").
_NUM_JOIN = frozenset({"y", "i", "and"})
_LINK_JOIN = frozenset({"de", "del", "d", "of", "the"})


# Characters allowed between tokens. Everything else (symbols, emoji, flags,
# enclosed or braille letters…) is deny-by-default: a counted ``{?}``.
GAP_ALLOWED = frozenset(string.punctuation.replace("|", "") + "¿¡«»“”‘’–—…•·")
_GAP_RUN_RE = re.compile(r"[\s" + re.escape("".join(sorted(GAP_ALLOWED))) + r"]+")


_ELLIPSIS_RE = re.compile(r"(?<!\.)\.\.\.(?!\.)|…")
_EDGE_SPACE_RE = re.compile(r"\A(\s*)(.*?)(\s*)\Z", re.S)
PUNCT_RUN_LIMIT = 4


def _punctuation_run(run: str) -> bool:
    """Four or more punctuation characters between two tokens, whitespace
    ignored and an ellipsis counted as one (Morse, ASCII art…)."""
    marks = "".join(_ELLIPSIS_RE.sub("…", run).split())
    return len(marks) >= PUNCT_RUN_LIMIT


def _allowed_run(run: str) -> Iterator[tuple[str, str]]:
    if not _punctuation_run(run):
        yield "gap", run
        return
    lead, middle, trail = _EDGE_SPACE_RE.fullmatch(run).groups()
    if lead:
        yield "gap", lead
    yield "bad", middle
    if trail:
        yield "gap", trail


def _split_gap(gap: str) -> Iterator[tuple[str, str]]:
    """Allowed runs stay ``gap`` (unless a long punctuation run); each run of
    other characters is ``bad``."""
    pos = 0
    for m in _GAP_RUN_RE.finditer(gap):
        if m.start() > pos:
            yield "bad", gap[pos:m.start()]
        yield from _allowed_run(m.group(0))
        pos = m.end()
    if pos < len(gap):
        yield "bad", gap[pos:]


def _tokenise(text: str) -> list[tuple[str, str]]:
    """``(kind, value)``: kinds ``gap``, ``bad`` (disallowed characters), ``srv``
    (type), ``cli`` (type) and ``word``."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        if m.start() > pos:
            tokens.extend(_split_gap(text[pos:m.start()]))
        if m.group(1) is not None:
            tokens.append(("srv", PLACEHOLDER_TYPES[ord(m.group(1)) - _SENTINEL_BASE]))
        elif m.group(2) is not None:
            tokens.append(("cli", m.group(2)))
        else:
            tokens.append(("word", m.group(3)))
        pos = m.end()
    if pos < len(text):
        tokens.extend(_split_gap(text[pos:]))
    return tokens


def _parts(word: str) -> list[str]:
    return [fold(p) for p in _PART_SPLIT.split(word)]


def _number_count(tok: tuple[str, str], lx: Lexicon) -> int:
    """How many number words a token stands for (0 = not a number)."""
    kind, value = tok
    if kind == "srv":
        return 1 if value == "number" else 0
    if kind != "word":
        return 0
    ps = _parts(value)
    if ps[0] in lx.numbers and ps[-1] in lx.numbers and all(p in lx.numbers or p in _NUM_JOIN for p in ps):
        return sum(p in lx.numbers for p in ps)
    return 0


def _ends_in(tok: tuple[str, str], vocab: frozenset[str]) -> bool:
    """A word whose last part is in *vocab* and whose other parts are link joiners (``d'abril``)."""
    if tok[0] != "word":
        return False
    ps = _parts(tok[1])
    return ps[-1] in vocab and all(p in _LINK_JOIN for p in ps[:-1])


def _is_word_in(tok: tuple[str, str], vocab: frozenset[str]) -> bool:
    return tok[0] == "word" and fold(tok[1]) in vocab


class _Elements:
    """Non-gap tokens plus whether each is joined to the next by spaces only."""

    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.idx = [i for i, t in enumerate(tokens) if t[0] != "gap"]
        self.link = []
        for a, b in zip(self.idx, self.idx[1:]):
            between = "".join(t[1] for t in tokens[a + 1:b])
            self.link.append(_HSPACE.fullmatch(between) is not None)

    def __len__(self):
        return len(self.idx)

    def tok(self, k: int) -> tuple[str, str]:
        return self.tokens[self.idx[k]]

    def linked(self, k: int, j: int) -> bool:
        """Elements k..j (k < j) are all joined by spaces only."""
        return all(self.link[m] for m in range(k, j))

    def collapse(self, spans: list[tuple[int, int, str]]) -> list[tuple[str, str]]:
        """Replace each element span ``(lo, hi, type)`` (and the gaps inside it) by one placeholder."""
        out: list[tuple[str, str]] = []
        pos = 0
        for lo, hi, kind in spans:
            out.extend(self.tokens[pos:self.idx[lo]])
            out.append(("srv", kind))
            pos = self.idx[hi] + 1
        out.extend(self.tokens[pos:])
        return out


def _merge(spans: list[tuple[int, int, str]], prefer: str) -> list[tuple[int, int, str]]:
    merged: list[tuple[int, int, str]] = []
    for lo, hi, kind in sorted(spans):
        if merged and lo <= merged[-1][1]:
            plo, phi, pkind = merged[-1]
            merged[-1] = (plo, max(phi, hi), prefer if prefer in (pkind, kind) else pkind)
        else:
            merged.append((lo, hi, kind))
    return merged


def _number_words(tokens: list[tuple[str, str]], lx: Lexicon) -> list[tuple[str, str]]:
    """Runs of number words next to a currency word → amount; two or more → number."""
    els = _Elements(tokens)
    n = len(els)

    def currency(k: int) -> bool:
        t = els.tok(k)
        return (t[0] == "srv" and t[1] == "amount") or _ends_in(t, lx.currency)

    spans = []
    k = 0
    while k < n:
        count = _number_count(els.tok(k), lx)
        if not count:
            k += 1
            continue
        lo = hi = k
        while True:
            if hi + 1 < n and els.linked(hi, hi + 1) and (c := _number_count(els.tok(hi + 1), lx)):
                hi, count = hi + 1, count + c
            elif (hi + 2 < n and els.linked(hi, hi + 2) and _is_word_in(els.tok(hi + 1), _NUM_JOIN)
                  and (c := _number_count(els.tok(hi + 2), lx))):
                hi, count = hi + 2, count + c
            else:
                break
        # One number word that is not in [words] ("catorce meses") is a {number} too.
        lone_number = lo == hi and els.tok(lo)[0] == "word" and fold(els.tok(lo)[1]) not in lx.words
        start, end, is_amount = lo, hi, False
        if hi + 1 < n and els.linked(hi, hi + 1) and currency(hi + 1):
            end, is_amount = hi + 1, True
        elif (hi + 2 < n and els.linked(hi, hi + 2) and _is_word_in(els.tok(hi + 1), _LINK_JOIN)
              and currency(hi + 2)):
            end, is_amount = hi + 2, True
        if lo > 0 and els.linked(lo - 1, lo) and currency(lo - 1):
            start, is_amount = lo - 1, True
        if is_amount:
            spans.append((start, end, "amount"))
        elif count >= 2 or lone_number:
            spans.append((start, end, "number"))
        k = hi + 1
    return els.collapse(_merge(spans, "amount")) if spans else tokens


def _dates(tokens: list[tuple[str, str]], lx: Lexicon) -> list[tuple[str, str]]:
    """A month or weekday next to a number or number word → date."""
    els = _Elements(tokens)
    n = len(els)
    spans = []
    for k in range(n):
        if not _ends_in(els.tok(k), lx.dates):
            continue
        lo = hi = k
        if k >= 1 and els.linked(k - 1, k) and _number_count(els.tok(k - 1), lx):
            lo = k - 1
        elif (k >= 2 and els.linked(k - 2, k) and _is_word_in(els.tok(k - 1), _LINK_JOIN)
              and _number_count(els.tok(k - 2), lx)):
            lo = k - 2
        if k + 1 < n and els.linked(k, k + 1) and _number_count(els.tok(k + 1), lx):
            hi = k + 1
        elif (k + 2 < n and els.linked(k, k + 2) and _is_word_in(els.tok(k + 1), _LINK_JOIN)
              and _number_count(els.tok(k + 2), lx)):
            hi = k + 2
        if lo < k or hi > k:
            spans.append((lo, hi, "date"))
    return els.collapse(_merge(spans, "date")) if spans else tokens


# --- Steps 5–7: the allowlist ----------------------------------------------------

def _has_upper(s: str) -> bool:
    return any(c.isupper() or c.istitle() for c in s)


def _is_abbreviation(word: str, abbreviations: frozenset[str]) -> bool:
    return word.isalpha() and word.isupper() and len(word) <= 4 and fold(word) in abbreviations


# Single-letter parts are allowed only as apostrophe elisions, per language,
# in a word that has a part of two or more letters:
#   ca: a prefix on the first part (``l'``, ``d'``, ``s'``, ``n'``, ``m'``, ``t'``);
#   en: a contraction suffix on the last part (``'s``, ``'t``, ``'m``, ``'d``),
#       and ``I'm`` / ``I'd``, the only words exempt from the two-letter part;
#   es and every other language: none.
@dataclass(frozen=True)
class _Elisions:
    prefix: frozenset[str] = frozenset()
    suffix: frozenset[str] = frozenset()
    exempt: frozenset[str] = frozenset()


_ELISIONS = {
    "ca": _Elisions(prefix=frozenset("ldsnmt")),
    "en": _Elisions(suffix=frozenset("stmd"), exempt=frozenset({"I'm", "I’m", "I'd", "I’d"})),
}
_NO_ELISIONS = _Elisions()
_SEP_SPLIT = re.compile(r"(['’\-])")


def _language(lx: Lexicon) -> str:
    return lx.language or lx.version.rpartition("-")[0]


def _shape_ok(word: str, elisions: _Elisions) -> bool:
    """Rejects spelled-out names (``s-o-l``, ``t'i'm``) and elisions the
    language does not have."""
    pieces = _SEP_SPLIT.split(word)
    if len(pieces) == 1 or word in elisions.exempt:
        return True
    parts, seps = pieces[0::2], pieces[1::2]
    if not any(len(p) >= 2 for p in parts):
        return False
    last = len(parts) - 1
    for i, part in enumerate(parts):
        if len(part) >= 2:
            continue
        if i == 0 and seps[0] in ("'", "’") and fold(part) in elisions.prefix:
            continue
        if i == last and seps[-1] in ("'", "’") and fold(part) in elisions.suffix:
            continue
        return False
    return True


def _single_letter_runs(tokens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Three or more consecutive one-letter words (``a e i o``) become one ``run``."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(tokens)
    while i < n:
        if tokens[i][0] == "word" and len(tokens[i][1]) == 1:
            j, last, count = i, i, 0
            while j < n and (tokens[j][0] == "gap" or (tokens[j][0] == "word" and len(tokens[j][1]) == 1)):
                if tokens[j][0] == "word":
                    last, count = j, count + 1
                j += 1
            if count >= 3:
                out.append(("run", str(count)))
                i = last + 1
                continue
            out.extend(tokens[i:last + 1])
            i = last + 1
            continue
        out.append(tokens[i])
        i += 1
    return out


_INITIAL_GAP_RE = re.compile(r"\.\s*")


def _initials(tokens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """A one-letter word directly followed by ``.`` and then another word or a
    placeholder is a name initial (``M. Smith``, ``j. garcía``): the letter
    and its dot become one ``initial`` token, masked in every language."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(tokens)
    while i < n:
        kind, value = tokens[i]
        if (kind == "word" and len(value) == 1 and i + 2 < n and tokens[i + 1][0] == "gap"
                and _INITIAL_GAP_RE.fullmatch(tokens[i + 1][1])
                and tokens[i + 2][0] in ("word", "srv", "cli")):
            out.append(("initial", value))
            rest = tokens[i + 1][1][1:]
            if rest:
                out.append(("gap", rest))
            i += 2
            continue
        out.append(tokens[i])
        i += 1
    return out


def _case_ok(word: str, at_start: bool) -> bool:
    """Rule 6, second half: no capital letter except a sentence-initial one or ``I``."""
    for k, part in enumerate(_PART_SPLIT.split(word)):
        if part == "I" or not _has_upper(part):
            continue
        if at_start and k == 0 and _has_upper(part[0]) and not _has_upper(part[1:]):
            continue
        return False
    return True


def _mask(text: str, lx: Lexicon, abbreviations: frozenset[str]) -> MaskResult:
    if len(text) > _MAX_CHARS:
        return _FAILSAFE
    text = _normalise(text)
    if len(text) > _MAX_CHARS:
        return _FAILSAFE
    text = _replace_typed(text)
    tokens = _tokenise(text)
    tokens = _number_words(tokens, lx)
    tokens = _dates(tokens, lx)
    tokens = _single_letter_runs(tokens)
    tokens = _initials(tokens)
    elisions = _ELISIONS.get(_language(lx), _NO_ELISIONS)

    out: list[str] = []
    last_mask = False        # the last non-gap output is {?}
    pending_gap = None       # index in out of the gap after that {?}, if spaces only
    masked = words = 0
    at_start = True

    def emit_mask():
        nonlocal last_mask, pending_gap
        if last_mask:
            if pending_gap is not None:
                del out[pending_gap]
                pending_gap = None
            return
        out.append(MASK)
        last_mask = True

    for kind, value in tokens:
        if kind == "gap":
            if _BREAK_RE.search(value):
                at_start = True
            if last_mask and _HSPACE.fullmatch(value):
                pending_gap = len(out)  # spaces only: a following {?} collapses into this one
            else:
                last_mask, pending_gap = False, None
            out.append(value)
            continue
        if kind == "srv":
            words += 1
            out.append("{" + value + "}")
            last_mask, pending_gap = False, None
        elif kind == "cli":
            if value in _KNOWN:
                out.append("{" + value + "}")
                last_mask, pending_gap = False, None
            else:  # a client "{?}" or an unknown type: masked as non-lexicon text
                words += 1
                masked += 1
                emit_mask()
        elif kind == "bad":  # characters outside the gap allowlist
            words += 1
            masked += 1
            emit_mask()
        elif kind == "initial":  # a name initial and its dot
            words += 1
            masked += 1
            emit_mask()
        elif kind == "run":  # spelled-out letters
            words += int(value)
            masked += int(value)
            emit_mask()
        else:
            words += 1
            if _is_abbreviation(value, abbreviations) or (
                _shape_ok(value, elisions) and all(p in lx.words for p in _parts(value))
                and _case_ok(value, at_start)
            ):
                out.append(value)
                last_mask, pending_gap = False, None
            else:
                masked += 1
                emit_mask()
        at_start = False
    return MaskResult("".join(out), masked, words)


def mask_text(text: str, lexicon: Lexicon, abbreviations: frozenset[str] | None = None) -> MaskResult:
    """Mask one free-text field. Never raises; on any internal error the result
    is ``{?}`` with ``masked == words == 1`` (so the field is dropped)."""
    try:
        if not isinstance(text, str) or not isinstance(lexicon, Lexicon):
            return _FAILSAFE
        abbrevs = ABBREVIATIONS if abbreviations is None else abbreviations
        return _mask(text, lexicon, abbrevs)
    except Exception:
        return _FAILSAFE


# --- Recipes -------------------------------------------------------------------

# Structured (non-free-text) fields are copied only from this allowlist, and
# only when their value is in a closed set or has a fixed, digit-bounded shape.
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SEMVER_RE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{1,2}")  # ≤ 6 digits: no room for a phone number
_LANGUAGE_RE = re.compile(r"[a-z]{2}")
SCHEMA_ID = "nuvel.recipe/2"
_TOOL_KINDS = frozenset({"email", "calendar", "chat", "docs", "storage", "tasks", "web", "none"})


def _in(values) -> object:
    closed = frozenset(values)
    return lambda v: isinstance(v, str) and v in closed


def _match(regex: re.Pattern):
    return lambda v: isinstance(v, str) and regex.fullmatch(v) is not None


def _recipe_version(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 1000


_TOP_FIELDS = {
    "schema": _in((SCHEMA_ID,)),
    "recipe_id": _match(_UUID_RE),
    "install_id": _match(_UUID_RE),
    "version": _recipe_version,
    "category": _in(CATEGORIES),
    "language": _match(_LANGUAGE_RE),
}
_PLUGIN_FIELDS = {"name": _in(PLUGIN_NAMES), "version": _match(_SEMVER_RE)}
_STEP_FIELDS = {"action": _in(ACTIONS), "tool_kind": _in(_TOOL_KINDS)}
_slot = _in(PLACEHOLDER_TYPES)

# The only key names a JSON pointer may contain (``dropped`` and invariant
# paths are stored and returned). Any other key is client text: ``{?}``.
POINTER_KEYS = frozenset({
    "schema", "recipe_id", "install_id", "plugin", "name", "version", "category",
    "language", "title", "when_to_use", "steps", "action", "tool_kind", "slots",
    "note", "pitfalls", "narrative",
})


def _key(key: object) -> str:
    return key if isinstance(key, str) and key in POINTER_KEYS else MASK


def _mask_field(value: object, lexicon: Lexicon | None, abbreviations) -> MaskResult | None:
    """The masked field, or ``None`` when it must be dropped."""
    if lexicon is None or not isinstance(value, str):
        return None
    result = mask_text(value, lexicon, abbreviations)
    if result.words > 0 and result.masked / result.words > DROP_THRESHOLD:
        return None
    return result


def _mask_recipe(recipe: object, lexicon: Lexicon | None, abbreviations) -> tuple[dict, dict]:
    info = {"lexicon": lexicon.version if lexicon is not None else None,
            "masked": 0, "words": 0, "dropped": []}
    dropped: list[str] = info["dropped"]
    if not isinstance(recipe, dict):
        dropped.append("")
        return {}, info

    def text(value: object) -> str | None:
        result = _mask_field(value, lexicon, abbreviations)
        if result is None:
            return None
        info["masked"] += result.masked
        info["words"] += result.words
        return result.text

    out: dict = {}
    for key, value in recipe.items():
        path = "/" + _key(key)
        if key in ("title", "when_to_use"):
            masked = text(value)
            out[key] = "" if masked is None else masked  # required / kept empty
            if masked is None:
                dropped.append(path)
        elif key == "narrative":
            masked = text(value)
            if masked is None:
                dropped.append(path)
            else:
                out[key] = masked
        elif key in _TOP_FIELDS:
            if _TOP_FIELDS[key](value):
                out[key] = value
            else:
                dropped.append(path)
        elif key == "plugin":
            if not isinstance(value, dict):
                dropped.append(path)
                continue
            plugin = {}
            for k, v in value.items():
                if k in _PLUGIN_FIELDS and _PLUGIN_FIELDS[k](v):
                    plugin[k] = v
                else:
                    dropped.append(f"{path}/{_key(k)}")
            out[key] = plugin
        elif key == "steps":
            if not isinstance(value, list):
                dropped.append(path)
                continue
            steps = []
            for i, step in enumerate(value):
                if not isinstance(step, dict):
                    dropped.append(f"/steps/{i}")
                    continue
                kept: dict = {}
                for k, v in step.items():
                    sub = f"/steps/{i}/{_key(k)}"
                    if k in _STEP_FIELDS:
                        if _STEP_FIELDS[k](v):
                            kept[k] = v
                        else:
                            dropped.append(sub)
                    elif k == "slots":
                        if not isinstance(v, list):
                            dropped.append(sub)
                            continue
                        slots = []
                        for j, slot in enumerate(v):
                            if _slot(slot):
                                slots.append(slot)
                            else:
                                dropped.append(f"{sub}/{j}")
                        kept[k] = slots
                    elif k == "note":
                        masked = text(v)
                        if masked is None:
                            dropped.append(sub)
                        else:
                            kept[k] = masked
                    else:
                        dropped.append(sub)
                steps.append(kept)
            out[key] = steps
        elif key == "pitfalls":
            if not isinstance(value, list):
                dropped.append(path)
                continue
            pitfalls = []
            for i, item in enumerate(value):
                masked = text(item)
                if masked is None:
                    dropped.append(f"/pitfalls/{i}")
                else:
                    pitfalls.append(masked)
            out[key] = pitfalls
        else:
            dropped.append(path)  # unknown key: never copied
    return out, info


def mask_recipe(recipe: dict, lexicon: Lexicon | None,
                abbreviations: frozenset[str] | None = None) -> tuple[dict, dict]:
    """A masked copy of *recipe* and ``{"lexicon", "masked", "words", "dropped"}``.

    The copy is built from an allowlist, never by copying the input: schema
    fields with the expected shape, plus the masked free-text fields
    (``title``, ``when_to_use``, ``steps[].note``, ``pitfalls[]``,
    ``narrative``). A free-text field more than ``DROP_THRESHOLD`` masked, or
    any when *lexicon* is ``None``, is dropped: ``title`` and ``when_to_use``
    become ``""``, the others are removed. Unknown keys and badly shaped
    values are dropped too. ``dropped`` lists JSON pointers into the input
    (keys that are not plain identifiers appear as ``{?}``). ``masked`` and
    ``words`` count the kept fields. Never raises; the input is not modified.
    """
    try:
        return _mask_recipe(recipe, lexicon, abbreviations)
    except Exception:
        pass
    try:  # fail safe: keep the structure, drop every free-text field
        out, info = _mask_recipe(recipe, None, abbreviations)
        info["lexicon"] = lexicon.version if isinstance(lexicon, Lexicon) else None
        return out, info
    except Exception:
        return {}, {"lexicon": None, "masked": 0, "words": 0, "dropped": [""]}


# --- Invariant (third layer) ---------------------------------------------------
# Deliberately written apart from the masker: its own tokens, its own
# sentence-start logic and its own character allowlist.

_INV_ALLOWED = frozenset(string.punctuation) - {"|"} | frozenset("¿¡«»“”‘’–—…•·")
_INV_CLOSERS = "\"'”’»)]"
_INV_TOKEN_RE = re.compile(
    r"\{(?:" + "|".join(PLACEHOLDER_TYPES) + r"|\?)\}"
    r"|[^\W\d_]+(?:['’\-][^\W\d_]+)*"
)
_INV_DOMAIN_RE = re.compile(r"[^\W\d_]\.[^\W\d_]{2}")


def _inv_too_much_punctuation(gap: str) -> bool:
    count, i, n = 0, 0, len(gap)
    while i < n:
        c = gap[i]
        if c.isspace():
            i += 1
            continue
        if c == ".":
            j = i
            while j < n and gap[j] == ".":
                j += 1
            count += 1 if j - i == 3 else j - i
            i = j
            continue
        count += 1
        i += 1
    return count >= 4


def _inv_sentence_break(gap: str) -> bool:
    if "\n" in gap:
        return True
    for i, c in enumerate(gap):
        if c in ".!?:":
            j = i + 1
            while j < len(gap) and gap[j] in _INV_CLOSERS:
                j += 1
            if j < len(gap) and gap[j].isspace():
                return True
    return False


def _inv_capitalised(token: str, start: bool, abbreviations: frozenset[str]) -> bool:
    if token.isalpha() and token.isupper() and len(token) <= 4 and fold(token) in abbreviations:
        return False
    for k, part in enumerate(re.split(r"['’\-]", token)):
        if part == "I":
            continue
        capitals = [c.isupper() or c.istitle() for c in part]
        if not any(capitals):
            continue
        if start and k == 0 and capitals[0] and not any(capitals[1:]):
            continue
        return True
    return False


def _inv_text_hit(text: object, abbreviations: frozenset[str]) -> bool:
    if not isinstance(text, str) or len(text) > _MAX_CHARS:
        return True
    if any(c.isdigit() for c in text):
        return True
    normal = unicodedata.normalize("NFKC", text)
    if any(p.search(normal) for p in PII_PATTERNS) or _INV_DOMAIN_RE.search(normal):
        return True
    previous_end = 0
    first = True
    previous = ""
    for m in _INV_TOKEN_RE.finditer(text):
        gap = text[previous_end:m.start()]
        if any(not (c.isspace() or c in _INV_ALLOWED) for c in gap) or _inv_too_much_punctuation(gap):
            return True
        if len(previous) == 1 and previous.isalpha() and gap[:1] == "." and not gap[1:].strip():
            return True  # a name initial: one letter, ".", then a word or placeholder
        start = first or _inv_sentence_break(gap)
        first = False
        previous_end = m.end()
        token = previous = m.group(0)
        if not token.startswith("{") and _inv_capitalised(token, start, abbreviations):
            return True
    tail = text[previous_end:]
    return any(not (c.isspace() or c in _INV_ALLOWED) for c in tail) or _inv_too_much_punctuation(tail)


def _inv_hits(recipe: dict, abbreviations: frozenset[str]) -> Iterator[str]:
    for key, value in recipe.items():
        path = "/" + _key(key)
        if key in ("title", "when_to_use", "narrative"):
            if _inv_text_hit(value, abbreviations):
                yield path
        elif key in _TOP_FIELDS:
            if not _TOP_FIELDS[key](value):
                yield path
        elif key == "plugin":
            if not isinstance(value, dict):
                yield path
                continue
            for k, v in value.items():
                if not (k in _PLUGIN_FIELDS and _PLUGIN_FIELDS[k](v)):
                    yield f"{path}/{_key(k)}"
        elif key == "steps":
            if not isinstance(value, list):
                yield path
                continue
            for i, step in enumerate(value):
                if not isinstance(step, dict):
                    yield f"/steps/{i}"
                    continue
                for k, v in step.items():
                    sub = f"/steps/{i}/{_key(k)}"
                    if k in _STEP_FIELDS:
                        if not _STEP_FIELDS[k](v):
                            yield sub
                    elif k == "slots":
                        if not isinstance(v, list):
                            yield sub
                        else:
                            for j, slot in enumerate(v):
                                if not _slot(slot):
                                    yield f"{sub}/{j}"
                    elif k == "note":
                        if _inv_text_hit(v, abbreviations):
                            yield sub
                    else:
                        yield sub
        elif key == "pitfalls":
            if not isinstance(value, list):
                yield path
                continue
            for i, item in enumerate(value):
                if _inv_text_hit(item, abbreviations):
                    yield f"/pitfalls/{i}"
        else:
            yield path


_POINTER_SEGMENT_RE = re.compile(r"\d{1,4}|\{\?\}")


def _pointer_ok(pointer: object) -> bool:
    """``""`` or ``/seg/seg…`` where each segment is a fixed schema key, an
    array index or ``{?}``."""
    if pointer == "":
        return True
    if not isinstance(pointer, str) or not pointer.startswith("/") or len(pointer) > 200:
        return False
    return all(seg in POINTER_KEYS or _POINTER_SEGMENT_RE.fullmatch(seg) for seg in pointer[1:].split("/"))


def _dropped_hits(dropped: object) -> Iterator[str]:
    if not isinstance(dropped, list):
        yield "/masking/dropped"
        return
    for i, pointer in enumerate(dropped):
        if not _pointer_ok(pointer):
            yield f"/masking/dropped/{i}"


def invariant_hits(recipe: dict, abbreviations: frozenset[str] | None = None,
                   dropped: list | None = None) -> list[str]:
    """JSON-pointer paths of a masked recipe that break the masking guarantees
    (spec §4.5). Every value at every depth is checked: free text must hold no
    digit, no character outside letters, whitespace, the punctuation allowlist
    and ``{type}``/``{?}`` placeholders, no run of four or more punctuation
    marks, no typed-PII match and no capitalised word outside a sentence
    start; structured fields must be in their closed set or fixed shape;
    unknown keys and wrongly shaped containers are hits. Pass the same
    *abbreviations* the masker used, and ``info["dropped"]`` as *dropped* to
    check that every pointer names only fixed schema keys (hits are reported
    as ``/masking/dropped/<i>``). Never raises; fails closed (``[""]``)."""
    try:
        if not isinstance(recipe, dict):
            return [""]
        abbrevs = ABBREVIATIONS if abbreviations is None else abbreviations
        hits = list(_inv_hits(recipe, abbrevs))
        if dropped is not None:
            hits.extend(_dropped_hits(dropped))
        return list(dict.fromkeys(hits))
    except Exception:
        return [""]
