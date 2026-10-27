"""nuvel.recipe_signal/1 and nuvel.recipe/2 schemas, validator keywords and the signal screen."""

import json
from pathlib import Path

import pytest

from nuvel_mcp import masking, recipes, traces

UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
UUID_C = "33333333-3333-4333-8333-333333333333"
PLUGIN = {"name": "chief-of-staff", "version": "0.3.0"}
FIXTURE = Path(__file__).parent / "fixtures" / "chief_of_staff" / "recipe.json"

SIGNAL = {
    "schema": "nuvel.recipe_signal/1", "signal_id": UUID_A, "recipe_id": UUID_B, "install_id": UUID_C,
    "plugin": PLUGIN, "category": "client-followup", "event": "used",
    "at": "2026-10-27T10:00:00Z", "uses": 3, "successes": 2, "corrections": 0,
}
STEP = {"action": "draft", "tool_kind": "email", "slots": ["person", "topic"],
        "note": "Redactar el resumen para {person}"}
RECIPE = {
    "schema": "nuvel.recipe/2", "recipe_id": UUID_B, "install_id": UUID_C, "plugin": PLUGIN,
    "version": 1, "category": "client-followup", "language": "es",
    "title": "Seguimiento tras una reunión", "when_to_use": "Cuando hay que cerrar acuerdos con el cliente",
    "steps": [STEP],
    "pitfalls": ["No enviar sin revisar"],
    "narrative": "El usuario pidió un resumen de la reunión con {company}.",
}
SKILL_RUN = {
    "schema": "nuvel.skill_run/1", "run_id": UUID_A, "install_id": UUID_C, "plugin": PLUGIN,
    "skill": {"name": "brief", "version": "0.3.0"}, "host": "claude-code", "mode": "interactive",
    "started_at": "2026-10-27T10:00:00Z", "duration_s": 5, "outcome": "success",
}
ACTIONS = [
    "search", "read", "summarise", "extract", "compare", "check", "calculate",
    "list", "draft", "edit", "translate", "create", "update", "file", "propose_slot",
    "schedule", "send", "reply", "forward", "share", "remind", "follow_up", "ask",
    "decide", "wait", "other",
]
PLACEHOLDERS = [
    "person", "company", "team", "product", "project", "document", "file",
    "folder", "amount", "number", "date", "time", "period", "place", "email",
    "phone", "url", "id", "topic", "other",
]


def _schema():
    return traces.load_schema(recipes.RECIPE_RESOURCE)


def _step_schema():
    return _schema()["properties"]["steps"]["items"]["properties"]


def test_signal_valid():
    assert recipes.validate_signal(SIGNAL) == []


def test_recipe_valid():
    assert recipes.validate_recipe(RECIPE) == []
    minimal = {k: v for k, v in RECIPE.items() if k not in ("pitfalls", "narrative")}
    minimal["steps"] = [{"action": "draft", "tool_kind": "email", "slots": []}]
    assert recipes.validate_recipe(minimal) == []


def test_schema_ids():
    assert recipes.SIGNAL_SCHEMA_ID == "nuvel.recipe_signal/1"
    assert recipes.RECIPE_SCHEMA_ID == "nuvel.recipe/2"
    assert recipes.RECIPE_RESOURCE == "schemas/recipe.v2.schema.json"
    assert _schema()["properties"]["schema"]["const"] == recipes.RECIPE_SCHEMA_ID == masking.SCHEMA_ID


def test_v1_schema_removed():
    from importlib import resources
    assert not resources.files("nuvel_mcp").joinpath("schemas/recipe.v1.schema.json").is_file()
    assert not hasattr(recipes, "screen_recipe")


def test_v2_schema_accepts_fixture():
    assert recipes.validate_recipe(json.loads(FIXTURE.read_text())) == []


def test_v1_shape_rejected():
    v1_step = {"action": "draft", "tool_kind": "email", "text": "Redactar el resumen"}
    errors = recipes.validate_recipe(RECIPE | {"steps": [v1_step]})
    assert "/steps/0/text" in errors and "/steps/0/slots" in errors
    assert recipes.validate_recipe(RECIPE | {"schema": "nuvel.recipe/1"}) == ["/schema"]


def test_action_enum_exact():
    assert _step_schema()["action"]["enum"] == ACTIONS == list(recipes.ACTIONS)


def test_placeholder_enum_exact():
    assert _step_schema()["slots"]["items"]["enum"] == PLACEHOLDERS == list(masking.PLACEHOLDER_TYPES)


def test_schema_enums_match_code_constants():
    schema = _schema()
    props = schema["properties"]
    assert props["category"]["enum"] == list(recipes.CATEGORIES)
    assert props["plugin"]["properties"]["name"]["enum"] == list(recipes.PLUGIN_NAMES)
    assert props["plugin"]["properties"]["version"]["pattern"] == r"^\d{1,2}\.\d{1,2}\.\d{1,2}$"
    assert _step_schema()["tool_kind"]["enum"] == list(recipes.TOOL_KINDS)
    assert frozenset(recipes.TOOL_KINDS) == masking._TOOL_KINDS
    assert _step_schema()["action"]["enum"] == list(recipes.ACTIONS)
    assert _step_schema()["slots"]["items"]["enum"] == list(masking.PLACEHOLDER_TYPES)
    assert "tenant" not in props and "masking" not in props


def test_server_fields_rejected_by_schema():
    assert recipes.validate_recipe(RECIPE | {"tenant": "x"}) == ["/tenant"]
    masking_stamp = {"lexicon": "es-1", "masked": 0, "words": 1, "dropped": []}
    assert recipes.validate_recipe(RECIPE | {"masking": masking_stamp}) == ["/masking"]


def test_signal_rejects_tenant():
    errors = recipes.validate_signal(SIGNAL | {"tenant": "x"})
    assert "/tenant" in errors
    assert "x" not in "".join(errors).replace("tenant", "")


def test_recipe_rejects_nested_extra():
    assert recipes.validate_recipe(RECIPE | {"steps": [STEP | {"extra": 1}]}) == ["/steps/0/extra"]
    plugin = PLUGIN | {"extra": 1}
    assert recipes.validate_recipe(RECIPE | {"plugin": plugin}) == ["/plugin/extra"]


def test_new_signal_category_accepted_by_schema():
    assert recipes.validate_signal(SIGNAL | {"category": "brand-new-thing"}) == []


def test_recipe_closed_identifier_sets():
    assert recipes.validate_recipe(RECIPE | {"category": "brand-new-thing"}) == ["/category"]
    assert recipes.validate_recipe(RECIPE | {"plugin": PLUGIN | {"name": "other"}}) == ["/plugin/name"]
    assert recipes.validate_recipe(RECIPE | {"steps": [STEP | {"action": "call_pedro"}]}) == ["/steps/0/action"]
    assert recipes.validate_recipe(RECIPE | {"steps": [STEP | {"slots": ["pedro"]}]}) == ["/steps/0/slots/0"]


@pytest.mark.parametrize("version,ok", [
    ("0.3.0", True), ("10.20.30", True), ("100.0.0", False), ("612.345.678", False), ("1.2", False),
])
def test_plugin_semver(version, ok):
    errors = recipes.validate_recipe(RECIPE | {"plugin": PLUGIN | {"version": version}})
    assert errors == ([] if ok else ["/plugin/version"])


def test_signal_bad_event_and_counter():
    assert recipes.validate_signal(SIGNAL | {"event": "exploded"}) == ["/event"]
    assert recipes.validate_signal(SIGNAL | {"uses": 100001}) == ["/uses"]


def test_recipe_title_too_long():
    assert "/title" in recipes.validate_recipe(RECIPE | {"title": "x" * 81})
    assert recipes.validate_recipe(RECIPE | {"title": "x" * 80}) == []


def test_recipe_needs_a_step():
    assert "/steps" in recipes.validate_recipe(RECIPE | {"steps": []})


def test_recipe_limits():
    assert recipes.validate_recipe(RECIPE | {"steps": [STEP] * 15}) == []
    assert recipes.validate_recipe(RECIPE | {"steps": [STEP] * 16}) == ["/steps"]
    assert recipes.validate_recipe(RECIPE | {"pitfalls": ["a"] * 6}) == ["/pitfalls"]
    assert recipes.validate_recipe(RECIPE | {"pitfalls": ["a" * 161]}) == ["/pitfalls/0"]
    assert recipes.validate_recipe(RECIPE | {"when_to_use": "a" * 201}) == ["/when_to_use"]
    assert recipes.validate_recipe(RECIPE | {"narrative": "a" * 2000}) == []
    assert recipes.validate_recipe(RECIPE | {"narrative": "a" * 2001}) == ["/narrative"]
    assert recipes.validate_recipe(RECIPE | {"steps": [STEP | {"note": "a" * 161}]}) == ["/steps/0/note"]
    assert recipes.validate_recipe(RECIPE | {"language": "ESP"}) == ["/language"]
    assert recipes.validate_recipe(RECIPE | {"version": 0}) == ["/version"]
    assert recipes.validate_recipe(RECIPE | {"steps": [STEP | {"tool_kind": "fax"}]}) == ["/steps/0/tool_kind"]


def test_slots_limits_and_unique():
    four = ["person", "company", "date", "amount"]
    assert recipes.validate_recipe(RECIPE | {"steps": [STEP | {"slots": four}]}) == []
    five = four + ["topic"]
    assert recipes.validate_recipe(RECIPE | {"steps": [STEP | {"slots": five}]}) == ["/steps/0/slots"]
    dup = ["person", "person"]
    assert recipes.validate_recipe(RECIPE | {"steps": [STEP | {"slots": dup}]}) == ["/steps/0/slots"]


def test_unique_items_keyword():
    schema = {"type": "array", "uniqueItems": True, "items": {"type": "object"}}
    traces.check_schema_keywords(schema)
    errors: list[str] = []
    traces._validate([{"a": 1, "b": 2}, {"b": 2, "a": 1}], schema, "/x", errors)
    assert errors == ["/x"]
    errors = []
    traces._validate([1, True], {"type": "array", "uniqueItems": True}, "/x", errors)
    assert errors == []  # JSON: 1 and true are different values
    errors = []
    traces._validate(["a", "b"], {"type": "array", "uniqueItems": False}, "/x", errors)
    assert errors == []


def test_skill_run_still_validates():
    assert traces.validate_skill_run(SKILL_RUN) == []
    assert traces.validate_skill_run(SKILL_RUN | {"tenant": "x"}) == ["/tenant"]


def test_new_schemas_pass_keyword_check():
    for resource in (recipes.SIGNAL_RESOURCE, recipes.RECIPE_RESOURCE):
        traces.check_schema_keywords(traces.load_schema(resource))


def test_screen_signal():
    assert recipes.screen_signal(SIGNAL) == []
    assert recipes.screen_signal(SIGNAL | {"category": "ventas-600123456"}) == ["/category"]
    assert recipes.screen_signal(SIGNAL | {"category": "x@acme.com"}) == ["/category"]


def test_screen_signal_closed_lists():
    assert recipes.screen_signal(SIGNAL | {"category": "llamar-a-pedro"}) == ["/category"]
    assert recipes.screen_signal(SIGNAL | {"category": "sales"}) == ["/category"]
    plugin = {"name": "pedro.garcia", "version": "0.3.0"}
    assert recipes.screen_signal(SIGNAL | {"plugin": plugin}) == ["/plugin/name"]
    both = SIGNAL | {"plugin": plugin, "category": "x"}
    assert recipes.screen_signal(both) == ["/plugin/name", "/category"]
    for category in recipes.CATEGORIES:
        assert recipes.screen_signal(SIGNAL | {"category": category}) == []
