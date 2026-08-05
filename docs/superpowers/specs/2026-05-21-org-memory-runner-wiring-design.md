# OrgMemoryService Runner Wiring — Design

**Status:** Approved (brainstorm) — pending implementation plan
**Date:** 2026-05-21
**Owner:** @Folken2
**Depends on:** [[2026-05-15-org-memory-service-v1-design]] (OrgMemoryService v1, PR #39)

## Context

OrgMemoryService v1 shipped as a fully-tested library but is **not auto-wired** into the default `nuvel run-adk` runner. ADK 2.x's convenience function `google.adk.cli.fast_api.get_fast_api_app` accepts only `memory_service_uri: Optional[str]` — no kwarg for a constructed `BaseMemoryService` instance. So today, using OrgMemoryService in a deployed agent requires the operator to write a custom Runner (~30 lines of glue) outside of nuvel.

This is the second-biggest friction point in OrgMemoryService adoption (the first being keeping `org_graph.yaml` in sync with HR — deferred to a separate spec when real users surface). Without wiring, the v1 PR creates a "yes-but-no" situation: the library works, the docs explain it, but no production agent actually has memory yet.

While inspecting ADK internals for this wiring, we discovered ADK ships an **official extension seam** we'd missed: `google.adk.cli.service_registry`. Custom memory services can be registered against a URI scheme, after which the standard `get_fast_api_app(memory_service_uri="myscheme://...")` path constructs them natively. No monkey-patching, no custom runner, no feature regression — this is the ADK-blessed path. This spec adopts it.

## Goals

1. `nuvel run-adk` auto-wires `OrgMemoryService` when configured, with zero new operator concepts beyond a URI string.
2. Implementation uses ADK's `service_registry.register_memory_service(scheme, factory)` — no monkey-patching, no shadowing of internal functions.
3. Preserve all `get_fast_api_app` features (eval UI, streaming, A2A, hot-reload). No feature regression.
4. Operator config converges on one URI: `NUVEL_ORG_MEMORY_URI=nuvel-org-memory://default` (sentinel; service config still reads from `NUVEL_ORG_MEMORY_DSN` + `NUVEL_ORG_GRAPH_PATH` for v1).
5. End-to-end integration test exercises the actual ADK app — proves a request to `/run` returns memory hits.

## Non-Goals (v1 of wiring)

- HRIS sync for `org_graph.yaml` (separate spec).
- A new resolver implementation (ConfigScopeResolver stays).
- Replacing `org_graph.yaml` with a richer URI param schema. v1 keeps DSN + graph path on env vars; URI is just the sentinel that activates the scheme.
- Multi-org auto-routing per request (org_id is still per-deployment).
- Removing the v1 `factory.build_default_service()` API — it stays for standalone/script use cases (scoring, evals, ad-hoc jobs).

## Approach Considered

| # | Approach | Verdict |
|---|---|---|
| A | Monkey-patch `create_memory_service_from_options` before calling `get_fast_api_app` | Rejected — fragile, silent regression on ADK rename, no precedent in nuvel codebase |
| B | Post-process the FastAPI app and swap `runner.memory_service` on each Runner | Rejected — requires reaching into ADK app state, brittle across ADK minor versions |
| C | Write a full custom runner that bypasses `get_fast_api_app` | Rejected — reimplements eval UI, streaming, A2A, hot-reload; high maintenance cost |
| D | Register a factory in ADK's `service_registry` for a `nuvel-org-memory://` scheme | **Chosen** — ADK-blessed extension API, zero internals, survives ADK version bumps |

## Architecture

```
nuvel/memory/
  ... (existing v1) ...
  adk_registry.py           # NEW — register_org_memory_scheme() + factory
                            #       reads NUVEL_ORG_MEMORY_DSN + NUVEL_ORG_GRAPH_PATH from env

nuvel/run_adk.py            # MODIFIED — calls register_org_memory_scheme() at startup,
                            #            then sets memory_service_uri=NUVEL_ORG_MEMORY_URI
                            #            in get_fast_api_app(...)
```

Module boundaries:

- **`nuvel.memory.adk_registry`** knows ADK's `service_registry`. Knows nothing about Postgres or org graphs (delegates to `factory.build_default_service`).
- **`nuvel.memory.factory`** (existing) — still the standalone construction API. Re-used by the ADK factory.
- **`nuvel/run_adk.py`** — wires the registration call. Doesn't import anything from `nuvel.memory.*` beyond `register_org_memory_scheme`.

## URI scheme

`nuvel-org-memory://default`

- **Scheme:** `nuvel-org-memory`. Long enough to be unambiguous; reads naturally in env files.
- **Authority/path:** `default` — sentinel. v1 reads all config from env vars, so the URI carries no information. Future versions may accept `nuvel-org-memory://acme?graph=/etc/nuvel/acme.yaml` for multi-org deployments.
- **Factory signature:** ADK expects `factory(uri: str, **kwargs) -> BaseMemoryService` (synchronous). Our factory:
  - Calls `asyncio.run(build_default_service())` to migrate the DB and construct the service (one-time, at app startup, before the event loop is hot).
  - Returns the `OrgMemoryService` instance.
  - Raises `RuntimeError` with a clear message if `NUVEL_ORG_MEMORY_DSN` or `NUVEL_ORG_GRAPH_PATH` is unset.

Note on the sync wrapper: ADK calls the factory once at app construction. Running `asyncio.run` there is safe because the FastAPI event loop hasn't started yet. The pool created here ties to that loop and would be discarded; v1's `_pool.py` already has loop-aware reset logic so the runtime pool is fresh.

## Registration

```python
# nuvel/memory/adk_registry.py (sketch — full impl in plan)

from google.adk.cli.service_registry import get_service_registry
from nuvel.memory.factory import build_default_service

ORG_MEMORY_SCHEME = "nuvel-org-memory"


def _factory(uri: str, **kwargs):
    import asyncio
    return asyncio.run(build_default_service())


def register_org_memory_scheme() -> None:
    """Idempotent — safe to call multiple times."""
    registry = get_service_registry()
    registry.register_memory_service(ORG_MEMORY_SCHEME, _factory)
```

## run_adk wiring

```python
# nuvel/run_adk.py — at the top of main(), after setup_logging()

memory_uri = os.getenv("NUVEL_ORG_MEMORY_URI")
if memory_uri:
    from nuvel.memory.adk_registry import register_org_memory_scheme
    register_org_memory_scheme()
    # Now memory_uri is passed straight to get_fast_api_app via memory_service_uri arg

# ... existing branches:
app = get_fast_api_app(
    ...,
    memory_service_uri=memory_uri,  # None when disabled; ADK falls back to InMemoryMemoryService
    ...
)
```

Drop the v1 "DB migration on startup" pre-flight block — the factory now runs migration on first instantiation. Drop the v1 "WARNING: not auto-wired" log entirely.

## Backward Compatibility

- v1 `factory.build_default_service()` stays — internal/script callers continue to use it directly.
- v1 env vars (`NUVEL_ORG_MEMORY_DSN`, `NUVEL_ORG_GRAPH_PATH`) keep their meaning.
- `run_adk.py` behavior **change**: setting `NUVEL_ORG_MEMORY_DSN` alone no longer triggers anything (it was a "pre-flight" log before). Operators must now also set `NUVEL_ORG_MEMORY_URI=nuvel-org-memory://default` to opt in. This is intentional — explicit URI > magic env presence.
- The v1 internal doc (`docs/memory/org-memory-service.md`) gets a top-of-file update: the "Custom Runner needed" section is replaced with "Set `NUVEL_ORG_MEMORY_URI` and you're done."

## Testing

- **Unit (`tests/test_memory_adk_registry.py`):**
  - `register_org_memory_scheme()` is idempotent.
  - After registration, `get_service_registry()._memory_factories` contains `nuvel-org-memory`.
  - `_factory("nuvel-org-memory://default")` returns an `OrgMemoryService` (uses `NullEmbedder` since no `GOOGLE_API_KEY` in test env).
  - Missing env vars raise `RuntimeError` with a message that names the missing var.
- **Integration (`tests/test_run_adk_memory_wiring.py`):**
  - With `NUVEL_ORG_MEMORY_URI` + DSN set: import `run_adk.main`, monkeypatch `uvicorn.run` to capture the app, send a synthetic `/run` request through TestClient, assert the response references a memory write that an earlier `/run` call performed.
  - Without `NUVEL_ORG_MEMORY_URI`: same flow but agent has the ADK default (`InMemoryMemoryService`). No regression.
- **Skipped without `NUVEL_MEMORY_TEST_DSN`** — integration tests use `pytest.skipif`.

## Error Handling

| Condition | Behavior |
|---|---|
| `NUVEL_ORG_MEMORY_URI` set but `NUVEL_ORG_MEMORY_DSN` missing | Factory raises `RuntimeError("NUVEL_ORG_MEMORY_DSN must be set when NUVEL_ORG_MEMORY_URI is set")`. Process exits at startup. |
| `NUVEL_ORG_MEMORY_URI` set but ADK app construction fails (e.g., scheme not registered) | Bubble up; `run_adk.py` already lets startup errors crash the process. |
| `NUVEL_ORG_MEMORY_URI` unset | No registration, no migration, default ADK `InMemoryMemoryService` used. Unchanged behavior from pre-OrgMemoryService nuvel. |
| Factory called with a URI it doesn't recognize | Trust ADK to handle scheme routing; if the scheme matches but the URI is malformed, the factory ignores the URI body in v1 (everything from env). |

## Operator Workflow (after this lands)

```bash
export NUVEL_ORG_MEMORY_DSN=postgresql://...
export NUVEL_ORG_GRAPH_PATH=/etc/nuvel/org_graph.yaml
export NUVEL_ORG_MEMORY_URI=nuvel-org-memory://default
export GOOGLE_API_KEY=...      # optional — without it, lexical fallback only

nuvel run-adk
# → agent now has hierarchical memory, end to end. No custom runner.
```

Three env vars, one CLI command. The custom-runner path documented in v1 stays available for callers who need finer control (e.g., supplying a custom `ScopeResolver` or `Embedder`).

## Open Questions Deferred

- Multi-org deployments — does `nuvel-org-memory://acme?graph=...` become a real URI shape? Probably yes when the second customer lands; spec then.
- Should the registration be lazy (only on first `/run`) instead of at `main()` startup? Probably no — failing fast on misconfig is better than failing on first request.
- Pluggable resolver / embedder via URI params (e.g. `?resolver=okta&embedder=null`) — defer until at least one user wants a non-default combo.

## Related

- `docs/superpowers/specs/2026-05-15-org-memory-service-v1-design.md` — the v1 spec this builds on.
- `docs/memory/org-memory-service.md` — operator-facing usage doc (will be updated by the plan).
- ADK source: `google/adk/cli/service_registry.py` — the extension API we're using.
- ADK source: `google/adk/cli/utils/service_factory.py:create_memory_service_from_options` — where ADK consumes the registry.
