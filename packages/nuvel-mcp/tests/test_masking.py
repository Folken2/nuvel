"""nuvel_mcp.masking: the allowlist masker for recipe free text (spec §4).

Every test injects a tiny Lexicon; none reads package data.
"""

import copy
import json

import pytest

from masking_corpus import load_corpus
from nuvel_mcp import masking
from nuvel_mcp.masking import (
    DROP_THRESHOLD,
    READABILITY_FLOOR,
    READABILITY_TARGET,
    Lexicon,
    MaskResult,
    invariant_hits,
    mask_recipe,
    mask_text,
)


def lex(words=(), numbers=(), dates=(), currency=(), version="xx-2026.10.1"):
    f = masking.fold
    return Lexicon(
        words=frozenset(f(w) for w in words),
        numbers=frozenset(f(w) for w in numbers),
        dates=frozenset(f(w) for w in dates),
        currency=frozenset(f(w) for w in currency),
        version=version,
    )


@pytest.fixture(autouse=True)
def _abbreviations(monkeypatch):
    monkeypatch.setattr(masking, "ABBREVIATIONS", frozenset({"ceo", "iva", "crm"}))


def masked(text, lexicon):
    return mask_text(text, lexicon).text


def test_constants():
    assert DROP_THRESHOLD == 0.40
    assert READABILITY_TARGET == 0.85
    assert READABILITY_FLOOR == 0.75


def test_lowercase_name_masked():
    r = mask_text("hablé con pedro", lex({"hablé", "con"}))
    assert r == MaskResult("hablé con {?}", 1, 3)


def test_capitalised_common_word_masked_mid_sentence():
    assert masked("el informe de Rosa", lex({"el", "informe", "de", "rosa"})) == "el informe de {?}"


def test_sentence_start_passes():
    lx = lex({"hecho", "el", "revisa"})
    r = mask_text("Hecho. El CEO revisa", lx)
    assert r.text == "Hecho. El CEO revisa"
    assert r.masked == 0 and r.words == 4


@pytest.mark.parametrize("text, expected", [
    ("Revisa:\n- Hola mundo", "Revisa:\n- Hola mundo"),
    ("hola: Mundo", "hola: Mundo"),
    ("hola\n• Mundo", "hola\n• Mundo"),
    ("hola, Mundo", "hola, {?}"),
    ("hola mundo. ¿Hola?", "hola mundo. ¿Hola?"),
    ("hola.Mundo hola", "{url} hola"),           # no whitespace: a bare domain, not a sentence
    ("hola HOLA", "hola {?}"),                    # all caps, not an abbreviation
    ("HOLA mundo", "{?} mundo"),                  # all caps at sentence start too
    ("hola McMundo", "hola {?}"),
])
def test_sentence_start_rules(text, expected):
    assert masked(text, lex({"hola", "mundo", "revisa", "mcmundo"})) == expected


def test_pronoun_i_passes():
    lx = lex({"yes", "i", "m", "think", "so"})
    assert masked("yes I think so, I'm", lx) == "yes I think so, I'm"


@pytest.mark.parametrize("text, expected", [
    ("escribe a pedro@acme.com hoy", "escribe a {email} hoy"),
    ("escribe a pedro＠acme.com hoy", "escribe a {email} hoy"),
    ("mira https://acme.com/x?y=1.", "mira {url}."),
    ("mira www.acme.com hoy", "mira {url} hoy"),
    ("mira acme.es hoy", "mira {url} hoy"),
    ("mira mail.acme-corp.co.uk/ruta hoy", "mira {url} hoy"),
    ("llama al +34 612 345 678 hoy", "llama al {phone} hoy"),
    ("iban ES91 2100 0418 4502 0005 1332", "iban {id}"),
    ("ref 1234567 hoy", "ref {id} hoy"),
    ("son 1.250 € hoy", "son {amount} hoy"),
    ("son 125.000 € hoy", "son {amount} hoy"),
    ("son 300 euros hoy", "son {amount} hoy"),
    ("son €5.000,50 hoy", "son {amount} hoy"),
    ("son EUR 300 hoy", "son {amount} hoy"),
    ("en 3 fases", "en {number} fases"),
    ("versión v2 hoy", "versión {number} hoy"),
    ("a las 14:30 hoy", "a las {number} hoy"),
    ("en ３ fases", "en {number} fases"),
])
def test_typed_patterns(text, expected):
    words = {"escribe", "a", "hoy", "mira", "llama", "al", "iban", "ref", "son",
             "en", "fases", "versión", "las"}
    assert masked(text, lex(words)) == expected


def test_typed_spans_count_as_words_not_masked():
    r = mask_text("escribe a pedro@acme.com", lex({"escribe", "a"}))
    assert (r.text, r.masked, r.words) == ("escribe a {email}", 0, 3)


def test_number_and_date_words():
    lx = lex({"son", "dos", "opciones", "el", "de", "los", "lunes", "y", "antes", "del"},
             numbers={"dos", "veinte", "mil", "cinco", "quince", "veintitres", "doce", "quinientos"},
             dates={"marzo", "lunes", "noviembre", "abril"},
             currency={"euros"})
    assert masked("son veinte mil euros", lx) == "son {amount}"
    assert masked("son doce mil quinientos euros", lx) == "son {amount}"
    assert masked("son dos euros", lx) == "son {amount}"
    assert masked("son veinte mil", lx) == "son {number}"
    assert masked("son veinte y cinco", lx) == "son {number}"
    assert masked("son veinte mil de euros", lx) == "son {amount}"
    assert masked("son 20 mil euros", lx) == "son {amount}"
    assert masked("dos opciones", lx) == "dos opciones"
    assert masked("el 15 de marzo", lx) == "el {date}"
    assert masked("el quince de marzo", lx) == "el {date}"
    assert masked("antes del 14 de noviembre", lx) == "antes del {date}"
    assert masked("el lunes veintitrés", lx) == "el {date}"
    assert masked("el lunes 14 de noviembre de 2026", lx) == "el {date}"
    assert masked("los lunes", lx) == "los lunes"
    assert masked("el 15 d'abril", lx) == "el {date}"


def test_number_words_english_and_catalan():
    lx = lex({"it", "costs", "on", "the", "costa"},
             numbers={"twenty", "five", "thousand", "vint", "cinc"},
             dates={"march"}, currency={"dollars", "euros"})
    assert masked("it costs twenty-five thousand dollars", lx) == "it costs {amount}"
    assert masked("costa vint-i-cinc euros", lx) == "costa {amount}"
    assert masked("on March 15th", lx) == "on {date}"
    assert masked("on the twenty-five", lx) == "on the {number}"  # two number words in one token
    assert masked("on the five", lx) == "on the {?}"              # one number word: lexicon decides


def test_hyphen_parts_all_checked():
    assert masked("con garcía-lópez", lex({"con", "garcia"})) == "con {?}"
    assert masked("con garcía-lópez", lex({"con", "lopez"})) == "con {?}"
    assert masked("con garcía-lópez", lex({"con", "garcia", "lopez"})) == "con garcía-lópez"
    assert masked("con García-López", lex({"con", "garcia", "lopez"})) == "con {?}"
    assert masked("con o'brien", lex({"con", "o"})) == "con {?}"


def test_client_mask_token_masked():
    r = mask_text("hola {?} mundo", lex({"hola", "mundo"}))
    assert (r.text, r.masked, r.words) == ("hola {?} mundo", 1, 3)


def test_unknown_placeholder_masked():
    r = mask_text("hola {foo} {person}", lex({"hola"}))
    assert (r.text, r.masked, r.words) == ("hola {?} {person}", 1, 2)


def test_collapse():
    r = mask_text("a X Y b", lex({"a", "b", "x", "y"}))
    assert (r.text, r.masked, r.words) == ("a {?} b", 2, 4)
    assert masked("a x {?} y b", lex({"a", "b"})) == "a {?} b"
    assert masked("a x. Y b", lex({"a", "b"})) == "a {?}. {?} b"    # punctuation is kept


def _recipe(**over):
    doc = {
        "schema": "nuvel.recipe/2",
        "recipe_id": "r1",
        "title": "a b",
        "when_to_use": "a b",
        "steps": [{"action": "search", "tool_kind": "email", "slots": ["person"], "note": "a b"},
                  {"action": "draft", "tool_kind": "docs", "slots": []}],
        "pitfalls": ["a b", "a b"],
        "narrative": "a b",
    }
    doc.update(over)
    return doc


def test_drop_rule():
    out, info = mask_recipe(_recipe(narrative="a b c d e"), lex({"a", "b"}))
    assert "narrative" not in out
    assert info["dropped"] == ["/narrative"]
    out, info = mask_recipe(_recipe(narrative="a b c d e"), lex({"a", "b", "c"}))
    assert out["narrative"] == "a b c {?}"
    assert info["dropped"] == []
    assert info["masked"] == 2 and info["lexicon"] == "xx-2026.10.1"


def test_drop_rule_every_field():
    bad = "x y z a"
    rec = _recipe(title=bad, when_to_use=bad, pitfalls=["a b", bad, "a"], narrative=bad)
    rec["steps"][0]["note"] = bad
    out, info = mask_recipe(rec, lex({"a", "b"}))
    assert out["title"] == "" and out["when_to_use"] == ""
    assert "note" not in out["steps"][0]
    assert out["pitfalls"] == ["a b", "a"]
    assert "narrative" not in out
    assert info["dropped"] == ["/title", "/when_to_use", "/steps/0/note", "/pitfalls/1", "/narrative"]
    assert out["steps"][0]["slots"] == ["person"] and out["steps"][1]["action"] == "draft"


def test_mask_recipe_does_not_mutate_input():
    rec = _recipe(narrative="a pedro")
    before = copy.deepcopy(rec)
    mask_recipe(rec, lex({"a", "b"}))
    assert rec == before


def test_zero_word_field_not_dropped():
    rec = _recipe(title="", narrative="{person} {email}, {date}.")
    out, info = mask_recipe(rec, lex({"a", "b"}))
    assert out["title"] == "" and out["narrative"] == "{person} {email}, {date}."
    assert info["dropped"] == []
    r = mask_text("{person} {email}", lex())
    assert (r.masked, r.words) == (0, 0)
    assert mask_text("", lex()) == MaskResult("", 0, 0)


def test_no_lexicon_drops_all_text():
    rec = _recipe()
    out, info = mask_recipe(rec, None)
    assert out["title"] == "" and out["when_to_use"] == ""
    assert "note" not in out["steps"][0] and "narrative" not in out
    assert out["pitfalls"] == []
    assert info == {"lexicon": None, "masked": 0, "words": 0,
                    "dropped": ["/title", "/when_to_use", "/steps/0/note",
                                "/pitfalls/0", "/pitfalls/1", "/narrative"]}
    assert out["steps"][1] == rec["steps"][1] and out["recipe_id"] == "r1"


def test_non_string_free_text_is_dropped():
    rec = _recipe(title={"x": "pedro"}, pitfalls=["a b", 3], narrative=["pedro"])
    rec["steps"][0]["note"] = 7
    out, info = mask_recipe(rec, lex({"a", "b"}))
    assert out["title"] == "" and out["pitfalls"] == ["a b"]
    assert "narrative" not in out and "note" not in out["steps"][0]
    assert info["dropped"] == ["/title", "/steps/0/note", "/pitfalls/1", "/narrative"]


HOSTILE = [
    "hablé con pe‍dro",                 # zero-width joiner inside the name
    "hablé con pe​dro",                 # zero-width space
    "hablé con pédro",                 # combining accent
    "hablé con r̈osa",                  # a mark that has no precomposed form
    "hablé con ｐｅｄｒｏ",                    # fullwidth letters
    "hablé con ‮pedro‬",           # RTL override
    "hablé con ‏pedro‎",           # RTL / LTR marks
    "hablé con pedro­",                 # soft hyphen
    "hablé con Реdro",                       # Cyrillic look-alikes
    "hablé con " + "pedro" * 400,            # a 2000-character token
]


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_unicode(text):
    lx = lex({"hablé", "con", "r", "osa", "pe", "dro"})
    r = mask_text(text, lx)
    assert r.text == "hablé con {?}"
    assert r.masked == 1


@pytest.mark.parametrize("text", [
    "", "\x00\x01\x7f", "\ud800 lone surrogate", "{" * 3000, "@" * 5000, "a@" * 3000,
    "1" * 5000, "." * 5000, "ﷺ" * 1000, "\n" * 100, "x" * 100_000, "e" * 4000 + "@b",
    "{?}" * 500, "́" * 50, "ES12" + "A" * 3000,
])
def test_never_raises(text):
    r = mask_text(text, lex({"a"}))
    assert isinstance(r, MaskResult)
    out, info = mask_recipe(_recipe(narrative=text), lex({"a"}))
    assert isinstance(out, dict) and isinstance(info["dropped"], list)
    assert invariant_hits(out) == []


@pytest.mark.parametrize("doc", [None, 3, "pedro", [], {"steps": "x"}, {"steps": [None, "pedro", {"note": None}]},
                                 {"pitfalls": "pedro"}, {"title": None}])
def test_mask_recipe_never_raises_on_odd_shapes(doc):
    out, info = mask_recipe(doc, lex({"a"}))
    assert isinstance(out, dict)
    assert "pedro" not in json.dumps(out)
    assert set(info) == {"lexicon", "masked", "words", "dropped"}


def test_non_str_text_fails_safe():
    r = mask_text(None, lex({"a"}))  # type: ignore[arg-type]
    assert r.text == "{?}" and r.masked == r.words == 1


def test_internal_error_drops_field(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(masking, "_tokenise", boom)
    r = mask_text("hablé con pedro", lex({"hablé", "con"}))
    assert r == MaskResult("{?}", 1, 1)
    out, info = mask_recipe(_recipe(narrative="a pedro b"), lex({"a", "b"}))
    assert "narrative" not in out and "/narrative" in info["dropped"]
    assert "pedro" not in json.dumps(out)


def test_overlong_text_fails_safe():
    r = mask_text("a " * 10_000, lex({"a"}))
    assert r == MaskResult("{?}", 1, 1)


# --- invariant ---------------------------------------------------------------

SMALL = lex(
    {"el", "la", "de", "del", "con", "para", "por", "en", "a", "y", "que", "the", "to", "and", "of",
     "for", "with", "on", "in", "i", "per", "amb", "les", "els", "factura", "invoice", "send", "envia",
     "manda", "prepara", "propuesta", "proposal", "rosa", "pilar", "bill", "rose", "mercedes"},
    numbers={"dos", "tres", "mil", "veinte", "doce", "quinientos", "two", "three", "thousand",
             "twenty", "fifteen", "dues", "tres", "vint", "mil"},
    dates={"marzo", "noviembre", "lunes", "march", "monday", "november", "març", "dilluns"},
    currency={"euros", "dollars", "dolares", "pounds"},
)


def _corpus_texts():
    return [c.text for lang in ("es", "en", "ca") for c in load_corpus("tune", lang)]


def _everything_lexicon():
    """Every word token of the tune corpus, secrets included: only the
    capitalisation rule and the typed patterns stand between them and the output."""
    from masking_corpus import tokens
    words = {masking.fold(t) for text in _corpus_texts() for t in tokens(text)}
    return Lexicon(frozenset(words), SMALL.numbers, SMALL.dates, SMALL.currency, "xx-2026.10.1")


@pytest.mark.parametrize("which", ["small", "everything"])
def test_invariant_clean_on_masker_output(which):
    lx = SMALL if which == "small" else _everything_lexicon()
    texts = _corpus_texts()
    assert len(texts) >= 270
    for i, text in enumerate(texts):
        out = mask_text(text, lx).text
        rec = {"title": out, "when_to_use": out, "steps": [{"note": out}], "pitfalls": [out],
               "narrative": out}
        assert invariant_hits(rec) == [], f"line {i}"
        masked_rec, _ = mask_recipe({"narrative": text, "steps": [{"note": text}]}, lx)
        assert invariant_hits(masked_rec) == [], f"line {i}"


def test_invariant_flags_planted():
    rec = {"title": "ok", "steps": [{"note": "fine"}], "pitfalls": ["fine"],
           "narrative": "escribe a pedro@acme.com"}
    assert invariant_hits(rec) == ["/narrative"]


@pytest.mark.parametrize("field, value, path", [
    ("title", "el informe de Rosa", "/title"),
    ("when_to_use", "ver acme.es", "/when_to_use"),
    ("narrative", "llama al +34 612 345 678", "/narrative"),
    ("narrative", "el ceo Y yo", "/narrative"),
    ("narrative", "iban ES91 2100 0418 4502 0005 1332", "/narrative"),
])
def test_invariant_flags_each_kind(field, value, path):
    assert invariant_hits({field: value}) == [path]


def test_invariant_paths_for_steps_and_pitfalls():
    rec = {"steps": [{"note": "ok"}, {"note": "con Pedro"}], "pitfalls": ["ok", "x@y.com"],
           "title": 3}
    assert invariant_hits(rec) == ["/title", "/steps/1/note", "/pitfalls/1"]


def test_invariant_allows_sentence_starts_and_exceptions():
    rec = {"narrative": "Hecho. El CEO revisa.\n- Hola: Mundo. I think {?} and {person}."}
    assert invariant_hits(rec) == []


def test_invariant_does_not_use_capitalised_run():
    assert invariant_hits({"narrative": "Hecho. El CEO revisa el informe"}) == []


# --- lexicon loading ---------------------------------------------------------

LEXICON_FILE = """\
# version: es-2026.10.1
# sources: base-x, names-y
# [words]
hola
Méndez
# a comment inside a section
mundo

# [numbers]
dos
# [dates]
Marzo
# [currency]
euros
"""


def test_load_lexicon_from_file(tmp_path):
    p = tmp_path / "es.txt"
    p.write_text(LEXICON_FILE, encoding="utf-8")
    lx = masking._load_lexicon_from(p, "es")
    assert lx.version == "es-2026.10.1"
    assert lx.words == frozenset({"hola", "mendez", "mundo"})
    assert lx.numbers == frozenset({"dos"})
    assert lx.dates == frozenset({"marzo"})
    assert lx.currency == frozenset({"euros"})


@pytest.mark.parametrize("content", [
    "",
    "# [words]\nhola\n",                                  # no version
    "# version: es-2026.10\n# [words]\nhola\n",           # bad version
    "# version: en-2026.10.1\n# [words]\nhola\n",         # wrong language
])
def test_load_lexicon_rejects_bad_files(tmp_path, content):
    p = tmp_path / "es.txt"
    p.write_text(content, encoding="utf-8")
    assert masking._load_lexicon_from(p, "es") is None


def test_load_lexicon_missing_file(tmp_path):
    assert masking._load_lexicon_from(tmp_path / "nope.txt", "es") is None


@pytest.mark.parametrize("lang", ["zz", "../recipes", "es/../es", "", "ES", None, 3])
def test_load_lexicon_unknown_or_hostile_language(lang):
    assert masking.load_lexicon(lang) is None


def test_load_abbreviations(tmp_path):
    p = tmp_path / "abbreviations.txt"
    p.write_text("# reviewed list\nCEO\nIVA\n\n", encoding="utf-8")
    assert masking._load_abbreviations_from(p) == frozenset({"ceo", "iva"})
    assert masking._load_abbreviations_from(tmp_path / "missing.txt") == frozenset()
    assert isinstance(masking.ABBREVIATIONS, frozenset)


def test_mask_text_uses_injected_abbreviations():
    lx = lex({"el", "revisa"})
    assert masked("el CRM revisa", lx) == "el CRM revisa"
    assert mask_text("el CRM revisa", lx, abbreviations=frozenset()).text == "el {?} revisa"
    assert masked("el CEOS revisa", lx) == "el {?} revisa"
