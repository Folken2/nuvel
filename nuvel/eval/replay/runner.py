"""Replay orchestration.

``_call_litellm_chat`` is the litellm boundary (system+user → output). It is
NOT the judge's adapter: replay generates prose, so it sends no
``response_format`` hint and therefore needs no empty-content fallback (that
fallback exists only because the judge requests JSON). ``replay_run`` does one
trace; ``ReplayRunner`` (Task 4) is the batch driver.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from nuvel.eval.replay.schema import ReplayResult, Variant, append_replay, load_replay_index
from nuvel.eval.rubric import DEFAULT_RUBRIC, Rubric
from nuvel.eval.scorer import JudgeFn, score_run
from nuvel.traces_cli import Run, _iter_trace_files, _parse_file_runs

logger = logging.getLogger(__name__)

# (model, system, user, *, temperature, max_tokens) -> (output_text, cost_usd)
ChatFn = Callable[..., Awaitable[tuple[str, float]]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _call_litellm_chat(
    model: str,
    system_prompt: str,
    user_input: str,
    *,
    temperature: float,
    max_tokens: int,
) -> tuple[str, float]:
    """Thin litellm adapter for replay. Returns (output_text, cost_usd)."""
    import litellm

    from nuvel.eval.judge import _extract_cost, _quiet_litellm_once

    _quiet_litellm_once()
    response = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = (response.choices[0].message.content or "").strip()
    return content, _extract_cost(response, model)


def build_synthetic_run(src: Run, output_text: str) -> Run:
    """Shape a ``Run`` the scorer can judge from a single replayed output.

    CRITICAL: ``apply_heuristics`` early-exits with ``skip_judge=True`` if the
    run has no ``run_end`` event (incomplete trace) or ``completion_tokens == 0``
    (no assistant output). Both must be satisfied or the judge never runs and
    quality silently stays 0.0. Hence the explicit ``run_end`` event and
    ``completion_tokens=1``.
    """
    return Run(
        agent=src.agent,
        file=src.file,
        session_id=src.session_id,
        trace_id=src.trace_id,
        user_input=src.user_input,
        completion_tokens=1,
        schema="adk",
        events=[
            {"event": "run_start", "user_input": src.user_input or ""},
            {"event": "llm_response", "response_text": output_text},
            {"event": "run_end"},
        ],
    )


async def replay_run(
    src: Run,
    variant: Variant,
    *,
    rubric: Rubric | None = None,
    _call: ChatFn = _call_litellm_chat,
    judge_fn: JudgeFn | None = None,
) -> ReplayResult:
    """Replay one trace under ``variant`` and score the output.

    Assumes ``src.user_input`` is non-empty (caller filters). Retries the chat
    call once on exception (mirrors the judge's retry); re-raises on the second
    failure so the batch driver can record ``replay.error`` and skip the trace.
    """
    model = variant.resolved_model()
    user_input = src.user_input or ""

    last_exc: Exception | None = None
    output_text = ""
    # Not accumulated across retries — a failed call is billed nothing; a
    # successful call overwrites. (Differs from judge._call_litellm's `+=`.)
    replay_cost = 0.0
    for attempt in (1, 2):
        try:
            output_text, replay_cost = await _call(
                model, variant.system_prompt, user_input,
                temperature=variant.temperature, max_tokens=variant.max_tokens,
            )
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001 — litellm surface is wide
            last_exc = exc
            logger.warning("replay chat failed (attempt %d): %s", attempt, exc)
    if last_exc is not None:
        raise last_exc

    synthetic = build_synthetic_run(src, output_text)
    scored = await score_run(synthetic, rubric=rubric or DEFAULT_RUBRIC, judge_fn=judge_fn)

    return ReplayResult(
        trace_id=src.trace_id or src.session_id or "",
        agent=src.agent,
        variant_name=variant.name,
        variant_version=variant.version,
        replayed_at=_now_iso(),
        model=model,
        output_text=output_text,
        replay_cost_usd=replay_cost,
        scored=asdict(scored),
    )


@dataclass
class ReplayReport:
    """Summary of one ``ReplayRunner.run()``."""

    replayed: int = 0
    skipped_existing: int = 0
    skipped_no_input: int = 0
    replay_errors: int = 0
    total_cost_usd: float = 0.0
    budget_exhausted: bool = False


@dataclass
class ReplayRunner:
    """Batch driver: replay a variant across one agent's traces dir.

    Mirrors ``ScoreSession`` — idempotency on ``(trace_id, variant.version)``,
    a shared cost budget across BOTH the replay chat call and the judge call,
    and bounded concurrency.
    Budget enforcement is best-effort: up to ``concurrency`` in-flight replays
    may complete after the budget threshold is crossed (mirrors ``ScoreSession``;
    matches the spec's 'in-flight replays complete').
    """

    variant: Variant
    traces_dir: Path
    agent: str
    since: "datetime | None" = None
    max_cost_usd: float = 1.0
    concurrency: int = 5
    force: bool = False
    dry_run: bool = False
    rubric: Rubric | None = None
    chat_fn: ChatFn = field(default=_call_litellm_chat)
    judge_fn: JudgeFn | None = None

    def _replay_path(self) -> Path:
        return self.traces_dir / "replays" / f"{self.variant.name}.jsonl"

    def _collect_runs(self) -> list[Run]:
        seen_sessions: set[str] = set()
        runs: list[Run] = []
        for f in _iter_trace_files([self.traces_dir]):
            for r in _parse_file_runs(f, keep_events=True):
                if self.since is not None and r.started_at:
                    try:
                        ts = datetime.fromisoformat(r.started_at.replace("Z", "+00:00"))
                        if ts < self.since:
                            continue
                    except ValueError:
                        pass
                # No per-file agent filter here: `traces_dir` is a single
                # agent's dir by construction (the CLI derives it from the
                # variant's location), so every run already belongs to
                # `self.agent`. The field is retained for caller/reporting
                # context — it is intentionally not a filter. (A path-derived
                # `_agent_label_for` check would also misbehave outside the
                # generated-agents/ layout, e.g. under $TRACE_DIR.)
                # Deduplicate: _parse_file_runs may produce a shadow Run for
                # run_end events whose trace_id is absent (keyed by session_id
                # alone), in addition to the real Run keyed by trace_id.
                # Track by session_id — whichever Run arrives first (with
                # user_input from run_start) wins; subsequent duplicates skip.
                sid = r.session_id
                if sid and sid in seen_sessions:
                    continue
                if sid:
                    seen_sessions.add(sid)
                runs.append(r)
        return runs

    async def run(self) -> ReplayReport:
        report = ReplayReport()
        runs = self._collect_runs()
        if not runs:
            return report

        replay_path = self._replay_path()
        existing = load_replay_index(replay_path)

        budget_lock = asyncio.Lock()
        budget = {"spent": 0.0, "exhausted": False}
        write_lock = asyncio.Lock()
        sem = asyncio.Semaphore(self.concurrency)

        async def _one(r: Run) -> None:
            tid = r.trace_id or r.session_id or ""
            if not (r.user_input or "").strip():
                report.skipped_no_input += 1
                return
            if not self.force:
                prior = existing.get(tid)
                if prior is not None and prior.variant_version == self.variant.version:
                    report.skipped_existing += 1
                    return
            async with budget_lock:
                if budget["exhausted"]:
                    return
            async with sem:
                try:
                    result = await replay_run(
                        r, self.variant,
                        rubric=self.rubric,
                        _call=self.chat_fn,
                        judge_fn=self.judge_fn,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("replay failed for %s: %s", tid, exc)
                    report.replay_errors += 1
                    return

            judge_cost = float((result.scored.get("judge") or {}).get("cost_usd") or 0.0)
            call_cost = result.replay_cost_usd + judge_cost

            if not self.dry_run:
                replay_path.parent.mkdir(parents=True, exist_ok=True)
                async with write_lock:
                    append_replay(replay_path, result)
            # Safe without a lock: no await separates these report mutations,
            # so the single-threaded event loop cannot interleave them.
            report.replayed += 1
            report.total_cost_usd += call_cost

            async with budget_lock:
                budget["spent"] += call_cost
                if budget["spent"] >= self.max_cost_usd:
                    budget["exhausted"] = True

        await asyncio.gather(*(asyncio.create_task(_one(r)) for r in runs))
        report.budget_exhausted = budget["exhausted"]
        return report
