"""Build ``nuvel_mcp/lexicon/{lang}.txt`` from permissively licensed sources.

Not shipped. Run from ``packages/nuvel-mcp`` with an isolated interpreter,
because the cache holds untrusted downloads::

    python -I scripts/build_lexicon.py --lang es --size 20000 --cache <dir> [--fetch]

Steps (spec §4.2):

1. every source file listed in ``lexicon_src/sources.json`` is read from the
   cache (downloaded first with ``--fetch``) and its sha256 verified; a
   mismatch stops the build;
2. the base: the top ``--size`` entries of the language's frequency sources
   (``rank`` lists keep their own order, ``count`` lists are merged by
   relative frequency);
3. entries are folded with ``nuvel_mcp.masking.fold`` and only alphabetic
   entries of two or more letters are kept;
4. every entry of the language's given-name, surname, place and brand lists
   (folded the same way) is subtracted;
5. ``business-{lang}.txt`` is added, minus any entry found in a name list
   (enforced here, and reported);
6. the elision letters the masker needs for that language are added, and the
   file is written with the deterministic version id from ``sources.json``.

The first 500 removed base entries (in base order) go to
``lexicon_src/removed-sample-{lang}.txt`` for the corpus gate. Nothing in this
script looks at the masking corpus.
"""

from __future__ import annotations

import argparse
import bz2
import csv
import hashlib
import io
import json
import re
import sys
import tarfile
import unicodedata
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
SRC = HERE / "lexicon_src"
OUT = PKG / "src" / "nuvel_mcp" / "lexicon"

# Same fold as nuvel_mcp.masking.fold (a test pins the two together); copied so
# that this script does not import the package from an isolated interpreter.
def fold(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


# Single letters the masker's elision rules need (masking._ELISIONS), plus the
# ``i`` of ``I``/``I'm``/``I'd`` in English. No other one-letter entries.
ELISION_LETTERS = {"ca": "ldsnmt", "en": "stmdi", "es": ""}

# Number words are typed (spec §4.1 step 2): a lone one outside [words]
# becomes {number}. Only the indefinite articles stay in [words].
NUMBER_WORDS_KEPT = {"ca": {"un", "una"}, "en": set(), "es": {"un", "una"}}

_WORD = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")
_PARTS = re.compile(r"['’\-]")
USER_AGENT = "nuvel-lexicon-build/1 (+https://github.com/Folken2/nuvel)"
REMOVED_SAMPLE = 500


def _alpha(entry: str) -> bool:
    return len(entry) >= 2 and entry.isalpha()


def _parts(token: str) -> list[str]:
    """Folded parts of a token split at ``'``/``’``/``-``, two letters or more."""
    return [p for p in (fold(x) for x in _PARTS.split(token)) if _alpha(p)]


# --- cache -------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cached(cache: Path, source: dict, entry: dict, fetch: bool) -> Path:
    path = cache / source["id"] / entry["name"]
    if not path.is_file():
        if not fetch:
            sys.exit(f"missing {path} (run with --fetch to download {entry['url']})")
        path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(entry["url"], headers={"User-Agent": USER_AGENT,
                                                            "Accept": "text/csv, */*"})
        with urllib.request.urlopen(req, timeout=600) as resp, path.open("wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
    digest = _sha256(path)
    if digest != entry["sha256"]:
        sys.exit(f"sha256 mismatch for {source['id']}/{entry['name']}: {digest}")
    return path


# --- readers: each yields (token, weight) or tokens ------------------------------

def read_conllu(path: Path) -> Counter:
    """Surface forms that appear in lowercase and are not tagged PROPN. A
    multiword token (``1-2 del``) counts once, as its surface form."""
    counts: Counter = Counter()
    skip_until = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                skip_until = 0  # sentence boundary
                continue
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 4:
                continue
            idx, form, upos = cols[0], cols[1], cols[3]
            if "-" in idx:
                _, hi = idx.split("-", 1)
                skip_until = int(hi)
                if form == form.lower():
                    for p in _parts(form):
                        counts[p] += 1
                continue
            if "." in idx:
                continue
            if idx.isdigit() and int(idx) <= skip_until:
                continue
            if upos == "PROPN" or form != form.lower():
                continue
            for p in _parts(form):
                counts[p] += 1
    return counts


def read_scowl(path: Path, spec: dict) -> list[str]:
    """SCOWL ``final/`` word lists, in level order, then alphabetical. Only
    lowercase entries; contractions contribute their parts (``doesn``)."""
    levels = spec["levels"]
    prefixes = spec["lists"]
    by_level: dict[int, set[str]] = {lv: set() for lv in levels}
    with tarfile.open(path, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or "/final/" not in member.name:
                continue
            base = member.name.rsplit("/", 1)[1]
            stem, _, level = base.rpartition(".")
            if stem not in prefixes or not level.isdigit() or int(level) not in by_level:
                continue
            data = tar.extractfile(member).read().decode("latin-1")
            for line in data.splitlines():
                w = line.strip()
                if not w or w != w.lower():
                    continue
                by_level[int(level)].update(_parts(w))
    ordered, seen = [], set()
    for lv in levels:
        for w in sorted(by_level[lv]):
            if w not in seen:
                seen.add(w)
                ordered.append(w)
    return ordered


def _single_token_names(values, min_count: int = 1, all_caps: bool = False):
    """Single-token entries only (multi-word labels such as ``De la Fuente``
    or ``La Coruña`` would remove function words). A hyphenated or
    apostrophised token (``Ribera-Solé``, ``O'Brien``, ``El-Aaiún``) gives
    each part that is capitalised and has three letters or more, so the
    particles and articles inside it stay out."""
    out = set()
    for value, count in values:
        if count < min_count:
            continue
        value = unicodedata.normalize("NFC", value.strip())
        if not value or any(c.isspace() for c in value):
            continue
        if not _WORD.fullmatch(value):
            continue
        if not all_caps and not value[0].isupper():
            continue  # lowercase labels are not names (``van``, ``de``)
        pieces = [p for p in _PARTS.split(value) if p]
        if len(pieces) == 1:
            out.update(_parts(value))
            continue
        for piece in pieces:
            if len(piece) >= 3 and (all_caps or piece[0].isupper()):
                out.update(_parts(piece))
    return out


def read_wikidata_csv(path: Path, spec: dict) -> set[str]:
    with path.open(encoding="utf-8", newline="") as fh:
        rows = csv.reader(fh)
        header = next(rows)
        has_count = len(header) > 1
        values = ((r[0], int(r[1]) if has_count else 1) for r in rows if r)
        return _single_token_names(values, spec.get("min_count", 1))


def read_geonames_cities(path: Path, spec: dict) -> set[str]:
    countries = set(spec["countries"])
    values = []
    with zipfile.ZipFile(path) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".txt"))
        for line in io.TextIOWrapper(zf.open(name), encoding="utf-8"):
            cols = line.rstrip("\n").split("\t")
            if (len(cols) > 14 and cols[8] in countries and int(cols[14] or 0) >= spec["min_population"]
                    and cols[7] not in spec.get("exclude_feature_codes", ())):
                values.append((cols[1], 1))
                values.append((cols[2], 1))
    return _single_token_names(values)


def read_geonames_countries(path: Path, spec: dict) -> set[str]:
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        values.append((cols[4], 1))   # country name
        values.append((cols[5], 1))   # capital
    return _single_token_names(values)


def read_census_zip(path: Path, spec: dict) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        rows = csv.reader(io.TextIOWrapper(zf.open(spec["member"]), encoding="latin-1"))
        next(rows)
        values = ((r[0], int(r[2]) if r[2].isdigit() else 0) for r in rows if r)
        return _single_token_names(values, spec.get("min_count", 1), all_caps=True)


def read_bcn_csv(path: Path, spec: dict) -> set[str]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = csv.DictReader(fh)
        values = ((r[spec["column"]], 1) for r in rows)
        return _single_token_names(values, all_caps=True)


def read_scowl_names(path: Path, spec: dict) -> set[str]:
    prefixes = spec["lists"]
    values = []
    with tarfile.open(path, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or "/final/" not in member.name:
                continue
            stem, _, level = member.name.rsplit("/", 1)[1].rpartition(".")
            if stem in prefixes and level.isdigit() and int(level) <= spec["max_level"]:
                data = tar.extractfile(member).read().decode("latin-1")
                values.extend((w.strip(), 1) for w in data.splitlines())
    return _single_token_names(values)


def read_tatoeba(path: Path) -> Counter:
    """Words of the sentences, counted where they appear in lowercase."""
    counts: Counter = Counter()
    with bz2.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 3:
                continue
            for tok in _WORD.findall(cols[2]):
                if tok == tok.lower():
                    for p in _parts(tok):
                        counts[p] += 1
    return counts


NAME_READERS = {
    "wikidata-csv": read_wikidata_csv,
    "geonames-cities": read_geonames_cities,
    "geonames-countries": read_geonames_countries,
    "census-zip": read_census_zip,
    "bcn-csv": read_bcn_csv,
    "scowl-names": read_scowl_names,
}


# --- build -------------------------------------------------------------------

def _read_list(name: str) -> list[str]:
    path = SRC / name
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        out.extend(line.split("#", 1)[0].split())
    return out


def _uses(source: dict, lang: str) -> dict | None:
    return source.get("use", {}).get(lang)


def base_words(config: dict, lang: str, cache: Path, fetch: bool) -> tuple[list[str], list[str]]:
    """The language's base list in rank order, and the ids of its sources."""
    ranked: list[list[str]] = []
    counts: list[Counter] = []
    ids = []
    for source in config["sources"]:
        use = _uses(source, lang)
        if source["kind"] != "base" or use is None:
            continue
        ids.append(source["id"])
        fmt = source["format"]
        if fmt == "conllu":
            c: Counter = Counter()
            for entry in source["files"]:
                c.update(read_conllu(cached(cache, source, entry, fetch)))
            counts.append(c)
        elif fmt == "tatoeba":
            counts.append(read_tatoeba(cached(cache, source, source["files"][0], fetch)))
        elif fmt == "scowl":
            ranked.append(read_scowl(cached(cache, source, source["files"][0], fetch), use))
        else:
            sys.exit(f"unknown base format {fmt}")
    order: list[str] = []
    if counts:
        merged: Counter = Counter()
        for c in counts:  # equal weight per source: relative frequency
            total = sum(c.values())
            for w, n in c.items():
                merged[w] += n / total
        order = [w for w, _ in sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))]
    for r in ranked:
        seen = set(order)
        order.extend(w for w in r if w not in seen)
    return order, ids


def name_set(config: dict, lang: str, cache: Path, fetch: bool) -> tuple[set[str], list[str], dict]:
    names: set[str] = set()
    ids, sizes = [], {}
    for source in config["sources"]:
        use = _uses(source, lang)
        if source["kind"] not in ("names", "places", "brands") or use is None:
            continue
        reader = NAME_READERS[source["format"]]
        got: set[str] = set()
        for entry in source["files"]:
            got |= reader(cached(cache, source, entry, fetch), use)
        ids.append(source["id"])
        sizes[source["id"]] = len(got)
        names |= got
    return names, ids, sizes


def write_abbreviations(out_dir: Path) -> None:
    """Copy the reviewed abbreviation list: all-caps words of two to four letters."""
    entries = _read_list("abbreviations.txt")
    if any(not (e.isalpha() and e.isupper() and 2 <= len(e) <= 4) for e in entries):
        sys.exit("abbreviations.txt: entries must be 2-4 capital letters")
    (out_dir / "abbreviations.txt").write_text(
        "# Reviewed all-caps abbreviations that pass the capitalisation rule (spec §4.1.5).\n"
        "# Copied from scripts/lexicon_src/abbreviations.txt by build_lexicon.py.\n"
        + "\n".join(sorted(set(entries))) + "\n", encoding="utf-8")


def write_sources_md(config: dict, out_dir: Path) -> None:
    """Attribution for the packaged lexicons, generated from sources.json."""
    rows = [
        "# Lexicon sources",
        "",
        "Generated from `scripts/lexicon_src/sources.json` by `scripts/build_lexicon.py`.",
        "Every source meets the licence rule: public domain or CC0, permissive OSI",
        "licences, or CC BY without share-alike or non-commercial terms. No CC-BY-SA,",
        "GPL, LGPL or MPL source is used. Each licence was checked at its licence page",
        "on the date given. Name, place and brand lists are only subtracted from the",
        "base lists; none of their entries ships.",
        "",
        "| id | kind | licence | checked | languages | licence page | contributes |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in config["sources"]:
        langs = ", ".join(sorted(s.get("use", {})))
        rows.append(f"| `{s['id']}` | {s['kind']} | {s['licence']} | {s['checked']} | {langs} "
                    f"| {s['licence_url']} | {s['what']} |")
    rows += ["", "## Files", "", "Long query URLs are shortened here; `sources.json` has them in full.", ""]
    for s in config["sources"]:
        for f in s["files"]:
            url = f["url"] if len(f["url"]) <= 160 else f["url"][:160] + "…"
            rows.append(f"- `{s['id']}`: <{url}> (sha256 `{f['sha256']}`)")
        if s.get("notes"):
            rows.append(f"  - {s['notes']}")
    rows += ["", "## Attribution", ""]
    rows += [f"- {line}" for line in config.get("attribution", [])]
    rows += ["", "## Languages", ""]
    for lang, status in sorted(config["shipped"].items()):
        rows.append(f"- `{lang}`: {status}")
    rows += ["", "Hand-written inputs (`business-*`, `numbers-*`, `dates-*`, `currency-*`,",
             "`abbreviations.txt`) are part of this repository, under its MIT licence.", ""]
    (out_dir / "SOURCES.md").write_text("\n".join(rows), encoding="utf-8")


def build(lang: str, size: int, cache: Path, fetch: bool, out_dir: Path, sample_dir: Path) -> dict:
    config = json.loads((SRC / "sources.json").read_text(encoding="utf-8"))
    version = config["versions"][lang]
    if not version.startswith(lang + "-"):
        sys.exit("version id does not match the language")

    order, base_ids = base_words(config, lang, cache, fetch)
    names, name_ids, sizes = name_set(config, lang, cache, fetch)

    words: list[str] = []
    removed: list[str] = []
    seen: set[str] = set()
    for w in order:
        if not _alpha(w) or w in seen:
            continue
        if len(seen) >= size:
            break
        seen.add(w)
        if w in names:
            removed.append(w)
        else:
            words.append(w)

    business = [fold(w) for w in _read_list(f"business-{lang}.txt")]
    business_dropped = sorted({w for w in business if w in names})
    bad_business = [w for w in business if not _alpha(w)]
    if bad_business:
        sys.exit(f"business-{lang}.txt: {len(bad_business)} entries are not single alphabetic words")
    sections = {
        "numbers": sorted({fold(w) for w in _read_list(f"numbers-{lang}.txt")}),
        "dates": sorted({fold(w) for w in _read_list(f"dates-{lang}.txt")}),
        "currency": sorted({fold(w) for w in _read_list(f"currency-{lang}.txt")}),
    }
    wordset = set(words) | {w for w in business if w not in names}
    wordset -= set(sections["numbers"]) - NUMBER_WORDS_KEPT[lang]
    wordset |= set(ELISION_LETTERS[lang])
    assert not (wordset - set(ELISION_LETTERS[lang])) & names
    for key, entries in sections.items():
        if any(not (e.isalpha() and len(e) >= 2) for e in entries):
            sys.exit(f"{key}-{lang}.txt: entries must be single alphabetic words of two letters or more")

    lines = [
        f"# version: {version}",
        f"# sources: {', '.join(base_ids + name_ids)}",
        "# Generated by packages/nuvel-mcp/scripts/build_lexicon.py; do not edit by hand.",
        "# Attribution and licences: SOURCES.md.",
        "# [words]",
        *sorted(wordset),
        "# [numbers]",
        *sections["numbers"],
        "# [dates]",
        *sections["dates"],
        "# [currency]",
        *sections["currency"],
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    write_abbreviations(out_dir)
    write_sources_md(config, out_dir)
    (out_dir / f"{lang}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sample = removed[:REMOVED_SAMPLE]
    (sample_dir / f"removed-sample-{lang}.txt").write_text(
        f"# First {len(sample)} base entries removed because a name, place or brand list has them.\n"
        + "\n".join(sample) + "\n", encoding="utf-8")
    return {
        "lang": lang, "version": version, "size": size, "words": len(wordset),
        "base_considered": len(seen), "removed": len(removed), "names": len(names),
        "name_sources": sizes, "business_dropped": business_dropped,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--lang", required=True, choices=("es", "en", "ca"))
    ap.add_argument("--size", type=int, required=True, help="base entries to consider (top N)")
    ap.add_argument("--cache", type=Path, required=True, help="download cache, outside the repo")
    ap.add_argument("--fetch", action="store_true", help="download missing sources")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--sample-dir", type=Path, default=SRC)
    args = ap.parse_args(argv)
    cache = args.cache.resolve()
    if PKG.parents[1] in cache.parents or cache == PKG.parents[1]:
        sys.exit("the cache must be outside the repository")
    report = build(args.lang, args.size, cache, args.fetch, args.out, args.sample_dir)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
