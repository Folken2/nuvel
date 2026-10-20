"""Tests for the Skills MCP hub tools: search, browse, install and feedback."""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path

import pytest

from nuvel_mcp import server as server_mod
from nuvel_mcp.server import SkillsMCPServer
from nuvel_mcp.skills_loader import SkillsLoader

# theme -> [(name, description, frontmatter version, body)]; a body of None
# means the SKILL.md is not vendored (a dependency entry).
HUB = {
    "software-development": [
        ("installer-transaction-design",
         "Plan/apply/doctor contract for installers that write agent config",
         '"2.1.0"', "Stage, verify, then swap. Never write in place."),
        ("rollback", "Undo a bad deploy", "1.0.0", "Revert the release."),
        ("release-notes", "Write user-facing release notes", "1.2.0",
         "Summarize changes. If a deploy fails, link the rollback runbook."),
    ],
    "hr": [
        ("onboarding", "Employee onboarding checklist", "1.0.0",
         "Order the zebrafish mug for the new hire."),
        ("offboarding", "Employee offboarding", "1.0.0", None),
    ],
    "sales": [
        ("prospecting", "Find new leads", "1.0.0", "Research accounts."),
        ("demo", "Run a product demo", "1.0.0", "Show the product."),
        ("closing", "Close the deal", "1.0.0", "Ask for the signature."),
    ],
}
ALL_NAMES = sorted(name for skills in HUB.values() for name, *_ in skills)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Keep every test away from real tokens, relays and the home directory."""
    for var in ("GITHUB_TOKEN", "NUVEL_FEEDBACK_URL", "NUVEL_SKILLS_INSTALL_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("NUVEL_FEEDBACK_QUEUE", raising=False)
    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(tmp_path / "feedback"))


@pytest.fixture
def hub(tmp_path) -> Path:
    skills = tmp_path / "hub" / "skills"
    index: dict[str, list[dict]] = {}
    for theme, entries in HUB.items():
        index[theme] = []
        for name, description, version, body in entries:
            index[theme].append({
                "name": name,
                "description": description,
                "author": "Nuvel Skills",
                "path": f"skills/{theme}/{name}/SKILL.md",
            })
            if body is None:
                continue
            skill_dir = skills / theme / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {name}\nversion: {version}\n---\n\n# {name}\n\n{body}\n",
                encoding="utf-8",
            )
    # Supporting files that install_skill must carry along.
    itd = skills / "software-development" / "installer-transaction-design"
    (itd / "scripts").mkdir()
    (itd / "scripts" / "plan.sh").write_text("#!/bin/sh\necho plan\n")
    (itd / "scripts" / "plan.sh").chmod(0o755)
    (itd / "references").mkdir()
    (itd / "references" / "contract.md").write_text("# Contract\n")
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


def call_error(server, tool, **arguments):
    resp = _rpc(server, tool, arguments)
    assert "error" in resp, resp
    return resp["error"]


def names(results):
    return [r["name"] for r in results]


# --- tools/list ---------------------------------------------------------------


def test_tools_list_has_full_tool_set(server):
    resp = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {t["name"]: t for t in resp["result"]["tools"]}
    assert set(tools) == {
        "search_skills", "list_skills", "list_themes",
        "get_skill", "install_skill", "propose_improvement",
        "fence_value", "record_feedback", "check_skill_health",
    }
    assert "confirm=true" in tools["install_skill"]["description"]
    assert tools["propose_improvement"]["inputSchema"]["required"] == [
        "skill_name", "issue", "suggested_fix",
    ]


# --- search_skills ------------------------------------------------------------


def test_search_multiword_matches_hyphenated_name(server):
    result = call(server, "search_skills", query="installer transaction")
    assert names(result["results"]) == ["installer-transaction-design"]
    assert result["count"] == 1
    assert result["query"] == "installer transaction"


@pytest.mark.parametrize("query", ["Installer   TRANSACTION", "installer_transaction",
                                   "installer/transaction", "installer.transaction"])
def test_search_normalizes_separators_and_case(server, query):
    result = call(server, "search_skills", query=query)
    assert names(result["results"]) == ["installer-transaction-design"]


def test_search_requires_all_tokens(server):
    result = call(server, "search_skills", query="installer onboarding")
    assert result["count"] == 0
    assert result["results"] == []


def test_search_result_shape(server):
    item = call(server, "search_skills", query="installer transaction")["results"][0]
    assert item == {
        "uri": "skill://software-development/installer-transaction-design",
        "theme": "software-development",
        "name": "installer-transaction-design",
        "description": "Plan/apply/doctor contract for installers that write agent config",
        "author": "Nuvel Skills",
        "version": "2.1.0",
        "score": item["score"],
        "matched_in": ["name", "description"],
        "snippet": "",
    }


def test_search_exact_name_outranks_body_only_match(server):
    result = call(server, "search_skills", query="rollback", include_body=True)
    assert names(result["results"]) == ["rollback", "release-notes"]
    exact, body_only = result["results"]
    assert exact["score"] > body_only["score"]
    assert body_only["matched_in"] == ["body"]
    assert "rollback" in body_only["snippet"]


def test_search_ranking_tiers(server):
    # name prefix > token-in-name > description-only, ties broken by name.
    result = call(server, "search_skills", query="release")
    assert names(result["results"]) == ["release-notes"]
    result = call(server, "search_skills", query="deploy")
    assert names(result["results"]) == ["rollback"]
    assert result["results"][0]["matched_in"] == ["description"]
    by_theme = call(server, "search_skills", query="sales")["results"]
    assert names(by_theme) == ["closing", "demo", "prospecting"]
    assert len({r["score"] for r in by_theme}) == 1


def test_search_body_only_term(server):
    assert call(server, "search_skills", query="zebrafish")["count"] == 0
    result = call(server, "search_skills", query="zebrafish", include_body=True)
    assert names(result["results"]) == ["onboarding"]
    hit = result["results"][0]
    assert hit["matched_in"] == ["body"]
    assert "zebrafish" in hit["snippet"]
    assert hit["version"] == "1.0.0"


def test_search_body_does_not_search_frontmatter(server):
    result = call(server, "search_skills", query="version", include_body=True)
    assert result["count"] == 0


def test_search_body_missing_skill_md_does_not_crash(server):
    result = call(server, "search_skills", query="employee", include_body=True)
    assert names(result["results"]) == ["offboarding", "onboarding"]
    offboarding = result["results"][0]
    assert offboarding["version"] == ""
    assert offboarding["snippet"] == ""


def test_search_body_unreadable_skill_md_does_not_crash(server, hub):
    (hub / "sales" / "demo" / "SKILL.md").write_bytes(b"\xff\xfe\x00 not utf-8")
    result = call(server, "search_skills", query="demo", include_body=True)
    assert names(result["results"]) == ["demo"]


def test_search_theme_filter(server):
    assert names(call(server, "search_skills", query="closing", theme="sales")["results"]) == ["closing"]
    assert call(server, "search_skills", query="closing", theme="hr")["count"] == 0


def test_search_respects_server_theme_scope(hub):
    scoped = SkillsMCPServer(SkillsLoader(hub), theme="hr")
    assert call(scoped, "search_skills", query="closing")["count"] == 0
    assert call(scoped, "search_skills", query="closing", theme="sales")["count"] == 0
    assert names(call(scoped, "search_skills", query="employee")["results"]) == [
        "offboarding", "onboarding",
    ]


def test_search_limit(server):
    result = call(server, "search_skills", query="e", limit=2)
    assert result["count"] == 2
    assert result["total"] > 2
    assert call(server, "search_skills", query="e", limit=999)["count"] == len(ALL_NAMES)


@pytest.mark.parametrize("arguments", [
    {},
    {"query": "   "},
    {"query": "---"},
    {"query": 42},
    {"query": "x", "limit": 0},
    {"query": "x", "limit": "ten"},
    {"query": "x", "include_body": "yes"},
    {"query": "x", "theme": 3},
])
def test_search_invalid_arguments(server, arguments):
    assert call_error(server, "search_skills", **arguments)["code"] == server_mod.INVALID_PARAMS


# --- list_skills / list_themes --------------------------------------------------


def test_list_skills_all(server):
    result = call(server, "list_skills")
    assert result["total"] == result["count"] == len(ALL_NAMES)
    assert result["offset"] == 0
    item = result["items"][0]
    assert set(item) == {"uri", "theme", "name", "description", "author", "version"}


def test_list_skills_pagination(server):
    pages = [call(server, "list_skills", limit=3, offset=o) for o in (0, 3, 6)]
    assert [p["count"] for p in pages] == [3, 3, 2]
    assert all(p["total"] == len(ALL_NAMES) for p in pages)
    assert [p["offset"] for p in pages] == [0, 3, 6]
    seen = [i["uri"] for p in pages for i in p["items"]]
    assert len(seen) == len(set(seen)) == len(ALL_NAMES)
    past_end = call(server, "list_skills", limit=3, offset=99)
    assert past_end["items"] == [] and past_end["total"] == len(ALL_NAMES)


def test_list_skills_theme_filter(server):
    result = call(server, "list_skills", theme="sales")
    assert result["total"] == 3
    assert names(result["items"]) == ["closing", "demo", "prospecting"]
    assert {i["theme"] for i in result["items"]} == {"sales"}


def test_list_skills_unknown_theme_is_empty(server):
    assert call(server, "list_skills", theme="nope") == {
        "total": 0, "count": 0, "offset": 0, "items": [],
    }


def test_list_skills_invalid_offset(server):
    assert call_error(server, "list_skills", offset=-1)["code"] == server_mod.INVALID_PARAMS


def test_list_themes(server):
    result = call(server, "list_themes")
    assert result["count"] == 3
    assert [t["theme"] for t in result["themes"]] == ["hr", "sales", "software-development"]
    sales = result["themes"][1]
    assert sales == {"theme": "sales", "count": 3, "skills": ["closing", "demo", "prospecting"]}


def test_list_tools_respect_server_theme_scope(hub):
    scoped = SkillsMCPServer(SkillsLoader(hub), theme="sales")
    assert [t["theme"] for t in call(scoped, "list_themes")["themes"]] == ["sales"]
    assert call(scoped, "list_skills")["total"] == 3
    assert call(scoped, "list_skills", theme="hr")["total"] == 0


# --- get_skill -------------------------------------------------------------------


def test_get_skill_includes_version(server):
    result = call(server, "get_skill", name="installer-transaction-design")
    assert result["version"] == "2.1.0"
    assert result["metadata"]["name"] == "installer-transaction-design"
    assert "Stage, verify, then swap." in result["content"]


# --- install_skill ---------------------------------------------------------------

SKILL = "installer-transaction-design"
SKILL_FILES = ["SKILL.md", "references/contract.md", "scripts/plan.sh"]


def _install(server, target, **kwargs):
    return call(server, "install_skill", name=SKILL, target_dir=str(target), **kwargs)


def test_install_plan_writes_nothing(server, tmp_path):
    target = tmp_path / "client-skills"
    result = _install(server, target)
    assert result["status"] == "planned"
    assert result["action"] == "install"
    assert result["files"] == 3
    assert result["paths"] == SKILL_FILES
    assert result["target"] == str(target / SKILL)
    assert result["backup"] is None
    assert result["error"] is None
    assert not target.exists()


def test_install_confirm_installs(server, hub, tmp_path):
    target = tmp_path / "client-skills"
    result = _install(server, target, confirm=True)
    assert result["status"] == "installed"
    assert result["action"] == "install"
    assert result["error"] is None
    assert result["version"] == "2.1.0"
    dest = target / SKILL
    source = hub / "software-development" / SKILL
    for rel in SKILL_FILES:
        assert (dest / rel).read_bytes() == (source / rel).read_bytes()
    assert os.access(dest / "scripts" / "plan.sh", os.X_OK)
    assert sorted(p.name for p in target.iterdir()) == [SKILL]  # no staging left


def test_install_theme_qualified_name(server, tmp_path):
    result = call(server, "install_skill", name=f"software-development/{SKILL}",
                  target_dir=str(tmp_path), confirm=True)
    assert result["status"] == "installed"


def test_install_unchanged_reinstall_is_noop(server, tmp_path):
    _install(server, tmp_path, confirm=True)
    assert _install(server, tmp_path)["action"] == "noop"
    result = _install(server, tmp_path, confirm=True)
    assert result["status"] == "noop"
    assert result["backup"] is None
    assert not (tmp_path / f"{SKILL}.backup").exists()


def test_install_drifted_destination_is_backed_up(server, tmp_path):
    _install(server, tmp_path, confirm=True)
    dest = tmp_path / SKILL
    (dest / "SKILL.md").write_text("local edits\n")
    (dest / "notes.txt").write_text("mine\n")

    plan = _install(server, tmp_path)
    assert plan["status"] == "planned" and plan["action"] == "update"
    assert plan["backup"] == str(tmp_path / f"{SKILL}.backup")
    assert (dest / "SKILL.md").read_text() == "local edits\n"

    result = _install(server, tmp_path, confirm=True)
    assert result["status"] == "installed" and result["action"] == "update"
    backup = tmp_path / f"{SKILL}.backup"
    assert result["backup"] == str(backup)
    assert (backup / "SKILL.md").read_text() == "local edits\n"
    assert (backup / "notes.txt").read_text() == "mine\n"
    assert "Stage, verify" in (dest / "SKILL.md").read_text()
    assert not (dest / "notes.txt").exists()


def test_install_never_overwrites_existing_backup(server, tmp_path):
    _install(server, tmp_path, confirm=True)
    dest = tmp_path / SKILL
    backup = tmp_path / f"{SKILL}.backup"
    backup.mkdir()
    (backup / "keep.txt").write_text("precious\n")
    (dest / "SKILL.md").write_text("drifted\n")

    result = _install(server, tmp_path, confirm=True)
    assert result["status"] == "blocked"
    assert "backup" in result["error"].lower()
    assert (backup / "keep.txt").read_text() == "precious\n"
    assert (dest / "SKILL.md").read_text() == "drifted\n"


def test_install_symlinked_destination_is_blocked(server, tmp_path):
    target = tmp_path / "client-skills"
    target.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (target / SKILL).symlink_to(elsewhere, target_is_directory=True)
    for confirm in (False, True):
        result = _install(server, target, confirm=confirm)
        assert result["status"] == "blocked"
        assert "symlink" in result["error"]
    assert list(elsewhere.iterdir()) == []


def test_install_symlinked_target_dir_is_blocked(server, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    result = _install(server, link, confirm=True)
    assert result["status"] == "blocked"
    assert "symlink" in result["error"]
    assert list(real.iterdir()) == []


def test_install_non_directory_target_is_blocked(server, tmp_path):
    target = tmp_path / "a-file"
    target.write_text("x")
    result = _install(server, target, confirm=True)
    assert result["status"] == "blocked"
    assert "not a directory" in result["error"]


def _failing_write_bytes(monkeypatch, fail_on_call):
    """Make the Nth Path.write_bytes call write half its data then raise."""
    real = Path.write_bytes
    calls = {"n": 0}

    def fake(self, data):
        calls["n"] += 1
        if calls["n"] == fail_on_call:
            real(self, data[: len(data) // 2])
            raise OSError("disk full")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", fake)


def test_install_partial_write_never_reports_success(server, tmp_path, monkeypatch):
    target = tmp_path / "client-skills"
    _failing_write_bytes(monkeypatch, fail_on_call=2)
    result = _install(server, target, confirm=True)
    assert result["status"] == "failed"
    assert "disk full" in result["error"]
    assert not (target / SKILL).exists()
    assert list(target.iterdir()) == []  # staging cleaned up


def test_install_partial_update_restores_original(server, tmp_path, monkeypatch):
    _install(server, tmp_path, confirm=True)
    dest = tmp_path / SKILL
    (dest / "SKILL.md").write_text("drifted\n")
    _failing_write_bytes(monkeypatch, fail_on_call=1)
    result = _install(server, tmp_path, confirm=True)
    assert result["status"] == "failed"
    assert result["backup"] is None
    assert (dest / "SKILL.md").read_text() == "drifted\n"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(SKILL)) == [SKILL]


def test_install_silent_truncation_is_detected(server, tmp_path, monkeypatch):
    real = Path.write_bytes
    monkeypatch.setattr(Path, "write_bytes", lambda self, data: real(self, b""))
    result = _install(server, tmp_path, confirm=True)
    assert result["status"] == "failed"
    assert not (tmp_path / SKILL).exists()


def test_install_defaults_to_env_dir(server, tmp_path, monkeypatch):
    monkeypatch.setenv("NUVEL_SKILLS_INSTALL_DIR", str(tmp_path / "from-env"))
    result = call(server, "install_skill", name=SKILL)
    assert result["status"] == "planned"
    assert result["target"] == str(tmp_path / "from-env" / SKILL)


def test_install_falls_back_to_claude_skills(server, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    result = call(server, "install_skill", name=SKILL)
    assert result["target"] == str(tmp_path / "home" / ".claude" / "skills" / SKILL)
    assert not (tmp_path / "home").exists()


def test_install_unvendored_skill_is_blocked(server, tmp_path):
    target = tmp_path / "client-skills"
    result = call(server, "install_skill", name="offboarding",
                  target_dir=str(target), confirm=True)
    assert result["status"] == "blocked"
    assert result["error"]
    assert not target.exists()


def test_install_requires_boolean_confirm(server, tmp_path):
    err = call_error(server, "install_skill", name=SKILL,
                     target_dir=str(tmp_path / "t"), confirm="true")
    assert err["code"] == server_mod.INVALID_PARAMS
    assert not (tmp_path / "t").exists()


def test_install_unknown_skill_and_scope(hub, server, tmp_path):
    assert call_error(server, "install_skill", name="nope")["code"] == server_mod.INVALID_PARAMS
    scoped = SkillsMCPServer(SkillsLoader(hub), theme="hr")
    assert call_error(scoped, "install_skill", name=SKILL)["code"] == server_mod.INVALID_PARAMS


# --- propose_improvement ---------------------------------------------------------

PROPOSAL = {"issue": "step 2 is stale", "suggested_fix": "update the link", "harness": "pytest"}


def test_propose_queues_without_token_or_relay(server, tmp_path):
    result = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert result["status"] == "queued"
    assert result["delivery"] == "queue"
    assert re.fullmatch(r"[0-9a-f]{16}", result["feedback_id"])
    queue = tmp_path / "feedback" / "feedback-queue.jsonl"
    assert result["queue_path"] == str(queue)
    lines = queue.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["feedback_id"] == result["feedback_id"]
    assert record["skill_name"] == SKILL
    assert record["issue"] == "step 2 is stale"
    assert record["queued_at"]


def test_propose_queue_creates_parent_dirs(server, tmp_path, monkeypatch):
    queue = tmp_path / "deep" / "er" / "feedback-queue.jsonl"
    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(queue.parent))
    result = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert result["queue_path"] == str(queue)
    assert queue.is_file()


def test_propose_feedback_id_is_stable(server, tmp_path):
    first = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    second = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    other = call(server, "propose_improvement", skill_name=SKILL,
                 **dict(PROPOSAL, issue="something else"))
    assert first["feedback_id"] == second["feedback_id"] != other["feedback_id"]
    assert len((tmp_path / "feedback" / "feedback-queue.jsonl").read_text().splitlines()) == 3


def test_propose_current_version_from_frontmatter(server):
    result = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert result["current_version"] == "2.1.0"
    assert "v2.1.0" in result["title"]
    themed = call(server, "propose_improvement",
                  skill_name=f"software-development/{SKILL}", **PROPOSAL)
    assert themed["current_version"] == "2.1.0"
    explicit = call(server, "propose_improvement", skill_name=SKILL,
                    current_version="0.9.0", **PROPOSAL)
    assert explicit["current_version"] == "0.9.0"
    unknown = call(server, "propose_improvement", skill_name="nope", **PROPOSAL)
    assert unknown["current_version"] == "unknown"
    assert unknown["status"] == "queued"


class _FakeUrlopen:
    def __init__(self, reply: bytes = b"", exc: Exception | None = None):
        self.reply, self.exc, self.requests = reply, exc, []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return io.BytesIO(self.reply)


def test_propose_relays_when_url_set(server, tmp_path, monkeypatch):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/feedback")
    fake = _FakeUrlopen(json.dumps({"issue_url": "https://gh/i/7", "id": "fb-7"}).encode())
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", fake)
    result = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert result["status"] == "relayed"
    assert result["delivery"] == "relay"
    assert result["issue_url"] == "https://gh/i/7"
    assert result["id"] == "fb-7"
    (request,) = fake.requests
    assert request.full_url == "https://relay.example/feedback"
    assert request.get_method() == "POST"
    sent = json.loads(request.data)
    assert sent["skill_name"] == SKILL
    assert sent["current_version"] == "2.1.0"
    assert sent["feedback_id"] == result["feedback_id"]
    assert not (tmp_path / "feedback" / "feedback-queue.jsonl").exists()


def test_propose_relay_failure_falls_back_to_queue(server, tmp_path, monkeypatch):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/feedback")
    fake = _FakeUrlopen(exc=server_mod.urllib.error.URLError("unreachable"))
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", fake)
    result = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert result["status"] == "queued"
    assert "unreachable" in result["relay_error"]
    assert (tmp_path / "feedback" / "feedback-queue.jsonl").is_file()


def test_propose_relay_rejects_non_http_url(server, tmp_path, monkeypatch):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "file:///etc/passwd")
    fake = _FakeUrlopen()
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", fake)
    result = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert result["status"] == "queued"
    assert fake.requests == []


def test_propose_github_path_reports_delivery(server, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    fake = _FakeUrlopen(json.dumps({"html_url": "https://github.com/x/1", "number": 1}).encode())
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", fake)
    result = call(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert result["status"] == "filed"
    assert result["delivery"] == "github"
    assert result["issue_url"] == "https://github.com/x/1"
    assert fake.requests[0].full_url == server_mod.GITHUB_ISSUES_URL
    assert not (tmp_path / "feedback" / "feedback-queue.jsonl").exists()


def test_propose_unqueueable_is_an_error_not_success(server, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(blocker / "store"))
    err = call_error(server, "propose_improvement", skill_name=SKILL, **PROPOSAL)
    assert err["code"] == server_mod.INTERNAL_ERROR


def test_propose_missing_required_args(server):
    err = call_error(server, "propose_improvement", skill_name=SKILL)
    assert err["code"] == server_mod.INVALID_PARAMS
    assert "issue" in err["message"] and "suggested_fix" in err["message"]
