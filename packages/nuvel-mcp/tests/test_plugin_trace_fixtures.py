"""Chief-of-staff plugin traces (one fixture per skill) validate on the server."""

import json
import uuid
from pathlib import Path

import pytest

from nuvel_mcp import recipes, traces
from nuvel_mcp.server import SkillsMCPServer

FIXTURES = Path(__file__).parent / "fixtures" / "chief_of_staff"
RECIPE_FIXTURES = {"recipe_signal", "recipe"}  # not skill runs
TRACE_FIXTURES = sorted(p for p in FIXTURES.glob("*.json") if p.stem not in RECIPE_FIXTURES)


@pytest.fixture(autouse=True)
def _no_relay(monkeypatch):
    monkeypatch.delenv("NUVEL_FEEDBACK_URL", raising=False)


@pytest.fixture
def server(tmp_path):
    return SkillsMCPServer(None, feedback_dir=tmp_path)


def load(skill):
    return json.loads((FIXTURES / f"{skill}.json").read_text())


def call(s, name, args):
    res = s.handle_tools_call({"name": name, "arguments": args})
    return json.loads(res["content"][0]["text"])


def test_one_fixture_per_skill():
    assert {p.stem for p in TRACE_FIXTURES} == {
        "setup", "radar", "brief", "prep", "weekly-review", "recall", "learn"}
    assert {p.stem for p in FIXTURES.glob("*.json")} - {p.stem for p in TRACE_FIXTURES} == RECIPE_FIXTURES


@pytest.mark.parametrize("path", TRACE_FIXTURES, ids=lambda p: p.stem)
def test_chief_of_staff_fixture_validates(path):
    trace = json.loads(path.read_text())
    assert traces.validate_skill_run(trace) == []
    assert trace["plugin"] == {"name": "chief-of-staff", "version": "0.3.0"}
    assert "tenant" not in trace
    assert all(s["id"].startswith(trace["skill"]["name"] + ".") for s in trace.get("steps", []))


def test_unknown_step_names_still_valid_identifiers(server):
    # a step id from a newer catalog is accepted
    trace = load("radar") | {"run_id": str(uuid.uuid4())}
    trace["steps"] = trace["steps"] + [{"id": "radar.future-step", "status": "ok"}]
    assert call(server, "record_run_trace", {"trace": trace})["status"] == "recorded"


def test_learn_signal_and_recipe_fixtures(server):
    signal = json.loads((FIXTURES / "recipe_signal.json").read_text())
    recipe = json.loads((FIXTURES / "recipe.json").read_text())
    assert recipes.validate_signal(signal) == [] and recipes.screen_signal(signal) == []
    assert recipes.validate_recipe(recipe) == [] and recipes.screen_recipe(recipe) == []
    assert "tenant" not in signal and "tenant" not in recipe
    assert signal["recipe_id"] == recipe["recipe_id"]
    assert call(server, "record_recipe_signal", {"signal": signal})["status"] == "recorded"
    assert call(server, "record_recipe_signal", {"signal": signal})["status"] == "duplicate"
    assert call(server, "share_recipe", {"recipe": recipe})["status"] == "recorded"
