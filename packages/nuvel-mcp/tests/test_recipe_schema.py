"""nuvel.recipe_signal/1 and nuvel.recipe/1 schemas, validator keywords and the PII screen."""

import pytest

from nuvel_mcp import recipes, traces

UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
UUID_C = "33333333-3333-4333-8333-333333333333"
PLUGIN = {"name": "chief-of-staff", "version": "0.3.0"}

SIGNAL = {
    "schema": "nuvel.recipe_signal/1", "signal_id": UUID_A, "recipe_id": UUID_B, "install_id": UUID_C,
    "plugin": PLUGIN, "category": "client-followup", "event": "used",
    "at": "2026-10-27T10:00:00Z", "uses": 3, "successes": 2, "corrections": 0,
}
RECIPE = {
    "schema": "nuvel.recipe/1", "recipe_id": UUID_B, "install_id": UUID_C, "plugin": PLUGIN,
    "version": 1, "category": "client-followup", "language": "es",
    "title": "Seguimiento tras una reunión", "when_to_use": "Cuando hay que cerrar acuerdos con el cliente",
    "steps": [{"action": "draft", "tool_kind": "email", "text": "Redactar el resumen para el cliente"}],
    "pitfalls": ["No enviar sin revisar"],
}
SKILL_RUN = {
    "schema": "nuvel.skill_run/1", "run_id": UUID_A, "install_id": UUID_C, "plugin": PLUGIN,
    "skill": {"name": "brief", "version": "0.3.0"}, "host": "claude-code", "mode": "interactive",
    "started_at": "2026-10-27T10:00:00Z", "duration_s": 5, "outcome": "success",
}


def test_signal_valid():
    assert recipes.validate_signal(SIGNAL) == []


def test_recipe_valid():
    assert recipes.validate_recipe(RECIPE) == []
    assert recipes.validate_recipe({k: v for k, v in RECIPE.items() if k != "pitfalls"}) == []


def test_schema_ids():
    assert recipes.SIGNAL_SCHEMA_ID == "nuvel.recipe_signal/1"
    assert recipes.RECIPE_SCHEMA_ID == "nuvel.recipe/1"


def test_signal_rejects_tenant():
    errors = recipes.validate_signal(SIGNAL | {"tenant": "x"})
    assert "/tenant" in errors
    assert "x" not in "".join(errors).replace("tenant", "")


def test_recipe_rejects_extra_property_and_nested_extra():
    assert "/tenant" in recipes.validate_recipe(RECIPE | {"tenant": "x"})
    step = {"action": "draft", "tool_kind": "email", "text": "ok", "extra": 1}
    assert recipes.validate_recipe(RECIPE | {"steps": [step]}) == ["/steps/0/extra"]


def test_new_category_accepted():
    assert recipes.validate_signal(SIGNAL | {"category": "brand-new-thing"}) == []


def test_signal_bad_event_and_counter():
    assert recipes.validate_signal(SIGNAL | {"event": "exploded"}) == ["/event"]
    assert recipes.validate_signal(SIGNAL | {"uses": 100001}) == ["/uses"]


def test_recipe_title_too_long():
    assert "/title" in recipes.validate_recipe(RECIPE | {"title": "x" * 81})
    assert recipes.validate_recipe(RECIPE | {"title": "x" * 80}) == []


def test_recipe_needs_a_step():
    assert "/steps" in recipes.validate_recipe(RECIPE | {"steps": []})


def test_recipe_limits():
    step = RECIPE["steps"][0]
    assert recipes.validate_recipe(RECIPE | {"steps": [step] * 16}) == ["/steps"]
    assert recipes.validate_recipe(RECIPE | {"pitfalls": ["a"] * 6}) == ["/pitfalls"]
    assert recipes.validate_recipe(RECIPE | {"pitfalls": ["a" * 161]}) == ["/pitfalls/0"]
    assert recipes.validate_recipe(RECIPE | {"language": "ESP"}) == ["/language"]
    assert recipes.validate_recipe(RECIPE | {"version": 0}) == ["/version"]
    bad = dict(step, tool_kind="fax")
    assert recipes.validate_recipe(RECIPE | {"steps": [bad]}) == ["/steps/0/tool_kind"]


def test_skill_run_still_validates():
    assert traces.validate_skill_run(SKILL_RUN) == []
    assert traces.validate_skill_run(SKILL_RUN | {"tenant": "x"}) == ["/tenant"]


def test_new_schemas_pass_keyword_check():
    for resource in (recipes.SIGNAL_RESOURCE, recipes.RECIPE_RESOURCE):
        traces.check_schema_keywords(traces.load_schema(resource))


def _with_text(text):
    return RECIPE | {"steps": [{"action": "draft", "tool_kind": "email", "text": text}]}


@pytest.mark.parametrize("text", [
    "Enviar a Marta García el resumen", "Escribir a marta@acme.com", "Pagar 1.250 €",
    "Llamar al +34 600 123 456", "Abrir https://acme.com/x", "IBAN ES9121000418450200051332",
])
def test_screen_blocks(text):
    assert recipes.screen_recipe(_with_text(text)) == ["/steps/0/text"]


@pytest.mark.parametrize("text", ["Revisar la propuesta con el cliente", "Preparar el resumen de la reunión"])
def test_screen_allows_generic(text):
    assert recipes.screen_recipe(_with_text(text)) == []


def test_screen_valid_recipe_is_clean():
    assert recipes.screen_recipe(RECIPE) == []


def test_screen_covers_all_text_fields_in_order_without_values():
    recipe = RECIPE | {
        "title": "Escribir a a@b.co", "when_to_use": "Ver www.acme.com",
        "steps": [{"action": "draft", "tool_kind": "email", "text": "Pagar $50"}] * 2,
        "pitfalls": ["ok", "Llamar a Juan Pérez"],
    }
    found = recipes.screen_recipe(recipe)
    assert found == ["/title", "/when_to_use", "/steps/0/text", "/steps/1/text", "/pitfalls/1"]
    assert not any("@" in p or "Juan" in p for p in found)


def test_screen_allows_capitalised_word_at_start():
    assert recipes.screen_recipe(_with_text("Revisar. Cliente Importante")) == ["/steps/0/text"]
    assert recipes.screen_recipe(_with_text("Cliente Importante pide algo")) == []


@pytest.mark.parametrize("text", [
    "Enviar a Ana García-López el resumen", "Enviar a Juan O'Brien", "Enviar a Juan O’Brien",
    "Pagar EUR 1250", "Pagar USD 300", "Pagar GBP 20", "Pagar 300 eur", "Pagar 300 euros",
    "Pagar 20 dólares", "Pagar ¥300", "Abrir acme.com/precios", "Mirar acme.es",
    "Escribir a marta＠acme.com",
])
def test_screen_blocks_round1(text):
    assert recipes.screen_recipe(_with_text(text)) == ["/steps/0/text"]


@pytest.mark.parametrize("text", [
    "p. ej. revisar el acta", "e.g. check the agenda", "versión 2.1", "Revisar el acta, etc.",
    "Calcular 3,5 de media",
])
def test_screen_allows_round1(text):
    assert recipes.screen_recipe(_with_text(text)) == []


def test_screen_action_field():
    step = {"action": "llamar_600123456", "tool_kind": "none", "text": "Revisar"}
    assert recipes.screen_recipe(RECIPE | {"steps": [step]}) == ["/steps/0/action"]
    step = {"action": "a@b.co", "tool_kind": "none", "text": "Revisar"}
    assert recipes.screen_recipe(RECIPE | {"steps": [step]}) == ["/steps/0/action"]


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
