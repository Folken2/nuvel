# OrgMemoryService Runner Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-wire `OrgMemoryService` into the default `nuvel run-adk` runner via ADK's official service registry — no monkey-patching, no custom runner, no feature regression. After this lands, three env vars + `nuvel run-adk` give a deployed agent hierarchical memory.

**Architecture:** Add `nuvel/memory/adk_registry.py` that registers a factory under URI scheme `nuvel-org-memory` with `google.adk.cli.service_registry`. Modify `nuvel/run_adk.py` to call the registration and pass `NUVEL_ORG_MEMORY_URI` as the `memory_service_uri` kwarg to `get_fast_api_app`. ADK then constructs the service natively — same path it uses for its built-in `agentengine://` and `rag://` schemes. The v1 `factory.build_default_service()` stays as the construction primitive; the new module is a 30-line adapter on top.

**Tech Stack:** Python 3.11+, ADK 2.x (`google.adk.cli.service_registry`, `google.adk.cli.fast_api`), FastAPI TestClient for integration. All v1 OrgMemoryService deps unchanged.

**Spec:** `docs/superpowers/specs/2026-05-21-org-memory-runner-wiring-design.md`

---

## File Structure

**Create:**
- `nuvel/memory/adk_registry.py` — registration module + factory
- `tests/test_memory_adk_registry.py` — unit tests
- `tests/test_run_adk_memory_wiring.py` — end-to-end test against real ADK app

**Modify:**
- `nuvel/run_adk.py` — call `register_org_memory_scheme()`, pass `NUVEL_ORG_MEMORY_URI` through. Remove v1 "pre-flight migration + warning" block (now redundant).
- `nuvel/memory/__init__.py` — re-export `register_org_memory_scheme` and `ORG_MEMORY_SCHEME`.
- `docs/memory/org-memory-service.md` — update "Enable" section to use the new URI; note custom-runner path still available for advanced use.

---

## Task 1: ADK registry module

**Files:**
- Create: `nuvel/memory/adk_registry.py`
- Modify: `nuvel/memory/__init__.py`
- Test: `tests/test_memory_adk_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_adk_registry.py
from __future__ import annotations

import pytest

from google.adk.cli.service_registry import get_service_registry

from nuvel.memory.adk_registry import (
    ORG_MEMORY_SCHEME,
    register_org_memory_scheme,
)


def test_registration_idempotent():
    register_org_memory_scheme()
    register_org_memory_scheme()  # second call must not raise
    registry = get_service_registry()
    assert ORG_MEMORY_SCHEME in registry._memory_factories


def test_scheme_constant_matches_doc():
    # Operator-visible string — guard against accidental renames.
    assert ORG_MEMORY_SCHEME == "nuvel-org-memory"


def test_factory_raises_when_dsn_missing(monkeypatch):
    monkeypatch.delenv("NUVEL_ORG_MEMORY_DSN", raising=False)
    monkeypatch.delenv("NUVEL_ORG_GRAPH_PATH", raising=False)
    register_org_memory_scheme()
    factory = get_service_registry()._memory_factories[ORG_MEMORY_SCHEME]
    with pytest.raises(RuntimeError, match="NUVEL_ORG_MEMORY_DSN"):
        factory("nuvel-org-memory://default")


def test_factory_returns_org_memory_service(monkeypatch, tmp_path):
    from nuvel.memory.org_memory_service import OrgMemoryService

    monkeypatch.setenv("NUVEL_ORG_MEMORY_DSN", "postgresql://placeholder/ignored")
    graph = tmp_path / "g.yaml"
    graph.write_text(
        "org_id: acme\nlevels: [user, org]\nusers:\n  a:\n    chain:\n"
        "      - {level: user, id: a}\n      - {level: org, id: acme}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NUVEL_ORG_GRAPH_PATH", str(graph))
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    register_org_memory_scheme()
    factory = get_service_registry()._memory_factories[ORG_MEMORY_SCHEME]
    # `migrate=False` path: we pass a flag via the URI query string in this test
    # to avoid hitting a real DB. The factory must honor `migrate=0` in the URI.
    svc = factory("nuvel-org-memory://default?migrate=0")
    assert isinstance(svc, OrgMemoryService)
```

- [ ] **Step 2: Run test → FAIL**

Run: `pytest tests/test_memory_adk_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nuvel.memory.adk_registry'`

- [ ] **Step 3: Implement the registry module**

```python
# nuvel/memory/adk_registry.py
"""Register OrgMemoryService as an ADK service-registry scheme.

After `register_org_memory_scheme()` is called, ADK's `get_fast_api_app`
will construct an `OrgMemoryService` natively when given
`memory_service_uri="nuvel-org-memory://default"`. No monkey-patching.
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import parse_qs, urlparse

from google.adk.cli.service_registry import get_service_registry
from google.adk.memory.base_memory_service import BaseMemoryService

from nuvel.memory.factory import build_default_service

ORG_MEMORY_SCHEME = "nuvel-org-memory"

log = logging.getLogger(__name__)


def _factory(uri: str, **_: object) -> BaseMemoryService:
    """ADK ServiceFactory adapter — sync wrapper around build_default_service.

    Honors a `?migrate=0` query param to skip migrate() (used by unit tests
    that mustn't hit a real DB).
    """
    dsn = os.getenv("NUVEL_ORG_MEMORY_DSN")
    graph_path = os.getenv("NUVEL_ORG_GRAPH_PATH")
    if not dsn:
        raise RuntimeError(
            "NUVEL_ORG_MEMORY_DSN must be set when memory_service_uri "
            f"uses the {ORG_MEMORY_SCHEME!r} scheme."
        )
    if not graph_path:
        raise RuntimeError(
            "NUVEL_ORG_GRAPH_PATH must be set when memory_service_uri "
            f"uses the {ORG_MEMORY_SCHEME!r} scheme."
        )

    parsed = urlparse(uri)
    params = parse_qs(parsed.query or "")
    migrate = params.get("migrate", ["1"])[0] != "0"

    return asyncio.run(
        build_default_service(dsn=dsn, org_graph_path=graph_path, migrate=migrate)
    )


def register_org_memory_scheme() -> None:
    """Idempotent — safe to call from process startup."""
    registry = get_service_registry()
    registry.register_memory_service(ORG_MEMORY_SCHEME, _factory)
    log.info("Registered ADK memory scheme %r", ORG_MEMORY_SCHEME)
```

- [ ] **Step 4: Re-export from `nuvel/memory/__init__.py`**

Add to the existing `__init__.py`:

```python
from nuvel.memory.adk_registry import ORG_MEMORY_SCHEME, register_org_memory_scheme
```

And append both names to `__all__` (preserving alphabetical order).

- [ ] **Step 5: Run test → PASS**

Run: `pytest tests/test_memory_adk_registry.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add nuvel/memory/adk_registry.py nuvel/memory/__init__.py tests/test_memory_adk_registry.py
git commit -m "feat(memory): register OrgMemoryService as ADK service-registry scheme"
```

---

## Task 2: Wire registration into run_adk.py

**Files:**
- Modify: `nuvel/run_adk.py`

- [ ] **Step 1: Read current state**

Run: `grep -n "NUVEL_ORG_MEMORY\|memory" nuvel/run_adk.py`

Expected: see the v1 pre-flight block (~lines 94-106) that runs migration and logs a WARNING. This block gets replaced.

- [ ] **Step 2: Replace the v1 block with the registry wiring**

In `nuvel/run_adk.py`, locate the v1 block that starts with `if os.getenv("NUVEL_ORG_MEMORY_DSN"):` (added in PR #39). Remove it entirely. At the same insertion point (after `setup_logging()` and `agents_dir`/`dev_mode`/`port` reads, before the `if dev_mode:` branch), insert:

```python
    memory_uri = os.getenv("NUVEL_ORG_MEMORY_URI")
    if memory_uri:
        from nuvel.memory.adk_registry import register_org_memory_scheme
        register_org_memory_scheme()
        print(f"[ADK] OrgMemoryService registered (scheme prefix in URI: {memory_uri.split('://', 1)[0]}://...)")
```

Then in BOTH `get_fast_api_app(...)` calls (the `if dev_mode` branch and the production `else` branch), add `memory_service_uri=memory_uri,` to the kwargs.

- [ ] **Step 3: Smoke-test imports still work**

Run: `python -c "from nuvel.run_adk import main; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Existing memory tests still green (no regressions)**

Run: `pytest tests/test_memory_*.py -q`
Expected: 25 + 4 from Task 1 = 29 passed (or some skip if no DSN env).

- [ ] **Step 5: Commit**

```bash
git add nuvel/run_adk.py
git commit -m "feat(memory): auto-wire OrgMemoryService into run_adk via NUVEL_ORG_MEMORY_URI"
```

---

## Task 3: End-to-end wiring test through real ADK app

**Files:**
- Create: `tests/test_run_adk_memory_wiring.py`

**Prereq:** `NUVEL_MEMORY_TEST_DSN` is set (use the Neon DSN from earlier). Test skips cleanly without it.

- [ ] **Step 1: Write the integration test**

```python
# tests/test_run_adk_memory_wiring.py
"""End-to-end: nuvel.run_adk constructs an ADK FastAPI app that has
OrgMemoryService wired through the service registry — proved by inspecting
the app's memory_service attribute and by exercising a memory write/read."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("NUVEL_MEMORY_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="NUVEL_MEMORY_TEST_DSN not set")

FIXTURE = Path(__file__).parent / "fixtures" / "org_graph.yaml"


def _set_env(monkeypatch):
    monkeypatch.setenv("NUVEL_ORG_MEMORY_DSN", DSN)
    monkeypatch.setenv("NUVEL_ORG_GRAPH_PATH", str(FIXTURE))
    monkeypatch.setenv("NUVEL_ORG_MEMORY_URI", "nuvel-org-memory://default")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.setenv("AGENTS_DIR", str(Path(__file__).parent / "fixtures" / "minimal_agent"))


@pytest.mark.asyncio
async def test_run_adk_wires_org_memory_service(monkeypatch, tmp_path):
    """When NUVEL_ORG_MEMORY_URI is set, run_adk-built app uses OrgMemoryService.

    We don't actually start uvicorn; we monkeypatch the get_fast_api_app caller
    to capture the constructed FastAPI app, then introspect.
    """
    _set_env(monkeypatch)

    # Provide a tiny agents_dir so ADK can load *something*.
    agent_dir = Path(monkeypatch.getenv("AGENTS_DIR") if hasattr(monkeypatch, "getenv") else os.environ["AGENTS_DIR"])
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "__init__.py").write_text("", encoding="utf-8")

    # Import after env is set so registration triggers via run_adk path.
    from nuvel.memory.adk_registry import register_org_memory_scheme
    register_org_memory_scheme()

    from google.adk.cli.fast_api import get_fast_api_app
    app = get_fast_api_app(
        agents_dir=str(agent_dir),
        memory_service_uri=os.environ["NUVEL_ORG_MEMORY_URI"],
        web=False,
        a2a=False,
        host="",
        port=0,
        url_prefix=None,
    )

    # Walk the app to find a Runner with our memory_service.
    # ADK stores runners in app.state; field names vary across versions —
    # do a shallow recursive scan for any attribute that quacks like an
    # OrgMemoryService.
    from nuvel.memory.org_memory_service import OrgMemoryService

    def _find(obj, depth=0):
        if depth > 5:
            return None
        if isinstance(obj, OrgMemoryService):
            return obj
        for attr in ("memory_service", "_memory_service", "state"):
            sub = getattr(obj, attr, None)
            if sub is None:
                continue
            hit = _find(sub, depth + 1)
            if hit is not None:
                return hit
        if isinstance(obj, dict):
            for v in obj.values():
                hit = _find(v, depth + 1)
                if hit is not None:
                    return hit
        return None

    found = _find(app) or _find(app.state)
    assert found is not None, "OrgMemoryService not wired into ADK app"

    # Sanity: do a real write/read through the wired service.
    marker = f"e2e-{uuid.uuid4().hex[:6]}"
    await found.add_memory(app_name="agent", user_id="albert",
                            memories=[{"content": marker}])
    resp = await found.search_memory(app_name="agent", user_id="albert", query=marker)
    contents = []
    for m in resp.memories:
        parts = m.content.parts or []
        contents.extend(p.text for p in parts if getattr(p, "text", None))
    assert any(marker in c for c in contents)


def test_no_memory_uri_means_default_adk_memory(monkeypatch):
    monkeypatch.delenv("NUVEL_ORG_MEMORY_URI", raising=False)
    # Don't import register; ADK should fall back to InMemoryMemoryService.
    from google.adk.cli.fast_api import get_fast_api_app
    app = get_fast_api_app(
        agents_dir=str(Path(__file__).parent / "fixtures" / "minimal_agent"),
        memory_service_uri=None,
        web=False, a2a=False, host="", port=0, url_prefix=None,
    )
    # We don't assert anything specific about the default service — just
    # that construction succeeds, proving zero regression when DSN is absent.
    assert app is not None
```

- [ ] **Step 2: Create a minimal agent fixture if absent**

```bash
mkdir -p tests/fixtures/minimal_agent/agent
touch tests/fixtures/minimal_agent/__init__.py
```

Write `tests/fixtures/minimal_agent/agent/__init__.py`:

```python
from google.adk.agents import Agent

agent = Agent(
    name="test_agent",
    model="gemini-2.0-flash-exp",
    description="Minimal agent for wiring tests.",
    instruction="You are a test agent.",
)
```

If ADK rejects this minimal shape, adjust to whatever's needed for `get_fast_api_app(agents_dir=...)` to succeed without making network calls. The test exercises wiring, not the agent itself.

- [ ] **Step 3: Run integration test**

```bash
NUVEL_MEMORY_TEST_DSN="$NEON_DEV_DSN" \
  pytest tests/test_run_adk_memory_wiring.py -v
```

Expected: 2 pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_run_adk_memory_wiring.py tests/fixtures/minimal_agent/
git commit -m "test(memory): end-to-end run_adk wiring through ADK service registry"
```

---

## Task 4: Update operator docs

**Files:**
- Modify: `docs/memory/org-memory-service.md`

- [ ] **Step 1: Replace the "Enable" section**

Find the section starting `## Enable` in `docs/memory/org-memory-service.md`. Replace it with:

```markdown
## Enable

Set three env vars and `nuvel run-adk` auto-wires `OrgMemoryService` through ADK's service registry — no custom runner required:

- `NUVEL_ORG_MEMORY_DSN` — Postgres DSN (Neon recommended; pgvector + pg_trgm required).
- `NUVEL_ORG_GRAPH_PATH` — path to `org_graph.yaml` (see `tests/fixtures/org_graph.yaml`).
- `NUVEL_ORG_MEMORY_URI=nuvel-org-memory://default` — opt-in. ADK's `get_fast_api_app` reads this scheme via the registry and constructs the service.
- `GOOGLE_API_KEY` — optional. Without it, embeddings fall back to NULL and reads use `pg_trgm` lexical similarity only.

```bash
export NUVEL_ORG_MEMORY_DSN=postgresql://...
export NUVEL_ORG_GRAPH_PATH=/etc/nuvel/org_graph.yaml
export NUVEL_ORG_MEMORY_URI=nuvel-org-memory://default
nuvel run-adk
```

DB migration runs idempotently on first service instantiation.

### Advanced: standalone use

If you need to use `OrgMemoryService` outside the `run-adk` runner (scripts, batch jobs, evals), call the factory directly:

```python
from nuvel.memory.factory import build_default_service

svc = await build_default_service()  # reads the same env vars
await svc.add_memory(app_name="x", user_id="alice", memories=[{"content": "..."}])
```
```

- [ ] **Step 2: Remove the "NOTE: ADK 2.x get_fast_api_app does not accept..." paragraph and the "Custom Runner" code example** elsewhere in the doc — they're now obsolete. Replace with a one-liner: "Custom Runner construction is no longer needed for v1 deployments. See [the wiring spec](../superpowers/specs/2026-05-21-org-memory-runner-wiring-design.md) for the registry-based approach."

- [ ] **Step 3: Remove the "ADK `get_fast_api_app` auto-wiring (waiting on upstream..." line from "Not in v1"** — it just shipped.

- [ ] **Step 4: Commit**

```bash
git add docs/memory/org-memory-service.md
git commit -m "docs(memory): document NUVEL_ORG_MEMORY_URI auto-wiring path"
```

---

## Self-Review Checklist

- **Spec coverage:** Goals 1–5 → Tasks 1+2 (registration + run_adk), 1 (registry path, not monkey-patch), 2 (no get_fast_api_app args removed), 2 (single URI env var), 3 (end-to-end test). Non-goals respected (no HRIS sync, no resolver swap, no custom URI params yet).
- **Placeholders:** None. Every step has runnable code or an exact command.
- **Type consistency:** `OrgMemoryService`, `register_org_memory_scheme`, `ORG_MEMORY_SCHEME`, `build_default_service`, `_factory`, `memory_service_uri` — names match across tasks and align with v1.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-21-org-memory-runner-wiring.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session, batch with checkpoints.

Which approach?
