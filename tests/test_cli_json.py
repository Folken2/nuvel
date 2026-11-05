"""`--json` on every read command: one JSON document on stdout, nothing else.

Each command runs against populated fixtures (traces, scored runs, a pricing
file, a plugin, an eval suite, a recipe store, a skills hub), so the JSON is
checked with real rows in it, not just the empty case.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from nuvel.cli import build_parser, main
from nuvel.eval.schema import ScoredRun

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

    monkeypatch.chdir(tmp_path)
    return tmp_path


# (argv, top-level key that must hold at least one row)
READ_COMMANDS = [
    (["skills", "list"], "skills"),
    (["skills", "search", "memory"], "skills"),
    (["plugins", "list"], "plugins"),
    (["plugins", "load", "plugins/demo"], "plugins"),
    (["plugins", "config"], "plugin_dirs"),
    (["traces", "list"], "runs"),
    (["traces", "show", "adk1"], "runs"),
    (["traces", "stats"], "agents"),
    (["traces", "errors"], "by_tool"),
    (["traces", "errors", "--recent", "5"], "recent"),
    (["traces", "recipes"], "candidates"),
    (["pricing", "list", "-t", "pricing.json"], "models"),
    (["eval", "report", "-s", "traces"], "agents"),
    (["eval", "worst", "-s", "traces"], "runs"),
    (["eval", "drift", "-s", "traces"], "agents"),
    (["evalv2", "list", "--skills-dir", "eval-skills"], "skills"),
    (["doctor"], "checks"),
]

# Read commands whose payload is a bare JSON value (pre-existing shape).
BARE_READ_COMMANDS = [
    ["bots", "skills", "list", "--hub", "hub"],
    ["bots", "skills", "search", "tickets", "--hub", "hub"],
]


def _run_json(argv: list[str], capsys: pytest.CaptureFixture):
    pytest.importorskip("yaml")
    code = main([*argv, "--json"])
    out = capsys.readouterr().out
    return code, json.loads(out)


@pytest.mark.parametrize("argv,key", READ_COMMANDS, ids=[" ".join(a) for a, _ in READ_COMMANDS])
def test_read_command_stdout_is_one_json_document(workspace, capsys, argv, key):
    code, payload = _run_json(argv, capsys)
    assert code in (0, 1, 2)  # doctor fails on a non-agent dir, drift exits 2 on drift
    assert isinstance(payload, dict)
    assert payload[key], f"{key} is empty: {payload}"


@pytest.mark.parametrize("argv", BARE_READ_COMMANDS, ids=" ".join)
def test_bots_read_commands_stdout_is_json(workspace, capsys, argv):
    code, payload = _run_json(argv, capsys)
    assert code == 0
    assert payload and payload[0]["name"] == "triage"


def test_every_read_command_accepts_json():
    """Every command tested above takes `--json` on the real parser."""
    parser = build_parser()
    for argv, _ in READ_COMMANDS:
        assert parser.parse_args([*argv, "--json"]).json is True
    for argv in BARE_READ_COMMANDS:
        assert parser.parse_args([*argv, "--json"]).json is True


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
    for argv in (["traces", "list"], ["traces", "stats"], ["traces", "errors"],
                 ["eval", "report"], ["skills", "search", "zzz-no-match"]):
        _, payload = _run_json(argv, capsys)
        assert isinstance(payload, dict)
