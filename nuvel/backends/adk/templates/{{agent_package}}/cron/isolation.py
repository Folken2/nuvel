"""Cron-run isolation: scoped secrets, headless approval policy, run markers.

A scheduled cron job runs unattended with no user to approve tool calls, so it
needs a bounded blast radius. This module provides three async-local
``ContextVar`` markers, installed together by :func:`cron_isolation` around a
job's invocation and reset on exit:

1. **cron-run marker** — records that the current async context is a scheduled
   cron run (which ``job_id``). Consulted by the headless-policy
   ``before_tool_callback`` (``plugins/cron_isolation_plugin.py``).
2. **secret scope** — the env-var names the job's manifest declared. When secret
   scoping is enabled (``NUVEL_CRON_SCOPE_SECRETS=1``), only these are visible to
   the run via :func:`active_cron_env`; everything else is masked. This is the
   blast-radius boundary — a routine can't read a secret it didn't declare.
3. **headless flag** — no user is present, so tool approvals follow
   ``NUVEL_CRON_HEADLESS_POLICY`` instead of prompting.

Everything here is opt-in and defaults to the pre-existing (unscoped, full-env)
behavior, so generated agents that don't set these env vars are unaffected.

Uses ``ContextVar`` (not process globals or ``os.environ`` mutation) so that
concurrent web turns and overlapping cron jobs in the same process never see
each other's scope.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

# ── env flags ────────────────────────────────────────────────────────

ENV_SCOPE_SECRETS = "NUVEL_CRON_SCOPE_SECRETS"
ENV_HEADLESS_POLICY = "NUVEL_CRON_HEADLESS_POLICY"
ENV_SHELL_TOOLS = "NUVEL_CRON_SHELL_TOOLS"

_TRUTHY = {"1", "true", "yes", "on"}

DEFAULT_HEADLESS_POLICY = "allow-shell"
_VALID_POLICIES = {"allow-shell", "deny-all", "allow-all"}

# Tool names treated as "shell/bin" — they run inside the isolated cron scope,
# so they're auto-allowed under the default headless policy. Names are matched
# case-insensitively. Override the set with ``NUVEL_CRON_SHELL_TOOLS`` (a
# comma-separated list) to match a generated agent's actual shell tool.
DEFAULT_SHELL_TOOLS = frozenset(
    {
        "shell",
        "bash",
        "sh",
        "run_shell",
        "run_command",
        "execute",
        "exec",
        "terminal",
        "process",
        "bin",
    }
)


def _truthy(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def scope_secrets_enabled() -> bool:
    """True when ``NUVEL_CRON_SCOPE_SECRETS`` opts into secret scoping."""
    return _truthy(ENV_SCOPE_SECRETS, default=False)


def headless_policy() -> str:
    """The active headless tool policy, defaulting to ``allow-shell``.

    Unknown values fall back to the default rather than failing the run.
    """
    raw = (os.environ.get(ENV_HEADLESS_POLICY) or "").strip().lower()
    return raw if raw in _VALID_POLICIES else DEFAULT_HEADLESS_POLICY


def shell_tool_names() -> frozenset[str]:
    raw = os.environ.get(ENV_SHELL_TOOLS)
    if not raw:
        return DEFAULT_SHELL_TOOLS
    names = {n.strip().lower() for n in raw.split(",") if n.strip()}
    return frozenset(names) if names else DEFAULT_SHELL_TOOLS


def is_shell_tool(tool_name: str) -> bool:
    """Whether ``tool_name`` counts as a sandboxed shell/bin tool."""
    return (tool_name or "").strip().lower() in shell_tool_names()


# ── run-context markers ──────────────────────────────────────────────


@dataclass(frozen=True)
class CronRun:
    """Marker for the active scheduled cron run."""

    job_id: str
    secrets: tuple[str, ...] | None = None


_cron_run: contextvars.ContextVar[CronRun | None] = contextvars.ContextVar(
    "nuvel_cron_run", default=None
)
_secret_scope: contextvars.ContextVar[frozenset[str] | None] = (
    contextvars.ContextVar("nuvel_cron_secret_scope", default=None)
)
_headless: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "nuvel_cron_headless", default=False
)


def active_cron_run() -> CronRun | None:
    """The active :class:`CronRun`, or ``None`` on ordinary (non-cron) turns."""
    return _cron_run.get()


def is_headless() -> bool:
    """True when running unattended (a cron run installed the headless flag)."""
    return _headless.get()


def active_secret_scope() -> frozenset[str] | None:
    """The active declared-secret name set, or ``None`` when unscoped."""
    return _secret_scope.get()


# ── secret scoping ───────────────────────────────────────────────────


def resolve_cron_env(
    declared: Iterable[str] | None,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the env mapping a cron job with ``declared`` secrets may see.

    - Scoping disabled (default) **or** ``declared is None`` → the full ``base``
      env (``os.environ`` when omitted), i.e. the existing behavior. A job
      without a declared ``secrets`` list sees everything, for back-compat.
    - Scoping enabled **and** ``declared`` is a list → only those names that are
      actually present in ``base``. An empty list yields an empty dict — the job
      sees no env vars at all.

    Never mutates ``base``.
    """
    src: Mapping[str, str] = os.environ if base is None else base
    if declared is None or not scope_secrets_enabled():
        return dict(src)
    names = [n for n in declared if isinstance(n, str)]
    return {n: src[n] for n in names if n in src}


def active_cron_env(
    base: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    """The scoped env for the active cron run, or ``None`` outside a scoped run.

    Shell/subprocess tools can consult this to inject only the declared secrets
    into a child process instead of leaking the whole environment.
    """
    scope = _secret_scope.get()
    if scope is None:
        return None
    src: Mapping[str, str] = os.environ if base is None else base
    return {n: src[n] for n in scope if n in src}


# ── headless tool policy ─────────────────────────────────────────────


def evaluate_headless_tool(tool_name: str) -> tuple[bool, str]:
    """Decide whether ``tool_name`` may run under the active headless policy.

    Returns ``(allowed, reason)``. ``reason`` is a human-readable deny message
    when ``allowed`` is ``False`` (empty when allowed). Only meaningful during a
    cron run — callers should gate on :func:`active_cron_run` first.
    """
    policy = headless_policy()
    if policy == "allow-all":
        return True, ""
    if policy == "deny-all":
        return False, (
            f"headless cron policy 'deny-all' blocks tool {tool_name!r}"
        )
    # allow-shell (default): shell/bin tools run in the isolated cron scope.
    if is_shell_tool(tool_name):
        return True, ""
    return False, (
        f"headless cron policy 'allow-shell' blocks non-shell tool "
        f"{tool_name!r}: no user is present to approve it"
    )


# ── combined isolation scope ─────────────────────────────────────────


@contextlib.contextmanager
def cron_isolation(
    job_id: str,
    *,
    secrets: Iterable[str] | None = None,
    headless: bool = True,
) -> Iterator[CronRun]:
    """Install the cron-run marker, headless flag, and secret scope for a block.

    All three markers are reset on exit (even on error). The secret scope is only
    installed when scoping is enabled *and* the job declared a ``secrets`` list;
    otherwise it stays unscoped (full env), preserving legacy behavior.
    """
    declared: tuple[str, ...] | None = (
        tuple(secrets) if secrets is not None else None
    )
    run = CronRun(job_id=job_id, secrets=declared)
    run_token = _cron_run.set(run)
    headless_token = _headless.set(bool(headless))
    if scope_secrets_enabled() and declared is not None:
        scope_token = _secret_scope.set(
            frozenset(n for n in declared if isinstance(n, str))
        )
    else:
        scope_token = _secret_scope.set(None)
    try:
        yield run
    finally:
        _secret_scope.reset(scope_token)
        _headless.reset(headless_token)
        _cron_run.reset(run_token)


__all__ = [
    "CronRun",
    "ENV_HEADLESS_POLICY",
    "ENV_SCOPE_SECRETS",
    "ENV_SHELL_TOOLS",
    "active_cron_env",
    "active_cron_run",
    "active_secret_scope",
    "cron_isolation",
    "evaluate_headless_tool",
    "headless_policy",
    "is_headless",
    "is_shell_tool",
    "resolve_cron_env",
    "scope_secrets_enabled",
    "shell_tool_names",
]
