"""`--json` on every read command: one JSON document on stdout, nothing else.

Each command runs against populated fixtures (traces, scored runs, a pricing
file, a plugin, an eval suite, a recipe store, a skills hub, a generated agent,
a fleet record, an OAuth store), so the JSON is checked with real rows in it,
not just the empty case. The commands that need a running service (`hermes`
for bots, Postgres for the mcp database commands, a model for evalv2 run) get
a fake at that one seam; everything between it and stdout is the real code.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import nuvel_mcp.cli
import nuvel_mcp.insights
import pytest

from nuvel.bots import cli as bots_cli
from nuvel.bots import fleet as bots_fleet
from nuvel.bots.types import Bot
from nuvel.cli import build_parser, main
from nuvel.eval.schema import ScoredRun
from nuvel.evalv2 import cli as evalv2_cli
from nuvel.evalv2.judges import llm as evalv2_judge

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EVALV2_FIXTURES = Path(__file__).resolve().parent / "test_evalv2" / "fixtures"

SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
SKILL_MD = "---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    traces = tmp_path / "traces"
    shutil.copytree(FIXTURES / "traces_mixed", traces)
    _write_jsonl(traces / "errors.jsonl", [
        {"event": "run_start", "trace_id": "err1", "session_id": "s9",
         "timestamp": "2026-10-21T09:00:00+00:00", "user_input": "boom"},
        {"event": "llm_error", "trace_id": "err1", "model": "m/x", "error_type": "RateLimit",
         "error_message": "slow down", "timestamp": "2026-10-21T09:00:01+00:00"},
        {"event": "tool_end", "trace_id": "err1", "tool": "search", "status": "error",
         "duration_ms": 12, "result": {"error": "no index"},
         "timestamp": "2026-10-21T09:00:02+00:00"},
    ])
    (traces / "scored.jsonl").write_text(
        ScoredRun(
            trace_id="adk1", agent="(local)/meta_agent", scored_at="2026-10-22T00:00:00+00:00",
            scorer_version="1", rubric_version="1", overall=0.4,
            components={"success": 0.5, "quality": 0.3}, flags=["slow"],
        ).to_json_line() + "\n",
        encoding="utf-8",
    )

    (tmp_path / "pricing.json").write_text(json.dumps({
        "_comment": "kept", "m/x": {"input": 1e-6, "output": 2e-6},
    }), encoding="utf-8")

    plugin = tmp_path / "plugins" / "demo"
    (plugin / "skills" / "greeter").mkdir(parents=True)
    (plugin / "plugin.json").write_text(json.dumps(
        {"$schema": SCHEMA_ID, "name": "demo", "version": "0.1.0", "description": "Demo."}
    ), encoding="utf-8")
    (plugin / "skills" / "greeter" / "SKILL.md").write_text(
        SKILL_MD.format(name="greeter", desc="Greets."), encoding="utf-8"
    )
    monkeypatch.setenv("NUVEL_PLUGIN_DIRS", str(tmp_path / "plugins"))

    shutil.copytree(EVALV2_FIXTURES, tmp_path / "eval-skills")

    feedback = tmp_path / "feedback"
    _write_jsonl(feedback / "_recipes" / "signals" / "2026-10-27.jsonl", [{
        "schema": "nuvel.recipe_signal/2", "signal_id": "s1", "recipe_id": "r1",
        "category": "triage", "status": "active", "at": "2026-10-27T10:00:00Z",
        "uses": 3, "successes": 2, "corrections": 0, "days_used": 1, "tenant": "t1",
    }])
    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(feedback))

    hub_skill = tmp_path / "hub" / "ops" / "triage"
    hub_skill.mkdir(parents=True)
    (hub_skill / "SKILL.md").write_text(
        SKILL_MD.format(name="triage", desc="Sorts tickets."), encoding="utf-8"
    )

    agent = tmp_path / "generated-agents" / "demo-agent"
    (agent / "demo_agent").mkdir(parents=True)
    (agent / "demo_agent" / "agent.py").write_text("root_agent = None\n", encoding="utf-8")
    (agent / "requirements.txt").write_text("google-adk\n", encoding="utf-8")
    (tmp_path / "generated-agents" / "notes").mkdir()  # not an agent: skipped
    monkeypatch.delenv("AGENTS_OUTPUT_DIR", raising=False)

    monkeypatch.chdir(tmp_path)
    return tmp_path


# (argv, top-level key that must hold at least one row, exit code)
READ_COMMANDS = [
    (["agent", "list"], "agents", 0),
    (["skills", "list"], "skills", 0),
    (["skills", "search", "memory"], "skills", 0),
    (["plugins", "list"], "plugins", 0),
    (["plugins", "load", "plugins/demo"], "plugins", 0),
    (["plugins", "config"], "plugin_dirs", 0),
    (["traces", "list"], "runs", 0),
    (["traces", "show", "adk1"], "runs", 0),
    (["traces", "stats"], "agents", 0),
    (["traces", "errors"], "by_tool", 0),
    (["traces", "errors", "--recent", "5"], "recent", 0),
    (["traces", "recipes"], "candidates", 0),
    (["pricing", "list", "-t", "pricing.json"], "models", 0),
    (["eval", "report", "-s", "traces"], "agents", 0),
    (["eval", "worst", "-s", "traces"], "runs", 0),
    (["eval", "drift", "-s", "traces"], "agents", 0),
    (["evalv2", "list", "--skills-dir", "eval-skills"], "skills", 0),
    (["doctor"], "checks", 1),  # the workspace is not an agent: doctor fails, JSON still printed
]

# Read commands whose payload is a bare JSON array: (argv, field of the first row, its value)
BARE_READ_COMMANDS = [
    (["bots", "list"], "name", "demo-bot"),
    (["bots", "skills", "list", "--hub", "hub"], "name", "triage"),
    (["bots", "skills", "search", "tickets", "--hub", "hub"], "name", "triage"),
    (["fleet", "list"], None, "acme"),
    (["mcp", "client", "list"], "client_name", "ci-runner"),
]

# The rest: (argv, key that must hold a non-empty value)
OTHER_READ_COMMANDS = [
    (["bots", "info", "demo-bot"], "name"),
    (["bots", "logs", "demo-bot"], "logs"),
    (["fleet", "status", "acme"], "bots"),
    (["mcp", "db", "status"], "applied"),
    (["mcp", "clients", "list"], None),
    (["mcp", "report"], "tools"),
    (["evalv2", "compare", "summarize-skill"], "examples"),
]


class _FakeBotClient:
    """`hermes` stand-in: the bots commands' only external seam."""

    def __init__(self, *args, **kwargs):
        pass

    def list_bots(self):
        return [Bot(name="demo-bot", model="m/x", skills=["ops/triage"])]

    def get_bot_info(self, name):
        return Bot(name=name, description="Answers billing questions")

    def get_bot_logs(self, name, limit=10):
        return ["line one", "line two"]


class _FakeDatabase:
    """Postgres stand-in for the `nuvel mcp db|clients|report` commands."""

    driver_error = RuntimeError

    def migrate(self):
        return []

    def status(self):
        return {"schema": "nuvel_mcp", "applied": [1, 2, 3], "pending": [],
                "rows": {"usage_events": 4}}

    def close(self):
        pass


@pytest.fixture
def services(workspace, monkeypatch):
    """Fakes for hermes, Postgres and the eval model, plus the state they read."""
    monkeypatch.setattr(bots_cli, "BotClient", _FakeBotClient)
    monkeypatch.setattr(bots_fleet, "BotClient", _FakeBotClient)

    hermes_home = workspace / "hermes"
    monkeypatch.setattr(bots_fleet, "DEFAULT_HERMES_HOME", hermes_home)
    now = datetime(2026, 11, 5, tzinfo=timezone.utc).isoformat()
    (hermes_home / "fleets").mkdir(parents=True)
    (hermes_home / "fleets" / "acme.json").write_text(json.dumps({
        "fleet_name": "acme", "company": "Acme", "success": True,
        "bots": [{"name": "triage-bot", "status": "created", "skills_installed": [],
                  "error": None}],
        "started_at": now, "completed_at": now,
    }), encoding="utf-8")

    monkeypatch.delenv("NUVEL_MCP_DATABASE_URL", raising=False)
    monkeypatch.setenv("NUVEL_MCP_OAUTH_STORE", str(workspace / "oauth.json"))
    assert main(["mcp", "client", "create", "--name", "ci-runner"]) == 0

    real_open = nuvel_mcp.cli._open_database
    monkeypatch.setattr(
        nuvel_mcp.cli, "_open_database",
        lambda *, migrate=None: (_FakeDatabase(), None) if migrate is False
        else real_open(migrate=migrate),  # `client list` keeps the real file store
    )
    monkeypatch.setattr(nuvel_mcp.insights, "list_clients", lambda db: [
        {"client_id": "nvcl_1", "name": "ci-runner", "person": "Ana", "company": "Acme",
         "notes": None, "last_seen": None, "events_30d": 0},
    ])
    monkeypatch.setattr(nuvel_mcp.insights, "build_report", lambda db, days, tools: {
        "window": {"days": days}, "totals": {"calls": 1},
        "tools": [{"tool": "get_skill", "calls": 1}],
    })

    monkeypatch.setenv("XDG_DATA_HOME", str(workspace / "xdg"))
    monkeypatch.setattr(evalv2_cli, "LLMExecutor", lambda model: lambda suite, ex: "A summary.")
    monkeypatch.setattr(evalv2_judge, "_default_judge_fn", lambda prompt, model: json.dumps(
        {"accuracy": 0.9, "conciseness": 0.9, "tone": 0.9}
    ))
    monkeypatch.setattr(evalv2_cli, "sync_eval_result", lambda result: False)
    run = ["evalv2", "run", "summarize-skill", "--skills-dir", "eval-skills"]
    assert main([*run, "--save-baseline"]) == 0
    assert main(run) == 0
    return workspace


def _run_json(argv: list[str], capsys: pytest.CaptureFixture):
    capsys.readouterr()  # drop anything a fixture printed
    code = main([*argv, "--json"])
    out = capsys.readouterr().out
    return code, json.loads(out)


@pytest.mark.parametrize(
    "argv,key,expected", READ_COMMANDS, ids=[" ".join(a) for a, _, _ in READ_COMMANDS]
)
def test_read_command_stdout_is_one_json_document(workspace, capsys, argv, key, expected):
    code, payload = _run_json(argv, capsys)
    assert code == expected
    assert isinstance(payload, dict)
    assert payload[key], f"{key} is empty: {payload}"


@pytest.mark.parametrize(
    "argv,field,value", BARE_READ_COMMANDS, ids=[" ".join(a) for a, _, _ in BARE_READ_COMMANDS]
)
def test_bare_array_read_command_stdout_is_json(services, capsys, argv, field, value):
    code, payload = _run_json(argv, capsys)
    assert code == 0
    assert isinstance(payload, list) and payload
    assert (payload[0][field] if field else payload[0]) == value


@pytest.mark.parametrize(
    "argv,key", OTHER_READ_COMMANDS, ids=[" ".join(a) for a, _ in OTHER_READ_COMMANDS]
)
def test_service_read_command_stdout_is_json(services, capsys, argv, key):
    code, payload = _run_json(argv, capsys)
    assert code == 0
    if key is None:
        assert isinstance(payload, list) and payload
    else:
        assert payload[key], f"{key} is empty: {payload}"


def test_evalv2_run_stdout_is_json(services, capsys):
    code, payload = _run_json(
        ["evalv2", "run", "summarize-skill", "--skills-dir", "eval-skills"], capsys
    )
    assert code == 0
    assert payload["suite"] == "summarize-eval" and payload["examples"]


def test_every_read_command_accepts_json():
    """Every command tested here takes `--json` on the real parser."""
    parser = build_parser()
    commands = [argv for argv, *_ in READ_COMMANDS + BARE_READ_COMMANDS + OTHER_READ_COMMANDS]
    for argv in [*commands, ["evalv2", "run", "summarize-skill"]]:
        assert parser.parse_args([*argv, "--json"]).json is True


def test_agent_list_text_and_json_agree(workspace, capsys):
    assert main(["agent", "list"]) == 0
    assert capsys.readouterr().out.split() == ["demo-agent", "adk"]
    _, payload = _run_json(["agent", "list"], capsys)
    assert payload["agents"] == [{
        "name": "demo-agent", "framework": "adk",
        "path": str(Path("generated-agents") / "demo-agent"),
    }]


def test_json_renders_what_the_text_view_computes(workspace, capsys):
    main(["skills", "list"])
    text_slugs = [line.split()[0] for line in capsys.readouterr().out.splitlines()]
    _, payload = _run_json(["skills", "list"], capsys)
    assert [s["slug"] for s in payload["skills"]] == text_slugs

    main(["traces", "list"])
    text = capsys.readouterr().out
    _, payload = _run_json(["traces", "list"], capsys)
    assert f"\n{len(payload['runs'])} run(s)." in text

    main(["pricing", "list", "-t", "pricing.json"])
    text = capsys.readouterr().out
    _, payload = _run_json(["pricing", "list", "-t", "pricing.json"], capsys)
    assert payload["models"] == [
        {"model": "m/x", "input_usd_per_token": 1e-6, "output_usd_per_token": 2e-6}
    ]
    assert "models: 1" in text


def test_traces_stats_json_keys(workspace, capsys):
    _, payload = _run_json(["traces", "stats", "-s", "traces"], capsys)
    assert set(payload) == {"agents", "total", "skill_steps", "missing_cost"}
    assert payload["total"]["runs"] == sum(a["runs"] for a in payload["agents"])
    assert payload["skill_steps"]  # the fixture's skill run, found via --source


def test_traces_show_json_carries_the_timeline(workspace, capsys):
    _, payload = _run_json(["traces", "show", "err1"], capsys)
    (run,) = payload["runs"]
    assert [e.get("event") for e in run["events"]] == ["run_start", "llm_error", "tool_end"]


def test_json_with_nothing_to_report_is_still_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRACE_DIR", raising=False)
    monkeypatch.delenv("AGENTS_OUTPUT_DIR", raising=False)
    for argv in (["traces", "list"], ["traces", "stats"], ["traces", "errors"],
                 ["eval", "report"], ["skills", "search", "zzz-no-match"], ["agent", "list"]):
        _, payload = _run_json(argv, capsys)
        assert isinstance(payload, dict)
