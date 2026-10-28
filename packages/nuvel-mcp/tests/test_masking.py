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


def lex(words=(), numbers=(), dates=(), currency=(), version=None, language="es"):
    f = masking.fold
    if version is None:
        version = f"{language}-2026.10.1"
    return Lexicon(
        words=frozenset(f(w) for w in words),
        numbers=frozenset(f(w) for w in numbers),
        dates=frozenset(f(w) for w in dates),
        currency=frozenset(f(w) for w in currency),
        version=version,
        language=language,
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
    lx = lex({"yes", "i", "m", "think", "so"}, language="en")
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
    assert masked("on the five", lx) == "on the {number}"         # one number word outside [words]


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
    r = mask_text("uno Xx Yy dos", lex({"uno", "dos", "xx", "yy"}))
    assert (r.text, r.masked, r.words) == ("uno {?} dos", 2, 4)
    assert masked("uno xx {?} yy dos", lex({"uno", "dos"})) == "uno {?} dos"
    assert masked("uno xx. Yy dos", lex({"uno", "dos"})) == "uno {?}. {?} dos"  # punctuation is kept


RID = "123e4567-e89b-42d3-a456-426614174000"
IID = "0b8f7a2c-1d3e-4f5a-9b6c-7d8e9f0a1b2c"


def _recipe(**over):
    doc = {
        "schema": "nuvel.recipe/2",
        "recipe_id": RID,
        "install_id": IID,
        "plugin": {"name": "chief-of-staff", "version": "0.4.0"},
        "version": 1,
        "category": "invoicing",
        "language": "es",
        "title": "a bb",
        "when_to_use": "a bb",
        "steps": [{"action": "search", "tool_kind": "email", "slots": ["person"], "note": "a bb"},
                  {"action": "draft", "tool_kind": "docs", "slots": []}],
        "pitfalls": ["a bb", "a bb"],
        "narrative": "a bb",
    }
    doc.update(over)
    return doc


def test_drop_rule():
    five = "uno dos tres cuatro cinco"
    out, info = mask_recipe(_recipe(narrative=five), lex({"a", "bb", "uno", "dos"}))
    assert "narrative" not in out
    assert info["dropped"] == ["/narrative"]
    out, info = mask_recipe(_recipe(narrative=five), lex({"a", "bb", "uno", "dos", "tres"}))
    assert out["narrative"] == "uno dos tres {?}"
    assert info["dropped"] == []
    assert info["masked"] == 2 and info["lexicon"] == "es-2026.10.1"


def test_drop_rule_every_field():
    bad = "x y z a"
    rec = _recipe(title=bad, when_to_use=bad, pitfalls=["a bb", bad, "a"], narrative=bad)
    rec["steps"][0]["note"] = bad
    out, info = mask_recipe(rec, lex({"a", "bb"}))
    assert out["title"] == "" and out["when_to_use"] == ""
    assert "note" not in out["steps"][0]
    assert out["pitfalls"] == ["a bb", "a"]
    assert "narrative" not in out
    assert info["dropped"] == ["/title", "/when_to_use", "/steps/0/note", "/pitfalls/1", "/narrative"]
    assert out["steps"][0]["slots"] == ["person"] and out["steps"][1]["action"] == "draft"


def test_mask_recipe_does_not_mutate_input():
    rec = _recipe(narrative="a pedro")
    before = copy.deepcopy(rec)
    mask_recipe(rec, lex({"a", "bb"}))
    assert rec == before


def test_zero_word_field_not_dropped():
    rec = _recipe(title="", narrative="{person} {email}, {date}.")
    out, info = mask_recipe(rec, lex({"a", "bb"}))
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
    assert out["steps"][1] == rec["steps"][1] and out["recipe_id"] == RID


def test_non_string_free_text_is_dropped():
    rec = _recipe(title={"x": "pedro"}, pitfalls=["a bb", 3], narrative=["pedro"])
    rec["steps"][0]["note"] = 7
    out, info = mask_recipe(rec, lex({"a", "bb"}))
    assert out["title"] == "" and out["pitfalls"] == ["a bb"]
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
    "hablé con 🇲🇽",                          # regional-indicator flag
    "hablé con 🇵🇪🇩🇷🇴",                       # regional indicators spelling a name
    "hablé con 🅿🅴🅳🆁🅾",                       # negative squared letters
    "hablé con ⠏⠑⠙⠗⠕",                       # braille
    "hablé con 😀🎉",                         # other emoji
    "hablé con ᴘᴇᴅʀᴏ",                        # small capitals
    "hablé con p-e-d-r-o",                   # spelled out
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
    out, info = mask_recipe(_recipe(narrative="a pedro bb"), lex({"a", "bb"}))
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
    language = lx.version.split("-")[0]
    for i, text in enumerate(texts):
        out = mask_text(text, lx).text
        rec = {"language": language, "title": out, "when_to_use": out, "steps": [{"note": out}],
               "pitfalls": [out], "narrative": out}
        assert invariant_hits(rec) == [], f"line {i}"
        masked_rec, _ = mask_recipe({"language": language, "narrative": text, "steps": [{"note": text}]},
                                    lx)
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
    assert invariant_hits(rec) == ["/steps/1/note", "/pitfalls/1", "/title"]  # input order


def test_invariant_allows_sentence_starts_and_exceptions():
    rec = {"language": "en", "narrative": "Hecho. El CEO revisa.\n- Hola: Mundo. I think {?} and {person}."}
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


# --- fix round 1 -------------------------------------------------------------

def test_gap_characters_are_deny_by_default():
    lx = lex({"cliente", "hola", "mundo"})
    r = mask_text("cliente 🇲🇽", lx)
    assert (r.text, r.masked, r.words) == ("cliente {?}", 1, 2)
    r = mask_text("hola ★ mundo ☎ hola", lx)
    assert (r.text, r.masked, r.words) == ("hola {?} mundo {?} hola", 2, 5)
    # the closed punctuation set passes untouched
    text = "hola — mundo… «hola» ¿mundo? “hola” ‘mundo’ – hola · mundo • hola; (mundo) [hola] {person}!"
    assert masked(text, lx) == unicodedata_nfkc(text)


def unicodedata_nfkc(s):
    import unicodedata
    return unicodedata.normalize("NFKC", s)


def test_disallowed_gap_counts_toward_drop():
    out, info = mask_recipe(_recipe(narrative="a 🇲🇽 🅿🅴🅳🆁🅾 ⠏⠑⠙ bb"), lex({"a", "bb"}))
    assert "narrative" not in out and "/narrative" in info["dropped"]


def test_mask_recipe_copies_only_allowlisted_keys():
    rec = _recipe(extra="Pedro García", tenant="acme", masking={"x": 1})
    rec["Pedro García"] = "secret"
    rec["plugin"] = {"name": "chief-of-staff", "version": "0.4.0", "owner": "Pedro"}
    rec["steps"][0]["text"] = "pedro@x.com"
    rec["steps"][0]["slots"] = ["person", "Pedro", 3]
    rec["category"] = "Pedro García"
    rec["steps"][1]["action"] = "call pedro"
    rec["steps"][1]["tool_kind"] = "fax"
    out, info = mask_recipe(rec, lex({"a", "bb"}))
    blob = json.dumps([out, info], ensure_ascii=False)
    assert "Pedro" not in blob and "pedro" not in blob and "acme" not in blob
    assert set(out) == {"schema", "recipe_id", "install_id", "plugin", "version", "language",
                        "title", "when_to_use", "steps", "pitfalls", "narrative"}
    assert out["plugin"] == {"name": "chief-of-staff", "version": "0.4.0"}
    assert out["steps"][0] == {"action": "search", "tool_kind": "email", "slots": ["person"], "note": "a bb"}
    assert out["steps"][1] == {"slots": []}
    assert info["dropped"] == [
        "/plugin/{?}", "/category", "/steps/0/slots/1", "/steps/0/slots/2", "/steps/0/{?}",
        "/steps/1/action", "/steps/1/tool_kind", "/{?}", "/{?}", "/{?}", "/{?}",
    ]
    assert invariant_hits(out, dropped=info["dropped"]) == []


@pytest.mark.parametrize("field, value", [
    ("schema", "nuvel.other/2"), ("recipe_id", "pedro"), ("install_id", 5), ("version", True),
    ("version", 0), ("language", "esp"), ("category", "612345678"), ("plugin", "x"),
])
def test_badly_shaped_structured_fields_dropped(field, value):
    out, info = mask_recipe(_recipe(**{field: value}), lex({"a", "bb"}))
    assert field not in out and info["dropped"] == [f"/{field}"]


def test_mask_recipe_output_is_not_aliased_to_input():
    rec = _recipe()
    out, _ = mask_recipe(rec, lex({"a", "bb"}))
    out["steps"][0]["slots"].append("date")
    out["plugin"]["name"] = "x"
    assert rec["steps"][0]["slots"] == ["person"] and rec["plugin"]["name"] == "chief-of-staff"


@pytest.mark.parametrize("rec, hits", [
    ({"extra": "Pedro"}, ["/{?}"]),
    ({"Pedro García": "x"}, ["/{?}"]),
    ({"steps": "Pedro García pedro@x.com"}, ["/steps"]),
    ({"steps": ["Pedro", {"note": "ok"}]}, ["/steps/0"]),
    ({"steps": [{"slots": "person"}]}, ["/steps/0/slots"]),
    ({"steps": [{"slots": ["person", "Pedro"]}]}, ["/steps/0/slots/1"]),
    ({"steps": [{"text": "x", "action": "Pedro"}]}, ["/steps/0/{?}", "/steps/0/action"]),
    ({"pitfalls": "Pedro"}, ["/pitfalls"]),
    ({"plugin": {"name": "x", "owner": "Pedro"}}, ["/plugin/name", "/plugin/{?}"]),
    ({"recipe_id": "Pedro"}, ["/recipe_id"]),
    ({"tenant": "acme"}, ["/{?}"]),
])
def test_invariant_scans_every_value(rec, hits):
    assert invariant_hits(rec) == hits


def test_invariant_accepts_a_full_masked_recipe():
    out, _ = mask_recipe(_recipe(), lex({"a", "bb"}))
    assert invariant_hits(out) == []


@pytest.mark.parametrize("text", [
    "paso 5", "paso ５", "cliente 🇲🇽", "con ⠏⠑⠙⠗⠕", "con 🅿🅴🅳", "visita rosá.com", "con pédro",
    "x​y",
])
def test_invariant_independent_checks(text):
    assert invariant_hits({"narrative": text}) == ["/narrative"]


def test_invariant_takes_the_maskers_abbreviations(monkeypatch):
    monkeypatch.setattr(masking, "ABBREVIATIONS", frozenset())
    iva = frozenset({"iva"})
    lx = lex({"con"})
    assert mask_text("con IVA", lx, abbreviations=iva).text == "con IVA"
    assert invariant_hits({"narrative": "con IVA"}, abbreviations=iva) == []
    assert invariant_hits({"narrative": "con IVA"}) == ["/narrative"]
    out, _ = mask_recipe({"narrative": "con IVA"}, lx, abbreviations=iva)
    assert out["narrative"] == "con IVA"
    assert invariant_hits(out, abbreviations=iva) == []


def test_invariant_does_not_share_the_maskers_break_regex():
    import inspect
    src = inspect.getsource(masking._inv_text_hit) + inspect.getsource(masking._inv_sentence_break)
    assert "_BREAK_RE" not in src and "_tokenise" not in src


def test_idn_domains():
    lx = lex({"visita", "rosa", "com", "e", "g"})
    assert masked("visita rosá.com", lx) == "visita {url}"
    assert masked("visita rosá.com.", lx) == "visita {url}."
    assert masked("visita e.g. rosa", lx) == "visita {?} rosa"   # one-letter word + ".": initials rule


@pytest.mark.parametrize("language, text, expected", [
    ("xx", "con s-o-l", "con {?}"),
    ("xx", "a e i o", "{?}"),
    ("xx", "con a e i o y", "con {?}"),
    ("xx", "con a, e, i o", "con {?}"),
    ("xx", "p-e-d-r-o", "{?}"),
    ("xx", "con e-mail", "con {?}"),
    ("ca", "con e i", "con e i"),
    ("xx", "con e i", "con {?}"),            # no standalone letters in an unknown language
    ("ca", "con l'informe d'avui", "con l'informe d'avui"),
    ("en", "it's I'm don't", "it's I'm don't"),
    ("ca", "con l'o", "con {?}"),
    ("ca", "con x'informe", "con {?}"),
])
def test_spelled_out_names(language, text, expected):
    lx = lex({"con", "s", "o", "l", "a", "e", "i", "p", "d", "r", "y", "mail", "informe", "avui",
              "it", "m", "don", "t", "x"}, language=language)
    assert masked(text, lx) == expected


def test_lexicon_stores_sources(tmp_path):
    p = tmp_path / "es.txt"
    p.write_text(LEXICON_FILE, encoding="utf-8")
    assert masking._load_lexicon_from(p, "es").sources == "base-x, names-y"


def test_lexicon_carries_its_language(tmp_path):
    p = tmp_path / "ca.txt"
    p.write_text("# version: ca-2026.10.1\n# [words]\ninforme\nl\n", encoding="utf-8")
    lx = masking._load_lexicon_from(p, "ca")
    assert lx.language == "ca"
    assert masked("con l'informe", Lexicon(lx.words | {"con"}, lx.numbers, lx.dates, lx.currency,
                                           lx.version, language=lx.language)) == "con l'informe"


@pytest.mark.parametrize("content", [
    "# version: es-2026.10.1\nhola\n# [words]\nmundo\n",           # entry before the first section
    "# version: es-2026.10.1\n# [names]\npedro\n",                 # unknown section
    "# version: es-2026.10.1\n# [words]\ne-mail\n",                # hyphen
    "# version: es-2026.10.1\n# [words]\nl'aigua\n",               # apostrophe
    "# version: es-2026.10.1\n# [dates]\nd’abril\n",               # typographic apostrophe
    "# version: es-2026.10.1\n# [words]\nbuenos dias\n",           # two words
    "# sources: x\n# [words]\nhola\n",                             # no version
])
def test_lexicon_reader_fails_closed(tmp_path, capsys, content):
    p = tmp_path / "es.txt"
    p.write_text(content, encoding="utf-8")
    assert masking._load_lexicon_from(p, "es") is None
    err = capsys.readouterr().err
    assert err.strip() == masking._INVALID_LEXICON
    for secret in ("hola", "mundo", "pedro", "mail", "aigua", "abril", "buenos"):
        assert secret not in err


def test_name_list_folding_is_the_maskers_fold(tmp_path):
    """Task 4 subtracts name lists with ``masking.fold``; lookups use the same fold."""
    import masking_corpus
    samples = ["Rösa", "GARCÍA", "Straße", "Ŀlorenç", "ÉMILE", "Nuñez", "ﬁnance"]
    assert [masking.fold(s) for s in samples] == [masking_corpus.fold(s) for s in samples]
    p = tmp_path / "es.txt"
    p.write_text("# version: es-2026.10.1\n# [words]\nRösa\ncon\n", encoding="utf-8")
    lx = masking._load_lexicon_from(p, "es")
    assert lx.words == frozenset({masking.fold("Rösa"), "con"}) == frozenset({"rosa", "con"})
    # a folded name-list entry removes every accent and case variant
    names = {masking.fold("Rosa")}
    pruned = Lexicon(lx.words - names, lx.numbers, lx.dates, lx.currency, lx.version)
    assert masked("con rösa", pruned) == "con {?}"
    assert masked("con rosa", lx) == "con rosa"


# --- fix round 2 -------------------------------------------------------------

from nuvel_mcp import recipes  # noqa: E402


@pytest.mark.parametrize("path, mutate", [
    ("/steps/0/action", lambda r: r["steps"][0].update(action="call_pedro_garcia")),
    ("/steps/0/action", lambda r: r["steps"][0].update(action="summarize")),
    ("/steps/0/tool_kind", lambda r: r["steps"][0].update(tool_kind="phone")),
    ("/steps/0/slots/0", lambda r: r["steps"][0].update(slots=["pedro"])),
    ("/category", lambda r: r.update(category="llamar_a_pedro_garcia")),
    ("/category", lambda r: r.update(category="sales")),
    ("/plugin/name", lambda r: r["plugin"].update(name="pedro.garcia:6:1:2:3:4:5:6:7:8")),
    ("/plugin/version", lambda r: r["plugin"].update(version="6123.45.678")),
    ("/plugin/version", lambda r: r["plugin"].update(version="0.4")),
    ("/plugin/version", lambda r: r["plugin"].update(version="612.345.678")),
    ("/recipe_id", lambda r: r.update(recipe_id=RID.upper())),
    ("/install_id", lambda r: r.update(install_id="{" + IID + "}")),
    ("/version", lambda r: r.update(version=1001)),
    ("/language", lambda r: r.update(language="ES")),
    ("/schema", lambda r: r.update(schema="nuvel.recipe/1")),
])
def test_identifier_fields_are_closed_sets(path, mutate):
    rec = _recipe()
    mutate(rec)
    out, info = mask_recipe(rec, lex({"a", "bb"}))
    assert info["dropped"] == [path]
    assert "pedro" not in json.dumps(out) and "612" not in json.dumps(out)
    if path == "/language":  # no known language left: the invariant fails closed on lone letters
        assert set(invariant_hits(out, dropped=info["dropped"])) <= {
            "/title", "/when_to_use", "/steps/0/note", "/pitfalls/0", "/pitfalls/1", "/narrative"}
        assert path in invariant_hits(rec)
        return
    assert invariant_hits(out, dropped=info["dropped"]) == []
    assert invariant_hits(rec) == [path]  # the invariant flags the same value


def test_every_closed_value_passes():
    for action in recipes.ACTIONS:
        for category in recipes.CATEGORIES:
            rec = _recipe(category=category)
            rec["steps"][0]["action"] = action
            out, info = mask_recipe(rec, lex({"a", "bb"}))
            assert info["dropped"] == [] and out["category"] == category


def test_closed_lists_match_global_constraints():
    assert recipes.ACTIONS == (
        "search", "read", "summarise", "extract", "compare", "check", "calculate", "list",
        "draft", "edit", "translate", "create", "update", "file", "propose_slot", "schedule",
        "send", "reply", "forward", "share", "remind", "follow_up", "ask", "decide", "wait", "other")
    assert masking.PLACEHOLDER_TYPES == (
        "person", "company", "team", "product", "project", "document", "file", "folder", "amount",
        "number", "date", "time", "period", "place", "email", "phone", "url", "id", "topic", "other")
    assert recipes.PLUGIN_NAMES == ("chief-of-staff",)
    assert len(recipes.CATEGORIES) == len(set(recipes.CATEGORIES)) and recipes.CATEGORIES[-1] == "other"
    import masking_corpus  # noqa: F401  (the corpus category list is the same closed list)
    from test_masking_corpus_format import CATEGORIES as CORPUS_CATEGORIES
    assert set(recipes.CATEGORIES) == CORPUS_CATEGORIES


def test_dropped_pointers_name_only_schema_keys():
    rec = {"pedro_garcia_612345678": 1, "steps": [{"llamar_rosa": 1, "action": "search"}],
           "plugin": {"rosa": 1}, "Pedro García": "x"}
    out, info = mask_recipe(rec, lex({"a"}))
    assert info["dropped"] == ["/{?}", "/steps/0/{?}", "/plugin/{?}", "/{?}"]
    blob = json.dumps([out, info, invariant_hits(rec)])
    assert "pedro" not in blob and "rosa" not in blob and "612" not in blob
    assert invariant_hits({"pedro_garcia": 1}) == ["/{?}"]


@pytest.mark.parametrize("dropped, hits", [
    (["", "/title", "/steps/3/note", "/{?}", "/plugin/{?}", "/pitfalls/0"], []),
    (["/pedro_garcia"], ["/masking/dropped/0"]),
    (["/title", "/steps/0/llamar"], ["/masking/dropped/1"]),
    (["title"], ["/masking/dropped/0"]),
    ([3], ["/masking/dropped/0"]),
    ("/title", ["/masking/dropped"]),
])
def test_invariant_checks_dropped(dropped, hits):
    assert invariant_hits({"title": "ok"}, dropped=dropped) == hits


@pytest.mark.parametrize("language", ["ca", "en", "es", "xx"])
@pytest.mark.parametrize("text", [
    "con t'i'm", "con s'i'd", "con d'l's", "con t’i’m", "con s’i’d", "con d’l’s",
    "con d's",           # no part of two letters
    "con l'informe's",   # a prefix and a suffix elision: no language allows both
])
def test_chained_elisions_masked_in_every_language(language, text):
    lx = lex({"con", "t", "i", "m", "s", "d", "l", "informe"}, language=language)
    assert masked(text, lx) == "con {?}"
    assert invariant_hits({"narrative": masked(text, lx)}) == []


@pytest.mark.parametrize("language", ["en", "es", "xx"])
def test_prefix_elision_on_the_last_part_masked(language):
    # In Catalan "informe'l" is a verb with a suffix clitic (test_ca_clitics_pass).
    lx = lex({"con", "l", "informe"}, language=language)
    assert masked("con informe'l", lx) == "con {?}"


@pytest.mark.parametrize("language, text, expected", [
    ("ca", "con l'informe", "con l'informe"),
    ("ca", "con l’informe d'avui", "con l’informe d'avui"),
    ("ca", "con s'am i n'an, m'an t'an", "con s'am i n'an, m'an t'an"),
    ("ca", "con it'd", "con {?}"),               # English suffixes are not Catalan ('s is a Catalan clitic)
    ("ca", "con I'm", "con {?}"),
    ("en", "con s'am", "con {?}"),               # prefix elisions are Catalan only
    ("en", "con d'an", "con {?}"),
    ("en", "con l'informe", "con {?}"),
    ("en", "I'm here, I'd go, don't", "I'm here, I'd go, don't"),
    ("en", "it's", "it's"),
    ("en", "con it'l", "con {?}"),               # only 's 't 'm 'd
    ("es", "con l'informe", "con {?}"),          # Spanish has no elisions
    ("es", "con it's", "con {?}"),
    ("es", "con I'm", "con {?}"),
    ("xx", "con l'informe", "con {?}"),          # unknown language: none either
    ("xx", "I'm here", "{?} here"),
])
def test_elisions_per_language(language, text, expected):
    lx = lex({"con", "s", "am", "i", "n", "an", "m", "t", "d", "l", "informe", "avui", "it",
              "here", "go", "don"}, language=language)
    assert masked(text, lx) == expected
    assert invariant_hits({"language": language, "narrative": masked(text, lx)}) == []


@pytest.mark.parametrize("text, expected", [
    ("con .--. . -.. .-. ---", "con {?}"),
    ("con |||| ||", "con {?}"),
    ("con | rosa", "con {?} rosa"),
    ("con -.-. rosa", "con {?} rosa"),
    ("con rosa... y rosa", "con rosa... y rosa"),
    ("con rosa… y rosa", "con rosa... y rosa"),
    ("con rosa — y rosa", "con rosa — y rosa"),
    ("con rosa -- y rosa", "con rosa -- y rosa"),
    ("con rosa, (y) rosa.", "con rosa, (y) rosa."),
    ("con rosa.\n\n- rosa", "con rosa.\n\n- rosa"),
    ("con rosa .... y", "con rosa {?} y"),
    ("con rosa . . . . y", "con rosa {?} y"),
])
def test_punctuation_runs(text, expected):
    lx = lex({"con", "rosa", "y"})
    assert masked(text, lx) == expected
    assert invariant_hits({"language": "es", "narrative": masked(text, lx)}) == []


@pytest.mark.parametrize("text", [
    "con .--. . -.. .-. ---", "con |||| ||", "con | rosa", "con rosa .... y", "con rosa . . . . y",
])
def test_invariant_flags_punctuation_runs(text):
    assert invariant_hits({"narrative": text}) == ["/narrative"]


# --- Task 4: corpus-gate rules ------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("el pedido PO-55102 hoy", "el pedido {id} hoy"),        # the letter prefix is part of the id
    ("la factura F-2026-0412 hoy", "la factura {id} hoy"),
    ("el pedido po-55102/a hoy", "el pedido {id} hoy"),
    ("el pedido 55102/a hoy", "el pedido {number} hoy"),
    ("el pedido ACME-X-7 hoy", "el pedido {id} hoy"),
    ("el pedido sin-número hoy", "el pedido sin-número hoy"),     # no digit: an ordinary word
])
def test_letter_prefixed_ids_are_one_token(text, expected):
    lx = lex({"el", "pedido", "la", "factura", "hoy", "po", "acme", "x", "sin", "numero"})
    assert masked(text, lx) == expected
    assert invariant_hits({"narrative": masked(text, lx)}) == []


def test_letter_prefixed_id_regex_is_bounded():
    # quadratic in the hyphen-part starts at worst; the 8000-character cap bounds it
    import time
    start = time.perf_counter()
    for text in ("a-" * 3999 + "a", "ab-" * 2600 + "x", ("a-" * 1000 + " ") * 3):
        mask_text(text, lex({"a"}))
    assert time.perf_counter() - start < 2


def test_lone_number_word_outside_words_is_a_number():
    lx = lex({"para", "meses", "una", "carta", "con"},
             numbers={"una", "catorce", "dos", "veinte", "mil"}, currency={"euros"})
    r = mask_text("para catorce meses", lx)
    assert (r.text, r.masked, r.words) == ("para {number} meses", 0, 3)
    assert masked("una carta con dos", lx) == "una carta con {number}"   # "una" is in [words]
    assert masked("veinte mil euros", lx) == "{amount}"


# --- Task 4 fix round 1: name initials ---------------------------------------

@pytest.mark.parametrize("language, text, expected", [
    ("en", "M. Smith reviews it", "{?} reviews it"),
    ("en", "send it to m. jones", "send it to {?}"),
    ("en", "D. Jones will call", "{?} will call"),
    ("en", "ask m.jones today", "ask {url} today"),             # a domain shape, typed first
    ("en", "ask m. {person} today", "ask {?} {person} today"),
    ("es", "J. García revisa", "{?} revisa"),
    ("es", "habla con j. garcía", "habla con {?}"),
    ("en", "the plan a. Then send it", "the plan {?} send it"),
    ("en", "plan a.", "plan a."),                          # nothing after the dot: not an initial
    ("en", "send it to a jones", "send it to a {?}"),
    ("es", "rosa y pedro o juan", "rosa y {?} o {?}"),
])
def test_name_initials_masked(language, text, expected):
    lx = lex({"m", "d", "j", "a", "y", "o", "send", "it", "to", "reviews", "will", "call", "ask",
              "today", "revisa", "habla", "con", "the", "plan", "then", "rosa"}, language=language)
    assert masked(text, lx) == expected
    assert invariant_hits({"language": language, "narrative": masked(text, lx)}) == []


@pytest.mark.parametrize("text", [
    "M. {?} reviews it", "send it to m. jones", "ask j. {person}", "con J.García", "plan a.\nNext",
])
def test_invariant_flags_initials(text):
    assert invariant_hits({"narrative": text}) == ["/narrative"]


@pytest.mark.parametrize("text", ["plan a.", "it is a. ", "con {?}. Hola", "I. "])
def test_invariant_allows_single_letter_without_following_word(text):
    assert invariant_hits({"language": "en", "narrative": text}) == []


# --- Task 4 fix round 2: standalone one-letter words --------------------------

@pytest.mark.parametrize("language, text, expected", [
    ("en", "we spoke with m.", "we spoke with {?}."),
    ("en", "send it to m jones", "send it to {?}"),
    ("en", "send it to m., jones", "send it to {?}., {?}"),
    ("en", "see m.- jones", "see {?}.- {?}"),
    ("en", "it is a plan, I think", "it is a plan, I think"),
    ("en", "it's I'm don't", "it's I'm don't"),          # elision letters still pass inside words
    ("ca", "l'informe i la d", "l'informe i la {?}"),
    ("ca", "a e i o", "{?}"),                             # a run of three or more is still masked
    ("ca", "rosa i pere o joan", "rosa i {?} o {?}"),
    ("es", "rosa y la e", "rosa y la e"),
    ("es", "rosa y la i", "rosa y la {?}"),
    ("xx", "rosa y la a", "rosa {?} la {?}"),             # unknown language: no standalone letter
])
def test_standalone_single_letters(language, text, expected):
    lx = lex({"we", "spoke", "with", "m", "send", "it", "to", "see", "is", "a", "plan", "i", "think",
              "s", "t", "d", "l", "don", "informe", "la", "rosa", "e", "o", "y"}, language=language)
    assert masked(text, lx) == expected
    assert invariant_hits({"language": language, "narrative": masked(text, lx)}) == []


@pytest.mark.parametrize("language, text", [
    ("en", "we spoke with m."), ("en", "send it to m {?}"), ("ca", "la d"), ("es", "la i"),
    ("xx", "rosa y"), (None, "rosa y"),
])
def test_invariant_flags_lone_letters_outside_the_standalone_set(language, text):
    rec = {"narrative": text} if language is None else {"language": language, "narrative": text}
    assert "/narrative" in invariant_hits(rec)


# --- Catalan clitics (spec addendum 2026-10-28) --------------------------------

CA_CLITIC_LEXICON = {"envia", "fes", "recorda", "dona", "porta", "compara", "con", "informe", "me", "li",
                     "ho", "ne", "la", "a"}


@pytest.mark.parametrize("text", [
    "envia'l", "fes-ho", "recorda'm", "dona-li",
    "envia'ls", "envia'ns", "envia'n", "porta-ho", "dona-me-la", "porta-me'l", "compara'ls-hi",
    "fes-hi", "fes-ne", "fes-te", "fes-se", "fes-nos", "fes-vos", "fes-los", "fes-les", "fes-lo",
    "Envia'l a la porta",                   # sentence start
    "envia’l", "recorda’m",                 # typographic apostrophe
])
def test_ca_clitics_pass(text):
    lx = lex(CA_CLITIC_LEXICON | {"porta"}, language="ca")
    assert masked(text, lx) == text
    assert invariant_hits({"language": "ca", "narrative": masked(text, lx)}) == []


@pytest.mark.parametrize("text", [
    "envia'l'm'n",         # three apostrophe clitics: a chain spelling a name
    "envia'l'm",           # at most one apostrophe-suffix group
    "fes-me-li-ho-ne",     # four hyphen clitics
    "fes-me-li-ho",        # at most two
    "pere-li",             # the stem is not a lexicon word
    "pere'l",
    "a-li", "a'l",         # a stem of one letter
    "l'envia'l",           # a prefix elision and a suffix clitic together
    "envia'L",             # a capitalised clitic mid-sentence
    "envia'x", "fes-xo",   # not clitics: the generic rule applies
])
def test_ca_clitic_chains(text):
    lx = lex(CA_CLITIC_LEXICON | {"l", "m", "n", "x"}, language="ca")
    assert masked("con " + text, lx) == "con {?}"


@pytest.mark.parametrize("language", ["en", "es", "xx"])
def test_ca_clitics_are_catalan_only(language):
    lx = lex(CA_CLITIC_LEXICON | {"l"}, language=language)
    assert masked("con envia'l", lx) == "con {?}"   # en has 's 't 'm 'd only; es and xx none
    assert masked("con envia'ls", lx) == "con {?}"


@pytest.mark.parametrize("text", [
    "envia'l'm'n", "envia'l'm", "fes-me-li-ho-ne", "fes-me-li-ho", "l'envia'l", "a-li", "a'l",
    "con envia'ls'hi i tot",
])
def test_invariant_flags_ca_clitic_chains(text):
    assert "/narrative" in invariant_hits({"language": "ca", "narrative": text})


@pytest.mark.parametrize("text", ["envia'l", "fes-ho", "dona-me-la", "porta-me'l", "pere-li"])
def test_invariant_allows_ca_clitics(text):
    # The invariant checks shape only: "pere-li" is caught by the masker's lexicon.
    assert invariant_hits({"language": "ca", "narrative": text}) == []


# --- Dates: weekdays joined by a comma or "dia", Catalan "u" and elided numbers --

DATE_LEXICON = dict(
    words={"el", "la", "de", "dia", "ens", "veiem", "paga", "nos", "vemos", "entrega", "a", "i", "y",
           "see", "you", "on", "the", "un", "una", "maig", "dilluns", "lunes", "mayo"},
    numbers={"vint", "trenta", "onze", "cinc", "cinco", "un", "una", "five"},
    dates={"maig", "dilluns", "dimecres", "octubre", "lunes", "mayo", "monday", "may"},
)


@pytest.mark.parametrize("language, text, expected", [
    ("ca", "ens veiem dilluns, 5 de maig", "ens veiem {date}"),
    ("ca", "ens veiem el dilluns dia 5 de maig", "ens veiem el {date}"),
    ("ca", "ens veiem dimecres dia 12", "ens veiem {date}"),
    ("ca", "ens veiem el 5 de maig, dilluns", "ens veiem el {date}"),
    ("ca", "ens veiem dilluns, a la tarda", "ens veiem dilluns, a la {?}"),   # a bare weekday passes
    ("es", "nos vemos el lunes, 5 de mayo", "nos vemos el {date}"),
    ("es", "nos vemos el lunes día 5", "nos vemos el {date}"),
    ("en", "see you on monday, may 5", "see you on {date}"),
])
def test_weekday_joined_to_a_date_by_comma_or_day_word(language, text, expected):
    lx = lex(language=language, **DATE_LEXICON)
    assert masked(text, lx) == expected


@pytest.mark.parametrize("text, expected", [
    ("ens veiem el vint-i-u de maig", "ens veiem el {date}"),
    ("ens veiem el trenta-u de maig", "ens veiem el {date}"),
    ("ens veiem l'u de maig", "ens veiem {date}"),
    ("ens veiem l'onze de maig", "ens veiem {date}"),
    ("ens veiem l’onze de maig", "ens veiem {date}"),
    ("paga el dia u", "paga el dia {number}"),
    ("entrega l'octubre de 2027", "entrega {date}"),
])
def test_catalan_numeral_u_and_elided_numbers(text, expected):
    lx = lex(language="ca", **DATE_LEXICON)
    assert masked(text, lx) == expected


@pytest.mark.parametrize("language", ["es", "en", "xx"])
def test_numeral_u_and_number_elisions_are_catalan_only(language):
    lx = lex(language=language, **DATE_LEXICON)
    assert masked("el dia u", lx) == "el dia {?}"
    assert masked("de l'onze", lx) == "de {?}"


# --- Fix round 1: fixed Catalan pronoun combinations, separators, other languages ---

CA_FIXED = ["n'hi", "s'hi", "m'hi", "t'hi", "l'hi", "s'ho", "m'ho", "t'ho", "l'ho", "li'n", "li'l",
            "li'ls", "n'he", "se'n", "me'n", "te'n"]


@pytest.mark.parametrize("word", CA_FIXED + [w.replace("'", "’") for w in CA_FIXED])
def test_ca_fixed_pronoun_combinations_pass(word):
    lx = lex({"ha", "molts", "va"}, language="ca")
    assert masked(f"{word} ha molts", lx) == f"{word} ha molts"
    assert invariant_hits({"language": "ca", "narrative": f"{word} ha molts"}) == []


def test_ca_fixed_combinations_case_and_scope():
    lx = lex({"ha", "molts", "ara"}, language="ca")
    assert masked("N'hi ha molts", lx) == "N'hi ha molts"          # sentence start
    assert masked("ara N'hi ha", lx) == "ara {?} ha"                 # capital mid-sentence
    assert masked("ara x'hi ha", lx) == "ara {?} ha"                 # not a fixed combination
    for language in ("es", "en"):
        assert masked("ara n'hi ha", lex({"ha", "ara", "hi"}, language=language)) == "ara {?} ha"


@pytest.mark.parametrize("text, expected", [
    ("envia’l", "envia’l"),            # U+2019 is an apostrophe
    ("enviaʼl", "{?}"),                # U+02BC is a letter: one unknown word
    ("envia′l", "envia{?}"),           # U+2032 prime: not an apostrophe
    ("fes‑ho", "fes{?}ho"),            # U+2011 non-breaking hyphen: not a word joiner
])
def test_ca_clitic_separators(text, expected):
    lx = lex(CA_CLITIC_LEXICON, language="ca")
    assert masked(text, lx) == expected
    assert invariant_hits({"language": "ca", "narrative": masked(text, lx)}) == []


@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize("text", ["envia'l'm'n", "fes-me-li-ho-ne", "a-li"])
def test_invariant_clitic_rule_is_off_outside_catalan(language, text):
    assert invariant_hits({"language": language, "narrative": text}) == []
    assert "/narrative" in invariant_hits({"language": "ca", "narrative": text})


@pytest.mark.parametrize("language", ["es", "en"])
def test_masker_clitic_rule_is_off_outside_catalan(language):
    # Outside ca a hyphenated word passes when every part is a lexicon word.
    lx = lex({"fes", "me", "li", "ho", "ne"}, language=language)
    assert masked("fes-me-li-ho-ne", lx) == "fes-me-li-ho-ne"
