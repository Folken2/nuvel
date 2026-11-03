"""Tests for nuvel.pricing — pricing.json resolution, merge logic, and CLI.

No network: `fetch_openrouter` is exercised against a fake urlopen, and the
CLI commands that call it get a canned catalog. All file writes go to tmp_path.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
from pathlib import Path

import pytest

from nuvel import pricing


def build_parser() -> argparse.ArgumentParser:
    # Same wiring nuvel.cli uses for this subcommand, without importing the
    # whole CLI (which pulls in nuvel_mcp).
    parser = argparse.ArgumentParser(prog="nuvel")
    pricing.register(parser.add_subparsers(dest="command", required=True))
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


OPUS = "anthropic/claude-opus-4"
SONNET = "anthropic/claude-sonnet-4"
GPT4O_MINI = "openai/gpt-4o-mini"

# Prices in USD per token, as OpenRouter reports them.
OPUS_PRICE = {"input": 1.5e-05, "output": 7.5e-05}
SONNET_PRICE = {"input": 3e-06, "output": 1.5e-05}
MINI_PRICE = {"input": 1.5e-07, "output": 6e-07}

FAKE_CATALOG = {
    OPUS: {"input": 1.6e-05, "output": 8e-05},  # price moved
    SONNET: dict(SONNET_PRICE),  # unchanged
    GPT4O_MINI: dict(MINI_PRICE),  # not in pricing.json yet
}


def _write_pricing(path: Path, entries: dict, comments: dict | None = None) -> Path:
    raw = {**(comments or {}), **entries}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _make_agent(root: Path, pkg: str = "my_agent") -> Path:
    return _write_pricing(root / pkg / "plugins" / "pricing.json", {OPUS: dict(OPUS_PRICE)})


def _run(argv: list[str], capsys) -> tuple[int, str, str]:
    rc = main(argv)
    out, err = capsys.readouterr()
    return rc, out, err


# ── Path resolution ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("make_agent", "target", "expect"),
    [
        (True, None, "agent"),
        (False, None, "template"),
        (True, "template", "template"),
        (False, "template", "template"),
        (True, "agent", "agent"),
    ],
)
def test_resolve_pricing_path_defaults(tmp_path, make_agent, target, expect):
    agent_file = _make_agent(tmp_path) if make_agent else None
    got = pricing.resolve_pricing_path(target, cwd=tmp_path)
    expected = {"agent": agent_file, "template": pricing._TEMPLATE_PRICING}[expect]
    assert got == expected.resolve()


def test_resolve_target_agent_without_agent_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="no agent pricing.json"):
        pricing.resolve_pricing_path("agent", cwd=tmp_path)


def test_resolve_explicit_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="pricing file not found"):
        pricing.resolve_pricing_path(str(tmp_path / "nope.json"), cwd=tmp_path)


def test_find_agent_pricing_ignores_files_and_missing_dirs(tmp_path):
    (tmp_path / "plugins").mkdir()  # plugins dir with no pricing.json under a sub
    (tmp_path / "stray.txt").write_text("x")
    assert pricing._find_agent_pricing(tmp_path) is None
    assert pricing._find_agent_pricing(tmp_path / "does-not-exist") is None


# ── File IO ──────────────────────────────────────────────────────────


def test_load_splits_comments_from_entries(tmp_path):
    p = _write_pricing(
        tmp_path / "pricing.json",
        {OPUS: dict(OPUS_PRICE)},
        comments={"_comment": "hello", "_format": "fmt"},
    )
    pf = pricing.load(p)
    assert pf.path == p
    assert pf.comments == {"_comment": "hello", "_format": "fmt"}
    assert pf.entries == {OPUS: OPUS_PRICE}


def test_save_writes_comments_first_then_sorted_entries(tmp_path):
    p = tmp_path / "pricing.json"
    pf = pricing.PricingFile(
        path=p,
        comments={"_comment": "c"},
        entries={GPT4O_MINI: dict(MINI_PRICE), OPUS: dict(OPUS_PRICE)},
    )
    pricing.save(pf)
    text = p.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert list(json.loads(text)) == ["_comment", OPUS, GPT4O_MINI]  # sorted entries
    assert text.index("_comment") < text.index(OPUS) < text.index(GPT4O_MINI)


def test_load_malformed_json_raises(tmp_path):
    p = tmp_path / "pricing.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        pricing.load(p)


# ── Catalog lookup and merge ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (OPUS, OPUS_PRICE),  # exact match
        ("openrouter/" + OPUS, OPUS_PRICE),  # strip prefix from key
        (OPUS.split("/", 1)[1], None),  # bare name, no provider: not a catalog id
        ("openrouter/unknown/model", None),
        ("unknown/model", None),
    ],
)
def test_lookup_catalog(key, expected):
    catalog = {OPUS: dict(OPUS_PRICE)}
    assert pricing._lookup_catalog(catalog, key) == expected


def test_lookup_catalog_adds_prefix_when_catalog_uses_it():
    catalog = {"openrouter/" + OPUS: dict(OPUS_PRICE)}
    assert pricing._lookup_catalog(catalog, OPUS) == OPUS_PRICE


def test_same_compares_prices_with_tolerance():
    assert pricing._same(OPUS_PRICE, dict(OPUS_PRICE))
    assert pricing._same(OPUS_PRICE, {"input": OPUS_PRICE["input"] + 1e-13, "output": OPUS_PRICE["output"]})
    assert not pricing._same(OPUS_PRICE, {"input": 1.6e-05, "output": OPUS_PRICE["output"]})


def test_merge_refresh_classifies_every_existing_key():
    existing = {
        OPUS: dict(OPUS_PRICE),
        SONNET: dict(SONNET_PRICE),
        "deepseek/deepseek-r1": {"input": 7e-07, "output": 2.5e-06},
    }
    diff = pricing.merge_refresh(existing, FAKE_CATALOG)
    assert diff.updated == {OPUS: (OPUS_PRICE, FAKE_CATALOG[OPUS])}
    assert diff.unchanged == [SONNET]
    assert diff.missing_from_or == ["deepseek/deepseek-r1"]
    assert diff.added == {}
    assert diff.has_changes


def test_merge_refresh_only_keys_adds_new_model():
    diff = pricing.merge_refresh({}, FAKE_CATALOG, only_keys=[GPT4O_MINI])
    assert diff.added == {GPT4O_MINI: MINI_PRICE}
    assert diff.updated == {} and diff.unchanged == [] and diff.missing_from_or == []


def test_apply_diff_updates_and_adds_but_keeps_missing():
    pf = pricing.PricingFile(
        path=Path("unused.json"),
        entries={OPUS: dict(OPUS_PRICE), "deepseek/deepseek-r1": {"input": 7e-07, "output": 2.5e-06}},
    )
    diff = pricing.merge_refresh(pf.entries, FAKE_CATALOG, only_keys=None)
    diff.added[GPT4O_MINI] = dict(MINI_PRICE)
    pricing.apply_diff(pf, diff)
    assert pf.entries[OPUS] == FAKE_CATALOG[OPUS]
    assert pf.entries[GPT4O_MINI] == MINI_PRICE
    assert pf.entries["deepseek/deepseek-r1"] == {"input": 7e-07, "output": 2.5e-06}


# ── Fetcher (fake transport, no network) ─────────────────────────────


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(monkeypatch, *, body: bytes | None = None, exc: Exception | None = None):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["ua"] = req.get_header("User-agent")
        seen["timeout"] = timeout
        if exc is not None:
            raise exc
        return _FakeResponse(body)

    monkeypatch.setattr(pricing.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_fetch_openrouter_parses_prices_and_skips_bad_rows(monkeypatch):
    payload = {
        "data": [
            {"id": OPUS, "pricing": {"prompt": "0.000015", "completion": "0.000075"}},
            {"id": "no/pricing"},
            {"id": "null/completion", "pricing": {"prompt": "0.1", "completion": None}},
            {"id": "bad/float", "pricing": {"prompt": "free", "completion": "0"}},
            {"pricing": {"prompt": "0.1", "completion": "0.2"}},  # no id
        ]
    }
    seen = _patch_urlopen(monkeypatch, body=json.dumps(payload).encode())
    assert pricing.fetch_openrouter() == {OPUS: {"input": 1.5e-05, "output": 7.5e-05}}
    assert seen["url"] == pricing.OPENROUTER_MODELS_URL
    assert seen["ua"] == "nuvel-pricing-sync/1.0"
    assert seen["timeout"] == pricing._HTTP_TIMEOUT


def test_fetch_openrouter_network_error_becomes_runtime_error(monkeypatch):
    _patch_urlopen(monkeypatch, exc=urllib.error.URLError("boom"))
    with pytest.raises(RuntimeError, match="OpenRouter request failed"):
        pricing.fetch_openrouter()


def test_fetch_openrouter_non_json_body_becomes_runtime_error(monkeypatch):
    _patch_urlopen(monkeypatch, body=b"<html>proxy interstitial</html>")
    with pytest.raises(RuntimeError, match="OpenRouter returned invalid JSON") as info:
        pricing.fetch_openrouter()
    assert isinstance(info.value.__cause__, json.JSONDecodeError)


def test_fetch_openrouter_undecodable_body_becomes_runtime_error(monkeypatch):
    _patch_urlopen(monkeypatch, body=b"\xff\xfe\x00not utf-8")
    with pytest.raises(RuntimeError, match="OpenRouter returned invalid JSON") as info:
        pricing.fetch_openrouter()
    assert isinstance(info.value.__cause__, UnicodeDecodeError)


def test_fetch_openrouter_read_timeout_becomes_runtime_error(monkeypatch):
    class _StalledResponse(_FakeResponse):
        def read(self, *args, **kwargs):
            raise TimeoutError("timed out")

    def fake_urlopen(req, timeout):
        return _StalledResponse(b"")

    monkeypatch.setattr(pricing.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="OpenRouter request timed out") as info:
        pricing.fetch_openrouter()
    assert isinstance(info.value.__cause__, TimeoutError)


# ── Formatting ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("usd_per_token", "expected"),
    [
        (1.5e-05, "$15.0000/Mtok"),
        (3e-06, "$3.0000/Mtok"),
    ],
)
def test_fmt_price(usd_per_token, expected):
    assert pricing._fmt_price(usd_per_token) == expected


# ── CLI parser wiring ────────────────────────────────────────────────


def _parse(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(["pricing", *argv])


@pytest.mark.parametrize(
    ("argv", "func", "target"),
    [
        (["list"], pricing._cmd_list, None),
        (["list", "--target", "template"], pricing._cmd_list, "template"),
        (["list", "-t", "agent"], pricing._cmd_list, "agent"),
        (["sync"], pricing._cmd_sync, None),
        (["sync", "--dry-run"], pricing._cmd_sync, None),
        (["add", OPUS], pricing._cmd_add, None),
    ],
)
def test_parser_dispatches_subcommands(argv, func, target):
    args = _parse(argv)
    assert args.func is func
    assert args.target == target


@pytest.mark.parametrize("argv", [["pricing"], ["pricing", "bogus"], ["pricing", "add"]])
def test_parser_rejects_bad_invocations(argv):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv)
    assert exc.value.code == 2  # argparse usage error


# ── CLI commands (end-to-end through main) ───────────────────────────


def test_cli_list_prints_sorted_models_with_prices(tmp_path, monkeypatch, capsys):
    path = _write_pricing(
        tmp_path / "pricing.json",
        {SONNET: dict(SONNET_PRICE), OPUS: dict(OPUS_PRICE)},
    )
    monkeypatch.chdir(tmp_path)
    rc, out, _ = _run(["pricing", "list", "--target", str(path)], capsys)
    assert rc == 0
    lines = out.splitlines()
    assert lines[0] == f"file: {path.resolve()}"
    assert lines[1] == "models: 2"
    assert lines[2].lstrip().startswith(OPUS)
    assert "in $15.0000/Mtok  out $75.0000/Mtok" in lines[2]
    assert lines[3].lstrip().startswith(SONNET)
    assert "in $3.0000/Mtok  out $15.0000/Mtok" in lines[3]


def test_cli_list_empty_file(tmp_path, capsys):
    path = _write_pricing(tmp_path / "pricing.json", {})
    rc, out, _ = _run(["pricing", "list", "-t", str(path)], capsys)
    assert rc == 0
    assert "models: 0" in out


def test_cli_sync_dry_run_does_not_write(tmp_path, monkeypatch, capsys):
    path = _write_pricing(tmp_path / "pricing.json", {OPUS: dict(OPUS_PRICE), SONNET: dict(SONNET_PRICE)})
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(pricing, "fetch_openrouter", lambda: FAKE_CATALOG)
    rc, out, _ = _run(["pricing", "sync", "--dry-run", "-t", str(path)], capsys)
    assert rc == 0
    assert "~ updated (1):" in out
    assert "input  $15.0000/Mtok → $16.0000/Mtok" in out
    assert "= unchanged: 1" in out
    assert "(dry-run — no file written)" in out
    assert path.read_text(encoding="utf-8") == before


def test_cli_sync_writes_refreshed_prices(tmp_path, monkeypatch, capsys):
    path = _write_pricing(
        tmp_path / "pricing.json",
        {OPUS: dict(OPUS_PRICE), SONNET: dict(SONNET_PRICE)},
        comments={"_comment": "keep me"},
    )
    monkeypatch.setattr(pricing, "fetch_openrouter", lambda: FAKE_CATALOG)
    rc, out, _ = _run(["pricing", "sync", "-t", str(path)], capsys)
    assert rc == 0
    assert f"wrote {path}" in out
    saved = pricing.load(path)
    assert saved.entries[OPUS] == FAKE_CATALOG[OPUS]
    assert saved.entries[SONNET] == SONNET_PRICE
    assert saved.comments == {"_comment": "keep me"}
    assert GPT4O_MINI not in saved.entries  # sync never adds new models


def test_cli_sync_no_changes_leaves_file_alone(tmp_path, monkeypatch, capsys):
    path = _write_pricing(tmp_path / "pricing.json", {SONNET: dict(SONNET_PRICE)})
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(pricing, "fetch_openrouter", lambda: FAKE_CATALOG)
    rc, out, _ = _run(["pricing", "sync", "-t", str(path)], capsys)
    assert rc == 0
    assert "No changes." in out
    assert path.read_text(encoding="utf-8") == before


def test_cli_sync_missing_explicit_file_raises(tmp_path):
    # An explicit path is checked in resolve_pricing_path, so the command's own
    # "pricing.json not found" branch is not reached for --target <path>.
    with pytest.raises(FileNotFoundError, match="pricing file not found"):
        main(["pricing", "sync", "-t", str(tmp_path / "gone.json")])


def test_cli_add_new_model_writes_entry(tmp_path, monkeypatch, capsys):
    path = _write_pricing(tmp_path / "pricing.json", {SONNET: dict(SONNET_PRICE)})
    monkeypatch.setattr(pricing, "fetch_openrouter", lambda: FAKE_CATALOG)
    rc, out, _ = _run(["pricing", "add", GPT4O_MINI, "-t", str(path)], capsys)
    assert rc == 0
    assert f"added: {GPT4O_MINI}" in out
    assert pricing.load(path).entries[GPT4O_MINI] == MINI_PRICE


def test_cli_add_unchanged_model_does_not_rewrite(tmp_path, monkeypatch, capsys):
    path = _write_pricing(tmp_path / "pricing.json", {SONNET: dict(SONNET_PRICE)})
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(pricing, "fetch_openrouter", lambda: FAKE_CATALOG)
    rc, out, _ = _run(["pricing", "add", SONNET, "-t", str(path)], capsys)
    assert rc == 0
    assert f"unchanged: {SONNET}  in $3.0000/Mtok  out $15.0000/Mtok" in out.splitlines()
    assert path.read_text(encoding="utf-8") == before


def test_cli_add_refreshes_existing_model(tmp_path, monkeypatch, capsys):
    path = _write_pricing(tmp_path / "pricing.json", {OPUS: dict(OPUS_PRICE)})
    monkeypatch.setattr(pricing, "fetch_openrouter", lambda: FAKE_CATALOG)
    rc, out, _ = _run(["pricing", "add", OPUS, "-t", str(path)], capsys)
    assert rc == 0
    assert f"updated: {OPUS}" in out
    assert pricing.load(path).entries[OPUS] == FAKE_CATALOG[OPUS]
