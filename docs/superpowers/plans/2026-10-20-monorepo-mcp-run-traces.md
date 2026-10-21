# nuvel monorepo, `nuvel-mcp` and skill run traces — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the Skills MCP server as a lean `nuvel-mcp` package from a uv-workspace monorepo, add a content-free `record_run_trace` tool with a pluggable store, an optional authenticated Streamable HTTP transport, and teach `nuvel traces` / `dashboard` to read skill runs.

**Architecture:** `nuvel/mcp/*` moves to `packages/nuvel-mcp/src/nuvel_mcp/` with a `sys.modules` alias shim so `nuvel.mcp.*` keeps working. Tool handlers stay in `SkillsMCPServer`; stdio and HTTP are thin adapters over `handle_tools_list` / `handle_tools_call`. Traces are validated by a stdlib validator driven by a JSON Schema file and written through a `TraceStore` protocol.

**Tech Stack:** Python ≥3.11, setuptools, uv workspace (optional for contributors), pytest, official `mcp` SDK (HTTP extra only).

**Spec:** `docs/superpowers/specs/2026-10-20-monorepo-mcp-run-traces-design.md`

## Global Constraints

- `nuvel-mcp` runtime dependencies: **none**. Extra `http = ["mcp>=1.10,<2"]`.
- Versions during development: both packages `0.5.0.dev0`; `nuvel-cli` requires `nuvel-mcp>=0.5.0.dev0,<0.6` (in `requirements.txt`, which feeds `dynamic` deps). Release bumps both to the same version.
- uv is not required: every install path must also work with `pip install -e packages/nuvel-mcp -e .`.
- `nuvel mcp serve` flags and behaviour unchanged; `from nuvel.mcp import …` and `nuvel.mcp.<submodule>` keep working and emit one `DeprecationWarning` ("nuvel.mcp is deprecated; import nuvel_mcp — removal not before 0.7").
- Identifier regex: `^[a-z0-9][a-z0-9_.:-]{0,79}$`. Trace schema id: `nuvel.skill_run/1`. No free-text field anywhere in a trace.
- Enums — `host`: `claude-chat, claude-cowork, claude-code, claude-routine, codex, other`; `mode`: `scheduled, interactive`; `outcome`: `success, partial, failure, blocked, skipped`; step `status`: `ok, skipped, error`; error `class`: `auth, rate_limit, not_found, permission, timeout, tool_error, contract_violation, registry_missing, other`.
- Bounds — `steps` ≤ 50, step `counts` ≤ 20 keys with ints 0–100000, `tools` ≤ 50 with `calls`/`errors` ints 0–10000, `errors` ≤ 20, `duration_s` 0–86400, semver `^\d+\.\d+\.\d+$`, `run_id`/`install_id` UUIDv4.
- Trace store path: `{feedback_dir}/_runs/{plugin}.{skill}/{YYYY-MM-DD}.jsonl` (date from normalised `started_at`, UTC).
- Relay: when `$NUVEL_FEEDBACK_URL` is set, POST `{"type": "skill_run", "record": <normalised trace>}` **after** the local append; failures logged, not retried.
- HTTP: default tools `{"record_run_trace", "check_skill_health"}`; default bind `127.0.0.1:8765`; refuse a non-loopback `--host` without `--auth`; tokens from `$NUVEL_MCP_TOKENS` as `name:token,name:token`; principal = name, never the token; max request body 65536 bytes.
- Commits: conventional prefixes, author Folken2 <folkenai21@gmail.com>, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. One PR per delivery phase (Tasks 1–2, 3–6, 7–8, 9–10).

## Review Focus

1. **Monkeypatching through the shim** — tests and users patch `nuvel.mcp.server.X`; it must patch the real module (`nuvel.mcp.server is nuvel_mcp.server`). Test in Task 1.
2. **Two runs appending to the same day file at once** (two scheduled tasks firing together) — both lines land intact and parseable. Test in Task 5.
3. **A trace whose `started_at` is near midnight in the server's local timezone** — the file date comes from the normalised UTC timestamp, not the server clock. Test in Task 5.
4. **An oversized or non-JSON HTTP body** — rejected with 413 / 400 before parsing into the validator, without echoing the body. Test in Task 8.
5. **`check_skill_health(plugin=…)` for a skill that has never sent a trace** — returns a zeroed `runs` block, not an error. Test in Task 6.

---

## Phase 1 — Workspace + extraction (PR 1, no behaviour change)

### Task 1: Move the server into `nuvel_mcp` with a compat shim and shared CLI flags

**Files:**
- Move (git mv): `nuvel/mcp/{server,feedback,skills_loader,installer}.py` → `packages/nuvel-mcp/src/nuvel_mcp/`
- Create: `packages/nuvel-mcp/src/nuvel_mcp/__init__.py` (same public names as today's `nuvel/mcp/__init__.py`), `packages/nuvel-mcp/src/nuvel_mcp/cli.py`, `packages/nuvel-mcp/src/nuvel_mcp/__main__.py`
- Create: `packages/nuvel-mcp/pyproject.toml`
- Replace: `nuvel/mcp/__init__.py` (shim)
- Modify: `nuvel/commands/mcp_serve.py` (delegate), `requirements.txt`, `pyproject.toml` (version `0.5.0.dev0`, `[tool.uv.workspace] members = ["packages/*"]`, `[tool.uv.sources] nuvel-mcp = { workspace = true }`)
- Move tests: `tests/test_mcp_server.py`, `tests/test_mcp_hub_tools.py` → `packages/nuvel-mcp/tests/`, imports rewritten to `nuvel_mcp`
- Modify: `tests/test_skill_feedback_store.py` imports → `nuvel_mcp` (keeps its `nuvel.commands.mcp_serve` usage)
- Create: `tests/test_mcp_compat.py`, `packages/nuvel-mcp/tests/test_cli.py`

**Interfaces:**
- Produces: `nuvel_mcp.cli.add_serve_arguments(parser: argparse.ArgumentParser) -> None` (owns every `serve` flag: `--skills-dir`, `--theme`, `--require`, `--with-org-memory`, `--feedback-dir`, exact help text moved from `mcp_serve.py`); `nuvel_mcp.cli.run_serve(args: argparse.Namespace) -> int`; `nuvel_mcp.cli.main(argv: list[str] | None = None) -> int` (subcommand `serve`). Console script `nuvel-mcp = "nuvel_mcp.cli:main"`.
- Produces: `nuvel.commands.mcp_serve.register` unchanged in signature; it calls `add_serve_arguments` and sets `func` to a wrapper over `run_serve`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mcp_compat.py`:
```python
def test_shim_aliases_real_modules():
    import nuvel_mcp.server, nuvel.mcp.server
    assert nuvel.mcp.server is nuvel_mcp.server
    from nuvel.mcp import feedback, skills_loader
    import nuvel_mcp.feedback, nuvel_mcp.skills_loader
    assert feedback is nuvel_mcp.feedback and skills_loader is nuvel_mcp.skills_loader

def test_shim_public_names_and_warning(recwarn):
    import importlib, sys
    sys.modules.pop("nuvel.mcp", None)
    mod = importlib.import_module("nuvel.mcp")
    assert mod.SkillsMCPServer is __import__("nuvel_mcp").SkillsMCPServer
    assert any(issubclass(w.category, DeprecationWarning) and "nuvel_mcp" in str(w.message) for w in recwarn)

def test_monkeypatch_through_shim_hits_real_module(monkeypatch):
    import nuvel.mcp.server as shim_server, nuvel_mcp.server as real
    monkeypatch.setattr(shim_server, "GITHUB_ISSUES_URL", "https://example.invalid")
    assert real.GITHUB_ISSUES_URL == "https://example.invalid"
```
`packages/nuvel-mcp/tests/test_cli.py`:
```python
def test_nuvel_mcp_and_nuvel_cli_share_flags():
    import argparse
    from nuvel_mcp.cli import add_serve_arguments
    a = argparse.ArgumentParser(); add_serve_arguments(a)
    from nuvel.commands import mcp_serve
    root = argparse.ArgumentParser(); mcp_serve.register(root.add_subparsers())
    serve = root._subparsers._group_actions[0].choices["mcp"]._subparsers._group_actions[0].choices["serve"]
    assert {x.dest for x in a._actions} == {x.dest for x in serve._actions}

def test_module_entrypoint_runs_help(capsys):
    from nuvel_mcp.cli import main
    import pytest
    with pytest.raises(SystemExit) as e: main(["serve", "--help"])
    assert e.value.code == 0 and "--feedback-dir" in capsys.readouterr().out
```
(`test_nuvel_mcp_and_nuvel_cli_share_flags` skips when `nuvel` isn't importable — `pytest.importorskip("nuvel.commands.mcp_serve")`.)

- [ ] **Step 2: Run to verify they fail**
Run: `pytest tests/test_mcp_compat.py packages/nuvel-mcp/tests/test_cli.py -v` → FAIL (`ModuleNotFoundError: nuvel_mcp`).

- [ ] **Step 3: Implement**
- `packages/nuvel-mcp/pyproject.toml`: setuptools, `src` layout, `name = "nuvel-mcp"`, `version = "0.5.0.dev0"`, `requires-python = ">=3.11"`, `dependencies = []`, `[project.optional-dependencies] http = ["mcp>=1.10,<2"]`, script above, package-data `nuvel_mcp = ["schemas/*.json"]`, MIT, author Albert Folch.
- Inside moved modules replace `from nuvel.mcp…` with `from nuvel_mcp…`. The lazy `from nuvel.memory.factory import build_default_service` in `_resolve_templates` stays lazy; on `ImportError` log `--with-org-memory needs nuvel-cli installed`.
- Shim: import `nuvel_mcp` and its four submodules, set `sys.modules["nuvel.mcp.<name>"]` to each real module, re-export the four public names, `warnings.warn(..., DeprecationWarning, stacklevel=2)` once.
- `requirements.txt`: add `nuvel-mcp>=0.5.0.dev0,<0.6`.
- `Makefile` `install`: `pip install -e packages/nuvel-mcp -e .`

- [ ] **Step 4: Run the whole suite**
Run: `pip install -e packages/nuvel-mcp -e ".[test]" && pytest tests packages/nuvel-mcp/tests -q` → all pass (existing MCP and feedback-store tests included).

- [ ] **Step 5: Commit** — `refactor: extract the Skills MCP server into the nuvel-mcp package`

### Task 2: Packaging, CI, release and docs for two packages

**Files:**
- Create: `packages/nuvel-mcp/tests/test_packaging.py`, `packages/nuvel-mcp/README.md`
- Modify: `.github/workflows/tests.yml`, `.github/workflows/release.yml`, `CONTRIBUTING.md`, `CLAUDE.md` (commands + repo layout), `README.md` (CLI table row for `nuvel-mcp serve`), `docs/reference/cli.md`, `docs/reference/repo-layout.md`, `.claude/skills/nuvel/SKILL.md`

**Interfaces:**
- Consumes: Task 1's package layout.

- [ ] **Step 1: Write the failing test**
```python
def test_wheel_has_no_runtime_dependencies(tmp_path):
    import subprocess, sys, zipfile, pathlib
    pkg = pathlib.Path(__file__).parents[1]
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", tmp_path, pkg], check=True)
    whl = next(tmp_path.glob("nuvel_mcp-*.whl"))
    meta = next(n for n in zipfile.ZipFile(whl).namelist() if n.endswith("METADATA"))
    lines = zipfile.ZipFile(whl).read(meta).decode().splitlines()
    requires = [l for l in lines if l.startswith("Requires-Dist:")]
    assert requires and all("extra ==" in l for l in requires)   # only the http extra
    assert any(l.startswith("Requires-Dist: mcp") for l in requires)
```
- [ ] **Step 2:** `pytest packages/nuvel-mcp/tests/test_packaging.py -v` → PASS already if Task 1 is right; if it fails, fix `pyproject.toml`.
- [ ] **Step 3: CI and release**
- `tests.yml`: install with `pip install -e packages/nuvel-mcp` then `pip install -r requirements.txt` then `pip install -e ".[test]"`; run `pytest tests packages/nuvel-mcp/tests -v`.
- `release.yml` build job: verify the tag equals **both** versions (root and `packages/nuvel-mcp/pyproject.toml`); `python -m build --outdir dist .` and `python -m build --outdir dist packages/nuvel-mcp`. Publish job unchanged (uploads everything in `dist/`); `github-release` unchanged.
- [ ] **Step 4: Docs** — `nuvel-mcp serve` (standalone, `uvx nuvel-mcp serve --skills-dir …`), the workspace layout, `make install`, and the deprecation of `nuvel.mcp`. Note in `CONTRIBUTING.md`: **before the first release, a PyPI trusted publisher for `nuvel-mcp` must be configured by the maintainer** (manual, outside the repo).
- [ ] **Step 5:** `pytest tests packages/nuvel-mcp/tests -q` → pass. Commit — `build: publish nuvel-cli and nuvel-mcp from one workspace`

---

## Phase 2 — Feedback-only mode and `record_run_trace` (PR 2)

### Task 3: Hub-optional server and tool allow-list

**Files:**
- Modify: `packages/nuvel-mcp/src/nuvel_mcp/server.py`, `packages/nuvel-mcp/src/nuvel_mcp/cli.py`
- Test: `packages/nuvel-mcp/tests/test_feedback_only.py`

**Interfaces:**
- Produces: `SkillsMCPServer.__init__(self, loader: SkillsLoader | None, *, theme=None, require_filter=None, with_org_memory=None, feedback_dir=None, tools: frozenset[str] | None = None, trace_store: "TraceStore | None" = None)`.
- Produces: module constants `HUB_TOOLS: frozenset[str]` (search_skills, list_skills, list_themes, get_skill, install_skill, fence_value), `FEEDBACK_TOOLS: frozenset[str]` (record_feedback, check_skill_health, propose_improvement, record_run_trace), `HTTP_DEFAULT_TOOLS = frozenset({"record_run_trace", "check_skill_health"})`.
- Produces: `class ToolInputError(Exception)` with `.errors: list[str]`; `handle_tools_call` turns it into a result with `isError: True` and text `{"status": "rejected", "errors": [...]}`.
- Produces: CLI flag `--tools a,b` (parsed to `frozenset`), `--skills-dir` default changes from `"."` to `None`.

- [ ] **Step 1: Failing tests**
```python
def test_no_hub_lists_only_feedback_tools(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    names = {t["name"] for t in s.handle_tools_list({})["tools"]}
    assert names == FEEDBACK_TOOLS
    assert s.handle_resources_list({})["resources"] == []

def test_tools_allowlist_filters_list_and_call(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path, tools=frozenset({"check_skill_health"}))
    assert [t["name"] for t in s.handle_tools_list({})["tools"]] == ["check_skill_health"]
    with pytest.raises(McpError):
        s.handle_tools_call({"name": "record_feedback", "arguments": {}})

def test_explicit_skills_dir_without_index_still_errors(tmp_path, capsys):
    assert main(["serve", "--skills-dir", str(tmp_path)]) == 1
```
- [ ] **Step 2:** run → FAIL. **Step 3:** implement (resource/hub handlers return empty / unknown-tool when `loader is None`; `run_serve` builds no loader when `args.skills_dir is None`; with `--skills-dir` given, `SkillsError` still exits 1). **Step 4:** `pytest packages/nuvel-mcp/tests tests -q` → pass (the old default `"."` is now explicit in any test that relied on it). **Step 5:** commit — `feat(mcp): feedback-only mode and --tools allow-list`

### Task 4: Trace schema and stdlib validator

**Files:**
- Create: `packages/nuvel-mcp/src/nuvel_mcp/schemas/skill_run.v1.schema.json` (JSON Schema 2020-12, every object `additionalProperties: false`, values from Global Constraints)
- Create: `packages/nuvel-mcp/src/nuvel_mcp/traces.py` (validator part)
- Test: `packages/nuvel-mcp/tests/test_trace_schema.py`

**Interfaces:**
- Produces: `SCHEMA_ID = "nuvel.skill_run/1"`, `IDENTIFIER_RE`, `load_schema() -> dict` (reads the packaged JSON via `importlib.resources`), `validate_skill_run(trace: object) -> list[str]` — returns failing JSON-pointer paths like `"/steps/2/id"` and **never values**; empty list = valid. Supports exactly the keywords the schema file uses (`type`, `enum`, `const`, `pattern`, `minimum`, `maximum`, `maxItems`, `maxProperties`, `required`, `properties`, `additionalProperties`, `items`, `propertyNames`, `format: uuid|date-time`).

- [ ] **Step 1: Failing tests** — the spec's example trace (copied verbatim into `tests/fixtures/skill_run_example.json`) validates; then, parametrised:
```python
@pytest.mark.parametrize("bad", ["Ana Garcia", "ana@acme.com", "Radar", "rádar", "x" * 81, "a b", ""])
@pytest.mark.parametrize("path", [("plugin","name"), ("skill","name"), ("steps",0,"id"), ("tools",0,"name")])
def test_identifier_fields_reject_content(example, bad, path):
    t = deep_set(copy.deepcopy(example), path, bad)
    errors = validate_skill_run(t)
    assert errors and all(bad not in e for e in errors)

def test_underscores_allowed(example):
    assert validate_skill_run(deep_set(example, ("tools",0,"name"), "gmail.search_threads")) == []

@pytest.mark.parametrize("where", [(), ("plugin",), ("steps",0), ("steps",0,"counts"), ("tools",0), ("safety",)])
def test_unknown_keys_rejected_everywhere(example, where):
    t = copy.deepcopy(example); deep_get(t, where)["note"] = 1
    assert validate_skill_run(t)

def test_client_cannot_send_tenant(example):
    assert "/tenant" in " ".join(validate_skill_run({**example, "tenant": "x"}))

def test_bounds(example): ...  # 51 steps, 21 count keys, duration_s 86401, calls 10001 → each rejected
```
- [ ] **Step 2:** FAIL. **Step 3:** implement the schema file and the recursive validator. **Step 4:** pass. **Step 5:** commit — `feat(mcp): nuvel.skill_run/1 trace schema and validator`

### Task 5: `TraceStore`, normalisation, relay and `record_run_trace`

**Files:**
- Modify: `packages/nuvel-mcp/src/nuvel_mcp/traces.py`, `server.py`, `cli.py`
- Create: `packages/nuvel-mcp/src/nuvel_mcp/auth.py` containing only `current_principal` (Task 7 adds the rest)
- Test: `packages/nuvel-mcp/tests/test_trace_store.py`, `packages/nuvel-mcp/tests/test_record_run_trace.py`

**Interfaces:**
- Produces: `normalize(trace: dict, tenant: str | None) -> dict` (truncate `started_at` to the minute in UTC with `Z`; add `tenant` only when not None).
- Produces: `class TraceStore(Protocol): def append(self, trace: dict) -> bool` (False = duplicate `run_id`) `; def query(self, plugin: str, skill: str, since: datetime, tenant: str | None) -> list[dict]` (tenant `None` = all).
- Produces: `class LocalTraceStore(TraceStore)` with `__init__(self, feedback_dir: Path)`; `load_trace_store(spec: str, feedback_dir: Path) -> TraceStore` where `spec` is `"local"` or `"module:factory"` (factory called with no args).
- Produces: `SkillsMCPServer.tool_record_run_trace(self, args) -> dict` — `args = {"trace": {...}}`; returns `{"status": "recorded" | "duplicate", "run_id": str}`; invalid → `ToolInputError(errors)`. Tenant comes from `nuvel_mcp.auth.current_principal: contextvars.ContextVar[str | None]` (default `None`), created in this task.
- Produces: CLI flag `--trace-store local|module:factory` (default `local`).
- Tool description must say: ids come only from the skill's published step catalog; never put names, subjects, addresses or any user content in a trace.

- [ ] **Step 1: Failing tests**
```python
def test_append_writes_utc_day_file(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    t = normalize({**example, "started_at": "2026-10-20T23:59:40+02:00"}, tenant=None)
    assert store.append(t) is True
    f = tmp_path / "_runs" / "chief-of-staff.radar" / "2026-10-20.jsonl"
    assert json.loads(f.read_text())["started_at"] == "2026-10-20T21:59:00Z"

def test_duplicate_run_id_ignored(tmp_path, example): ...   # second append → False, one line

def test_concurrent_appends_stay_line_atomic(tmp_path, example):
    store = LocalTraceStore(tmp_path)
    traces = [normalize({**example, "run_id": str(uuid.uuid4())}, None) for _ in range(50)]
    with ThreadPoolExecutor(8) as ex: list(ex.map(store.append, traces))
    lines = next((tmp_path / "_runs").rglob("*.jsonl")).read_text().splitlines()
    assert len(lines) == 50 and all(json.loads(l) for l in lines)

def test_record_run_trace_rejects_without_echo(tmp_path, example):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res = s.handle_tools_call({"name": "record_run_trace", "arguments": {"trace": {**example, "plugin": {"name": "ana@acme.com", "version": "0.2.0"}}}})
    assert res["isError"] is True and "ana@acme.com" not in json.dumps(res)

def test_relay_posts_after_local_append(tmp_path, example, monkeypatch): ...
    # NUVEL_FEEDBACK_URL set, urlopen patched: local file has the line AND the POST body is
    # {"type": "skill_run", "record": <normalised>}; a raising urlopen still returns "recorded".
```
- [ ] **Step 2:** FAIL. **Step 3:** implement. Append with a per-file `threading.Lock` plus a single `write()` of one line opened in `"a"` mode; duplicate check scans that day's file for the `run_id`. Relay reuses the existing `_relay_feedback` helper. **Step 4:** pass. **Step 5:** commit — `feat(mcp): record_run_trace with a pluggable trace store`

### Task 6: `check_skill_health(plugin=…)` runs block and Phase 2 docs

**Files:**
- Modify: `packages/nuvel-mcp/src/nuvel_mcp/server.py` (tool schema + handler)
- Create: `docs/reference/skill-run-traces.md` (schema table, privacy-by-construction argument, step catalogs, store layout, relay), `mkdocs.yml` nav entry
- Modify: `docs/reference/cli.md` (`--skills-dir` optional, `--tools`, `--trace-store`), `.claude/skills/nuvel/SKILL.md`, `CHANGELOG.md` (Unreleased)
- Test: `packages/nuvel-mcp/tests/test_health_runs.py`

**Interfaces:**
- Consumes: `TraceStore.query`, `current_principal` (both Task 5).
- Produces: `check_skill_health` input adds optional `plugin: string`. With it, the result gains `"runs": {"last_30_days": {<outcome>: int}, "errors_by_step": {<step id>: {<class>: int}}, "total": int}` for `plugin.skill`; without it, output is byte-identical to today.

- [ ] **Step 1: Failing tests** — runs block counts two stored traces (one `failure` with an `auth` error on `radar.2.email`); a plugin/skill with no traces returns `{"last_30_days": {all five outcomes: 0}, "errors_by_step": {}, "total": 0}`; a call without `plugin` matches the pre-change output fixture; with `current_principal` set to `"acme"`, traces stamped `tenant="other"` are excluded.
- [ ] **Step 2–4:** FAIL → implement → pass (`pytest packages/nuvel-mcp/tests tests -q`).
- [ ] **Step 5:** commit — `feat(mcp): skill health includes run traces; document skill run traces`

---

## Phase 3 — Streamable HTTP (PR 3)

### Task 7: Authenticator protocol and bearer tokens

**Files:**
- Modify: `packages/nuvel-mcp/src/nuvel_mcp/auth.py`
- Test: `packages/nuvel-mcp/tests/test_auth.py`

**Interfaces:**
- Consumes: `current_principal` (Task 5).
- Produces: `class Authenticator(Protocol): def authenticate(self, headers: Mapping[str, str]) -> str | None`; `class BearerAuthenticator` with `from_env(env: Mapping[str, str] = os.environ) -> "BearerAuthenticator"` reading `NUVEL_MCP_TOKENS`; `load_authenticator(spec: str | None) -> Authenticator | None` for `None`, `"bearer"`, or `"module:factory"`.

- [ ] **Step 1: Failing tests** — `name:token` pairs parse; `Authorization: Bearer <token>` → `"name"`; wrong/missing header → `None`; comparison uses `hmac.compare_digest`; malformed env entry (no colon, empty name) raises `ValueError` at load time naming the entry index, not its value; the token string never appears in `repr(authenticator)`.
- [ ] **Step 2–4:** FAIL → implement → pass. **Step 5:** commit — `feat(mcp): pluggable authenticator with bearer tokens`

### Task 8: HTTP adapter, CLI flags and deploy docs

**Files:**
- Create: `packages/nuvel-mcp/src/nuvel_mcp/http.py`
- Modify: `packages/nuvel-mcp/src/nuvel_mcp/cli.py` (`--transport`, `--host`, `--port`, `--auth`)
- Create: `docs/how-to/deploy-skills-mcp.md` (+ `mkdocs.yml` nav)
- Test: `packages/nuvel-mcp/tests/test_http.py` (module-level `pytest.importorskip("mcp")`)

**Interfaces:**
- Consumes: `SkillsMCPServer.handle_tools_list / handle_tools_call`, `HTTP_DEFAULT_TOOLS`, `Authenticator`, `current_principal`.
- Produces: `build_app(server: SkillsMCPServer, authenticator: Authenticator | None, max_body_bytes: int = 65536)` → ASGI app (mcp SDK low-level `Server` + Streamable HTTP session manager; `list_tools`/`call_tool` delegate to the shared handlers); an ASGI middleware authenticates, sets `current_principal` for the request, returns 401 on failure, 413 when `Content-Length` > `max_body_bytes`.
- Produces: `serve_http(server, host: str, port: int, authenticator: Authenticator | None) -> None` (uvicorn).
- CLI rules: `--transport http` without the extra exits 1 with `pip install "nuvel-mcp[http]"`; HTTP uses `HTTP_DEFAULT_TOOLS` unless `--tools` is given; a host other than `127.0.0.1`/`localhost`/`::1` without `--auth` exits 1.

- [ ] **Step 1: Failing tests** — with the mcp SDK's Streamable HTTP client against `build_app` (in-process ASGI transport): `tools/list` returns exactly `HTTP_DEFAULT_TOOLS`; `record_run_trace` round-trips and the stored line has `tenant == "acme"` for token `acme:s3cret`; no/wrong token → 401; a 70 000-byte body → 413 and the response doesn't contain it; invalid JSON → 400 without echo; `main(["serve","--transport","http","--host","0.0.0.0"])` → 1.
- [ ] **Step 2–4:** FAIL → implement → pass.
- [ ] **Step 5: Docs** — running `uvx --from "nuvel-mcp[http]" nuvel-mcp serve --transport http --auth bearer`, putting it behind TLS, what is and isn't exposed over HTTP and why (free-text tools off), and that hosting/OAuth live in nuvel-cloud. Commit — `feat(mcp): Streamable HTTP transport behind an authenticator`

---

## Phase 4 — Reading skill runs in nuvel-cli (PR 4)

### Task 9: `nuvel traces` reads `nuvel.skill_run/1`

**Files:**
- Modify: `nuvel/traces_cli.py`
- Test: `tests/test_traces_skill_run.py`, fixture `tests/fixtures/traces_mixed/` (one ADK file, one CASDK file, one `_runs/chief-of-staff.radar/2026-10-20.jsonl`)

**Interfaces:**
- Produces: `Run.steps: list[dict] = field(default_factory=list)` and `schema` value `"skill_run"`; in `_parse_file_runs`, a record with `rec.get("schema") == "nuvel.skill_run/1"` → `Run(agent=f"{plugin}:{skill}", session_id=run_id, trace_id=run_id, started_at=started_at, duration_ms=duration_s*1000, tool_calls=sum(calls), subtype=outcome, schema="skill_run", steps=steps)`.
- Produces: discovery adds every directory under `nuvel_mcp.feedback.resolve_feedback_dir() / "_runs"`.
- Produces: `step_error_rates(runs: list[Run]) -> list[tuple[str, int, int]]` — `(step_id, runs_with_step, runs_where_step_errored)`, sorted by error ratio desc then step id; `nuvel traces stats` prints a "Skill steps" table when any skill run is present.

- [ ] **Step 1: Failing tests** — `_collect_runs([fixture])` returns three runs with schemas `{"adk","casdk","skill_run"}`; the skill run has `agent == "chief-of-staff:radar"`, `duration_ms == 58000`, `tool_calls == 6`; `step_error_rates` on two runs where `radar.2.email` errored once returns `("radar.2.email", 2, 1)` first; `nuvel traces stats --source <fixture>` output contains `Skill steps`.
- [ ] **Step 2–4:** FAIL → implement → pass (`pytest tests -q`). **Step 5:** commit — `feat(traces): read nuvel.skill_run/1 traces`

### Task 10: Dashboard shows skill runs, and release notes

**Files:**
- Modify: `nuvel/dashboard/app.py` (`_run_has_error`, index context), the index template under `nuvel/dashboard/templates/`
- Modify: `CHANGELOG.md`, `README.md` (one line under traces/dashboard)
- Test: `tests/test_dashboard_skill_runs.py`

**Interfaces:**
- Consumes: `Run.schema == "skill_run"`, `Run.subtype`, `step_error_rates` (Task 9).
- Produces: `_run_has_error(run)` is `True` for a skill run whose `subtype` is `failure` or `blocked`; the index page receives `step_rates` and renders a "Skill steps" table only when non-empty.

- [ ] **Step 1: Failing tests** — with `TestClient(build_app(TraceLoader([fixture])))`, `/` lists `chief-of-staff:radar`, marks the failed skill run as an error, and contains `radar.2.email`; with no skill runs the "Skill steps" heading is absent.
- [ ] **Step 2–4:** FAIL → implement → pass. **Step 5:** commit — `feat(dashboard): show skill runs and per-step error rates`

---

## Phase 5 — Adopt #94 (spec amendment 2026-10-21)

Global constraints for Phase 5 (in addition to the above, which they override where they conflict):
- `nuvel-mcp` runtime dependencies: none, and **no extras** (the `http` extra is removed).
- HTTP defaults: #94's (`--host 127.0.0.1`, `--port 8080`, auth required unless `--no-auth`, `--allow-remote` for non-loopback).
- HTTP default tools: `search_skills, list_skills, list_themes, get_skill, fence_value, record_run_trace, check_skill_health`; `record_feedback`, `propose_improvement`, `install_skill` only via `--tools`.
- Tenant = OAuth `client_id`, never `subject`.
- Every task must keep the full suite green: `uv run pytest tests packages/nuvel-mcp/tests -q`.

### Task 11: Merge main and relocate #94 into `nuvel_mcp`

**Files:** merge `origin/main`; move `nuvel/mcp/http_transport.py`, `nuvel/mcp/oauth.py` → `packages/nuvel-mcp/src/nuvel_mcp/`; move `tests/test_mcp_http.py`, `tests/test_mcp_oauth.py`, `tests/_mcp_http_helpers.py` → `packages/nuvel-mcp/tests/`; resolve `AGENTS.md`, `CHANGELOG.md`, `nuvel/commands/mcp_serve.py`; extend the `nuvel.mcp` shim; move #94's flags and `client`/`token` subcommands into `nuvel_mcp.cli`.
**Interfaces:** Produces `nuvel_mcp.http_transport.serve_http(mcp, host, port, config, provider, no_auth=…)` and `nuvel_mcp.oauth.*` unchanged in behaviour; `nuvel mcp client|token …` and `nuvel-mcp client|token …` both work.
- [ ] Steps: merge with conflicts resolved per the amendment (keep this branch's delegation in `mcp_serve.py`; keep `--skills-dir` optional; CHANGELOG keeps both sides' entries); move files with `git mv`, rewrite imports to `nuvel_mcp`; both test suites green with this branch's HTTP layer still present (it is removed in Task 12); commit `merge: main (#94 HTTP transport + OAuth) into the nuvel-mcp branch` and `refactor(mcp): move #94's HTTP transport and OAuth into nuvel_mcp`.

### Task 12: Remove this branch's HTTP layer

**Files:** delete `packages/nuvel-mcp/src/nuvel_mcp/http.py` and its tests; strip `BearerAuthenticator`/`load_authenticator` from `auth.py` (keep `current_principal`); remove `--auth`, `--allowed-host`, the SDK-based `--transport http` wiring and the `http` extra (pyproject, uv.lock, CI install line, packaging test, dev dependency group); rewrite `docs/how-to/deploy-skills-mcp.md` around #94 (OAuth, clients, `--allow-remote`, reverse proxy with #94's public URL setting).
- [ ] Test first: packaging test asserts the `nuvel-mcp` wheel has **no** `Requires-Dist` lines at all; CLI test asserts `--auth` / `--allowed-host` are gone and `--transport http` uses #94's server. Commit `refactor(mcp): drop the SDK HTTP transport in favour of #94's`.

### Task 13: Port the protections onto #94's transport

**Files:** `nuvel_mcp/http_transport.py`, `nuvel_mcp/server.py`, `nuvel_mcp/cli.py`, tests in `packages/nuvel-mcp/tests/test_mcp_http.py` (or a new `test_http_protections.py`).
**Interfaces:** Consumes `TokenInfo.client_id`, `SkillsMCPServer.health_runs_only`, `current_principal`, `HTTP_DEFAULT_TOOLS` (redefined to the Phase 5 set).
- [ ] Failing tests first, over #94's real HTTP server: (1) `tools/list` without `--tools` returns exactly the Phase 5 default set; `record_feedback` call → refused; (2) a queued proposal's free text never appears in any HTTP tool result (`get_skill`, `check_skill_health`); `check_skill_health` without `plugin` → invalid-params, no echo; (3) two OAuth clients record traces concurrently → each stored trace's `tenant` equals its own `client_id`, never a `subject`; each sees only its own runs in `check_skill_health`; `--no-auth` → tenant absent; (4) loopback bind, request with `Origin: http://evil.example` → refused with and without `--no-auth`.
- [ ] Implement, green, commit `feat(mcp): remote mode, gated tool set and client_id tenancy on the HTTP transport`.

### Task 14: Docs and release notes reconciliation

**Files:** `CHANGELOG.md` (one coherent Unreleased section covering #94 + this branch), `docs/reference/skill-run-traces.md` (tenancy = OAuth client_id; HTTP tool set), `docs/reference/cli.md`, `.claude/skills/nuvel/SKILL.md`, `CLAUDE.md` monorepo section (no extras), `packages/nuvel-mcp/README.md`, `CONTRIBUTING.md` (pre-merge PyPI gate unchanged).
- [ ] `mkdocs build --strict` passes; grep shows no remaining references to `--auth bearer`, `NUVEL_MCP_TOKENS`, `--allowed-host`, `nuvel-mcp[http]` or `mcp>=1.30`. Commit `docs: reconcile MCP docs with #94's transport and OAuth`.
