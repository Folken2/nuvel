"""The single skill feedback store shared by propose_improvement,
record_feedback, check_skill_health and get_skill's embedded health."""

from __future__ import annotations

import argparse
import io
import json
import re
from pathlib import Path

import pytest

from nuvel.commands import mcp_serve
from nuvel.mcp import feedback
from nuvel.mcp import server as server_mod
from nuvel.mcp.server import SkillsMCPServer
from nuvel.mcp.skills_loader import SkillsLoader

SKILL = "deploy-checklist"
PROPOSAL = {"issue": "step 2 is stale", "suggested_fix": "update the link", "harness": "pytest"}
ALL_TOOLS = {
    "search_skills", "list_skills", "list_themes", "get_skill", "install_skill",
    "fence_value", "propose_improvement", "record_feedback", "check_skill_health",
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for var in ("GITHUB_TOKEN", "NUVEL_FEEDBACK_URL", "NUVEL_FEEDBACK_QUEUE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(tmp_path / "store"))


@pytest.fixture
def hub(tmp_path) -> Path:
    skills = tmp_path / "hub" / "skills"
    skill_dir = skills / "devops" / SKILL
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {SKILL}\nversion: 1.4.0\n---\n\n# {SKILL}\n\n"
        "## Rollout\n\nShip it.\n",
        encoding="utf-8",
    )
    index = {"devops": [{
        "name": SKILL,
        "description": "Pre-deploy checklist",
        "author": "Nuvel Skills",
        "path": f"skills/devops/{SKILL}/SKILL.md",
    }]}
    (skills / "index.json").write_text(
        json.dumps({"version": 1, "themes": index}), encoding="utf-8"
    )
    return skills


@pytest.fixture
def server(hub) -> SkillsMCPServer:
    return SkillsMCPServer(SkillsLoader(hub))


def _rpc(server, tool, arguments):
    return server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    })


def call(server, tool, **arguments):
    resp = _rpc(server, tool, arguments)
    assert "error" not in resp, resp
    return json.loads(resp["result"]["content"][0]["text"])


def outcome(**overrides):
    base = {
        "skill_name": SKILL,
        "skill_version": "1.4.0",
        "outcome": "failure",
        "severity": "blocking",
        "section": "Rollout",
        "what_didnt": "the rollout command was renamed",
    }
    base.update(overrides)
    return base


# --- one store ----------------------------------------------------------------


def test_queued_proposal_is_visible_to_check_skill_health(server, tmp_path):
    before = call(server, "check_skill_health", skill_name=SKILL)
    assert before == {"total_feedback": 0, "queued_proposals": 0, "recommendation": "ok"}

    proposed = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert proposed["status"] == "queued"
    assert proposed["delivery"] == "queue"
    assert re.fullmatch(r"[0-9a-f]{16}", proposed["feedback_id"])
    store = tmp_path / "store"
    assert proposed["queue_path"] == str(store / "feedback-queue.jsonl")
    assert proposed["feedback_dir"] == str(store)
    assert Path(proposed["queue_path"]).parent == server.feedback_dir

    health = call(server, "check_skill_health", skill_name=SKILL)
    assert health["queued_proposals"] == 1
    (listed,) = health["proposals"]
    assert listed["feedback_id"] == proposed["feedback_id"]
    assert listed["current_version"] == "1.4.0"
    assert listed["issue"] == "step 2 is stale"
    assert listed["suggested_fix"] == "update the link"
    assert listed["queued_at"]


def test_queued_proposal_shows_in_get_skill_health(server):
    proposed = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    skill = call(server, "get_skill", name=SKILL)
    assert skill["health"]["queued_proposals"] == 1
    assert skill["health"]["proposals"][0]["feedback_id"] == proposed["feedback_id"]


def test_themed_and_bare_names_share_one_store_key(server):
    call(server, "propose_improvement", skill_name=f"devops/{SKILL}", **PROPOSAL)
    call(server, "record_feedback", **outcome(skill_name=f"devops/{SKILL}"))
    for ref in (SKILL, f"devops/{SKILL}"):
        health = call(server, "check_skill_health", skill_name=ref)
        assert health["skill_name"] == SKILL
        assert health["queued_proposals"] == 1
        assert health["total_feedback"] == 1


def test_proposals_dedup_by_feedback_id(server):
    first = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    again = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert first["feedback_id"] == again["feedback_id"]
    assert call(server, "check_skill_health", skill_name=SKILL)["queued_proposals"] == 1

    call(server, "propose_improvement", skill_name=SKILL, **dict(PROPOSAL, issue="other"))
    assert call(server, "check_skill_health", skill_name=SKILL)["queued_proposals"] == 2


def test_proposals_are_scoped_to_their_skill(server):
    call(server, "propose_improvement", skill_name="some-other-skill", **PROPOSAL)
    assert call(server, "check_skill_health", skill_name=SKILL)["queued_proposals"] == 0


def test_delivered_proposals_do_not_land_in_the_store(server, monkeypatch):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/feedback")
    monkeypatch.setattr(
        server_mod.urllib.request, "urlopen",
        lambda request, timeout=None: io.BytesIO(b'{"id": "fb-1"}'),
    )
    relayed = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert relayed["delivery"] == "relay"
    assert call(server, "check_skill_health", skill_name=SKILL)["queued_proposals"] == 0


def test_relay_failure_queues_into_the_store(server, monkeypatch):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/feedback")

    def down(request, timeout=None):
        raise server_mod.urllib.error.URLError("unreachable")

    monkeypatch.setattr(server_mod.urllib.request, "urlopen", down)
    queued = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert queued["delivery"] == "queue"
    assert call(server, "check_skill_health", skill_name=SKILL)["queued_proposals"] == 1


# --- record_feedback changes the report -----------------------------------------


def test_record_feedback_changes_health_report(server, tmp_path):
    assert call(server, "check_skill_health", skill_name=SKILL)["recommendation"] == "ok"

    recorded = call(server, "record_feedback", **outcome())
    assert recorded["status"] == "recorded"
    assert (tmp_path / "store" / SKILL / f"{recorded['feedback_id']}.json").is_file()
    health = call(server, "check_skill_health", skill_name=SKILL)
    assert health["total_feedback"] == 1
    assert health["recent_30d"]["failure"] == 1
    assert health["recommendation"] == "ok"  # one report doesn't flag a section

    call(server, "record_feedback", **outcome())
    health = call(server, "check_skill_health", skill_name=SKILL)
    assert health["total_feedback"] == 2
    assert health["flagged_sections"][0]["section"] == "Rollout"
    assert health["flagged_sections"][0]["count"] == 2
    assert health["recommendation"] == "use_cautiously"


def test_outcomes_and_proposals_appear_in_one_report(server):
    call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    call(server, "record_feedback", **outcome(outcome="success", severity="minor"))
    health = call(server, "check_skill_health", skill_name=SKILL)
    assert health["total_feedback"] == 1
    assert health["recent_30d"]["success"] == 1
    assert health["queued_proposals"] == 1


# --- dedup keys -----------------------------------------------------------------


def _dedup_part(feedback_id: str) -> str:
    return feedback_id.split("-")[3]


def test_dedup_key_stable_across_case_whitespace_and_theme(server):
    a = call(server, "record_feedback", **outcome())
    b = call(server, "record_feedback", **outcome(
        skill_name=f"devops/{SKILL}",
        what_didnt="  The   rollout command was RENAMED ",
    ))
    c = call(server, "record_feedback", **outcome(what_didnt="a different problem"))
    d = call(server, "record_feedback", **outcome(section="Prerequisites"))
    assert a["feedback_id"] != b["feedback_id"]  # distinct records ...
    assert _dedup_part(a["feedback_id"]) == _dedup_part(b["feedback_id"])  # ... same key
    assert _dedup_part(a["feedback_id"]) != _dedup_part(c["feedback_id"])
    assert _dedup_part(a["feedback_id"]) != _dedup_part(d["feedback_id"])
    assert re.fullmatch(r"[0-9a-f]{16}", _dedup_part(a["feedback_id"]))


# --- store location and safety --------------------------------------------------


def test_explicit_feedback_dir_beats_env(hub, tmp_path):
    explicit = tmp_path / "explicit"
    server = SkillsMCPServer(SkillsLoader(hub), feedback_dir=str(explicit))
    proposed = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert proposed["queue_path"] == str(explicit / "feedback-queue.jsonl")
    call(server, "record_feedback", **outcome())
    assert (explicit / SKILL).is_dir()
    assert not (tmp_path / "store").exists()
    assert call(server, "check_skill_health", skill_name=SKILL)["queued_proposals"] == 1


def test_default_store_is_under_home(monkeypatch, tmp_path):
    monkeypatch.delenv("NUVEL_FEEDBACK_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert feedback.resolve_feedback_dir() == tmp_path / "home" / ".nuvel" / "feedback"


def test_store_is_not_created_until_a_write(hub, tmp_path):
    server = SkillsMCPServer(SkillsLoader(hub))
    call(server, "get_skill", name=SKILL)
    call(server, "check_skill_health", skill_name=SKILL)
    assert not (tmp_path / "store").exists()


def test_legacy_queue_env_is_ignored(server, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.jsonl"
    monkeypatch.setenv("NUVEL_FEEDBACK_QUEUE", str(legacy))
    proposed = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert proposed["queue_path"] == str(tmp_path / "store" / "feedback-queue.jsonl")
    assert not legacy.exists()


@pytest.mark.parametrize("bad", ["../escape", "..", "a/..", "/abs", "x y", ""])
def test_record_feedback_rejects_unsafe_skill_names(server, tmp_path, bad):
    result = call(server, "record_feedback", **outcome(skill_name=bad))
    assert result["status"] == "error"
    assert not (tmp_path / "escape").exists()


def test_check_skill_health_rejects_unsafe_skill_name(server):
    resp = _rpc(server, "check_skill_health", {"skill_name": "../../etc"})
    assert resp["error"]["code"] == server_mod.INVALID_PARAMS


# --- surface ----------------------------------------------------------------------


def test_stdio_lists_all_tools(server):
    stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
    stdout = io.StringIO()
    server.serve(stdin=stdin, stdout=stdout)
    tools = json.loads(stdout.getvalue())["result"]["tools"]
    assert {t["name"] for t in tools} == ALL_TOOLS


def test_cli_feedback_dir_defaults_to_env_resolution():
    parser = argparse.ArgumentParser()
    mcp_serve.register(parser.add_subparsers())
    assert parser.parse_args(["mcp", "serve"]).feedback_dir is None
    args = parser.parse_args(["mcp", "serve", "--feedback-dir", "/tmp/fb"])
    assert args.feedback_dir == "/tmp/fb"
