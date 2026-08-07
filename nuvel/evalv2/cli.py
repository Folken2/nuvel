"""nuvel evalv2 — CLI for the skill-driven evaluation system.

Four subcommands, registered onto the top-level ``nuvel`` parser alongside
the legacy ``eval`` tree (production trace scoring — a different job):

    nuvel evalv2 init <skill>         # stamp a starter eval/ suite into a skill
    nuvel evalv2 list                 # skills that ship an eval/ suite
    nuvel evalv2 run <skill>          # run a skill's suite, save the result
    nuvel evalv2 compare <skill>      # diff the latest run against baseline

The primary consumer is an AI agent, so ``run`` and ``compare`` both take a
``--json`` flag that emits the structured wire object on stdout. ``compare``
exits 2 on regression, mirroring v1's drift command for CI.

litellm is only reached through the default ``LLMExecutor`` inside ``run`` —
tests inject a fake executor and never touch the network.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .compare import ComparisonReport, compare_results
from .exceptions import EvalError
from .init import init_eval_suite
from .runner import EvalRunConfig, EvalRunner, LLMExecutor
from .schema import EvalSuiteResult
from .suite import EvalSuite

# Default skills directory — the bundled ADK skills, same root agent.py uses.
_DEFAULT_SKILLS_DIR = Path(__file__).resolve().parent.parent / "backends" / "adk" / "skills"


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
def get_results_dir() -> Path:
    """Return the evalv2 results directory (honors ``XDG_DATA_HOME``)."""
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "nuvel" / "evalv2"


def _skills_root(args: argparse.Namespace) -> Path:
    override = getattr(args, "skills_dir", None)
    if override:
        return Path(override).expanduser()
    from nuvel.config import get_skills_dir

    return get_skills_dir(_DEFAULT_SKILLS_DIR)


def _skill_result_files(results_dir: Path, skill: str) -> list[Path]:
    """Saved run files for a skill, newest last, excluding ``baseline.json``."""
    skill_dir = results_dir / skill
    if not skill_dir.is_dir():
        return []
    runs = [
        p
        for p in skill_dir.glob("*.json")
        if p.is_file() and p.name != "baseline.json"
    ]
    return sorted(runs, key=lambda p: p.name)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def find_eval_skills(skills_dir: Path) -> list[dict]:
    """Return one record per skill under ``skills_dir`` that ships an eval/ suite."""
    if not skills_dir.is_dir():
        return []
    found: list[dict] = []
    for entry in sorted(skills_dir.iterdir()):
        if not (entry / "eval" / "suite.yaml").is_file():
            continue
        try:
            suite = EvalSuite.from_skill(entry)
        except EvalError as exc:
            found.append({"skill": entry.name, "error": str(exc)})
            continue
        found.append(
            {
                "skill": entry.name,
                "suite": suite.name,
                "examples": len(suite.examples),
                "evaluators": _evaluator_kinds(suite),
            }
        )
    return found


def _evaluator_kinds(suite: EvalSuite) -> list[str]:
    kinds: list[str] = []
    for entry in suite.evaluators:
        if isinstance(entry, dict):
            kinds.extend(str(k) for k in entry.keys())
    return kinds


# --------------------------------------------------------------------------- #
# Core operations (testable seams — the CLI commands are thin wrappers)
# --------------------------------------------------------------------------- #
def run_eval(
    skill: str,
    *,
    skills_dir: Path,
    model: str | None = None,
    json_out: bool = False,
    save_baseline: bool = False,
    results_dir: Path | None = None,
    executor: Callable | None = None,
    judge_fn: Callable | None = None,
    stream=None,
) -> int:
    """Load a skill's suite, run it, print a report, and persist the result."""
    stream = stream or sys.stdout
    skill_dir = Path(skills_dir) / skill
    if not (skill_dir / "eval" / "suite.yaml").is_file():
        print(
            f"Error: skill '{skill}' has no eval/suite.yaml under {skills_dir}",
            file=sys.stderr,
        )
        return 1

    try:
        suite = EvalSuite.from_skill(skill_dir)
    except EvalError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    config = EvalRunConfig(
        model=model,
        executor=executor or LLMExecutor(model),
        judge_fn=judge_fn,
        save_baseline=save_baseline,
    )
    result = EvalRunner(suite, config).run()

    _persist_result(result, skill, results_dir or get_results_dir(), save_baseline)

    if json_out:
        print(result.to_json(), file=stream)
    else:
        print(_render_run(result), file=stream)
    return 0


def _persist_result(
    result: EvalSuiteResult, skill: str, results_dir: Path, save_baseline: bool
) -> Path:
    skill_dir = results_dir / skill
    skill_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f") + "Z"
    payload = result.to_json()
    run_path = skill_dir / f"{stamp}.json"
    run_path.write_text(payload, encoding="utf-8")
    if save_baseline:
        (skill_dir / "baseline.json").write_text(payload, encoding="utf-8")
    return run_path


def _resolve_skill_dir(skill: str, skills_dir: Path) -> Path:
    """Resolve a skill argument to a directory.

    A ``skill`` that is itself an existing directory (a path) is used as-is;
    otherwise it is treated as a name under ``skills_dir``.
    """
    candidate = Path(skill).expanduser()
    if candidate.is_dir():
        return candidate
    return Path(skills_dir) / skill


def init_eval(
    skill: str,
    *,
    skills_dir: Path,
    name: str | None = None,
    description: str = "",
    force: bool = False,
    stream=None,
) -> int:
    """Initialize an ``eval/`` suite in a skill directory."""
    stream = stream or sys.stdout
    skill_dir = _resolve_skill_dir(skill, skills_dir)
    if not skill_dir.is_dir():
        print(
            f"Error: skill directory not found: {skill_dir}",
            file=sys.stderr,
        )
        return 1

    try:
        eval_dir = init_eval_suite(
            skill_dir, name=name, description=description, force=force
        )
    except FileExistsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Initialized eval suite in {skill_dir}", file=stream)
    print(f"  {eval_dir / 'suite.yaml'}", file=stream)
    print(f"  {eval_dir / 'examples'}/", file=stream)
    print(
        f"\nAdd examples under {eval_dir / 'examples'}/, then run "
        f"`nuvel evalv2 run {skill_dir.name}`.",
        file=stream,
    )
    return 0


def compare_eval(
    skill: str,
    *,
    results_dir: Path | None = None,
    threshold: float | None = None,
    json_out: bool = False,
    stream=None,
) -> int:
    """Diff a skill's latest run against its saved baseline.

    Returns 2 on regression (CI-friendly), 1 on a usage error, 0 otherwise.
    """
    stream = stream or sys.stdout
    results_dir = results_dir or get_results_dir()
    skill_dir = results_dir / skill

    baseline_path = skill_dir / "baseline.json"
    if not baseline_path.is_file():
        print(
            f"Error: no baseline for '{skill}'. Run "
            f"`nuvel evalv2 run {skill} --save-baseline` first.",
            file=sys.stderr,
        )
        return 1

    runs = _skill_result_files(results_dir, skill)
    if not runs:
        print(f"Error: no runs recorded for '{skill}' to compare.", file=sys.stderr)
        return 1

    baseline = _load_result(baseline_path)
    current = _load_result(runs[-1])
    if baseline is None or current is None:
        return 1

    kwargs = {} if threshold is None else {"regression_threshold": threshold}
    report = compare_results(current, baseline, **kwargs)

    if json_out:
        print(json.dumps(report.to_dict(), indent=2), file=stream)
    else:
        print(_render_compare(report), file=stream)
    return 2 if report.regressed else 0


def _load_result(path: Path) -> EvalSuiteResult | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return EvalSuiteResult.from_dict(data)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: failed to load result {path}: {exc}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _fmt_score(value: float | None) -> str:
    return "  —  " if value is None else f"{value:.3f}"


def _render_run(result: EvalSuiteResult) -> str:
    s = result.summary
    lines = [
        f"suite: {result.suite}  (skill: {result.skill})",
        f"model: {result.model or 'default'}",
        "",
        f"{'id':<28}{'score':>8}  verdict",
        f"{'-' * 28}{'-' * 8}  {'-' * 8}",
    ]
    for ex in result.examples:
        verdict = "unscored" if ex.passed is None else ("pass" if ex.passed else "fail")
        tag = " (cached)" if ex.cache_hit else ""
        lines.append(f"{ex.id[:28]:<28}{_fmt_score(ex.score):>8}  {verdict}{tag}")
    lines += [
        "",
        f"overall: {_fmt_score(s.overall)}   "
        f"pass={s.passed} warn={s.warn} fail={s.failed} unscored={s.unscored} "
        f"(total {s.total})",
    ]
    return "\n".join(lines)


def _render_compare(report: ComparisonReport) -> str:
    sm = report.summary
    lines = [
        f"baseline: {report.baseline_id}",
        f"current:  {report.current_id}",
        "",
        f"{'id':<28}{'base':>8}{'curr':>8}{'delta':>9}  verdict",
        f"{'-' * 28}{'-' * 8}{'-' * 8}{'-' * 9}  {'-' * 8}",
    ]
    for ex in report.examples:
        delta = ex["delta"]
        delta_str = "   —   " if delta is None else f"{delta:+.3f}"
        lines.append(
            f"{ex['id'][:28]:<28}"
            f"{_fmt_score(ex['baseline_score']):>8}"
            f"{_fmt_score(ex['current_score']):>8}"
            f"{delta_str:>9}  {ex['verdict']}"
        )
    overall = sm.get("overall_delta")
    overall_str = "—" if overall is None else f"{overall:+.3f}"
    lines += [
        "",
        f"overall delta: {overall_str}   "
        f"wins={sm.get('wins', 0)} losses={sm.get('losses', 0)} ties={sm.get('ties', 0)}",
    ]
    for warning in sm.get("warnings", []):
        lines.append(f"warning: {warning}")
    lines.append("REGRESSION" if report.regressed else "no regression")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# argparse command handlers
# --------------------------------------------------------------------------- #
def _cmd_list(args: argparse.Namespace) -> int:
    skills_dir = _skills_root(args)
    skills = find_eval_skills(skills_dir)
    if not skills:
        print("No skills with eval suites found.")
        return 0
    width = max(len(s["skill"]) for s in skills)
    for s in skills:
        if "error" in s:
            print(f"  {s['skill']:<{width}}  ! {s['error']}")
            continue
        evals = ",".join(s["evaluators"]) or "-"
        print(
            f"  {s['skill']:<{width}}  {s['suite']}  "
            f"({s['examples']} examples; {evals})"
        )
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    return init_eval(
        args.skill,
        skills_dir=_skills_root(args),
        name=args.name,
        description=args.description,
        force=args.force,
    )


def _cmd_run(args: argparse.Namespace) -> int:
    return run_eval(
        args.skill,
        skills_dir=_skills_root(args),
        model=args.model,
        json_out=args.json,
        save_baseline=args.save_baseline,
    )


def _cmd_compare(args: argparse.Namespace) -> int:
    return compare_eval(
        args.skill,
        threshold=args.threshold,
        json_out=args.json,
    )


def _add_skills_dir_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--skills-dir",
        default=None,
        help="Skills directory to resolve <skill> against (default: configured).",
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``evalv2`` subcommand tree onto the top-level parser."""
    p = subparsers.add_parser(
        "evalv2",
        help="Skill-driven evaluation (v2): list, run, compare.",
    )
    sub = p.add_subparsers(dest="evalv2_command", required=True)

    p_init = sub.add_parser("init", help="Initialize an eval/ suite in a skill.")
    p_init.add_argument("skill", help="Skill name (under the skills dir) or a path.")
    _add_skills_dir_flag(p_init)
    p_init.add_argument("--name", default=None, help="Suite name (default: <skill>-eval).")
    p_init.add_argument("--description", default="", help="One-line suite description.")
    p_init.add_argument(
        "--force", action="store_true", help="Overwrite an existing eval/ directory."
    )
    p_init.set_defaults(func=_cmd_init)

    p_list = sub.add_parser("list", help="List skills that ship an eval/ suite.")
    _add_skills_dir_flag(p_list)
    p_list.set_defaults(func=_cmd_list)

    p_run = sub.add_parser("run", help="Run a skill's eval suite.")
    p_run.add_argument("skill", help="Skill name (directory under the skills dir).")
    _add_skills_dir_flag(p_run)
    p_run.add_argument("--model", default=None, help="Model override for the run.")
    p_run.add_argument(
        "--json", action="store_true", help="Emit the result as JSON on stdout."
    )
    p_run.add_argument(
        "--save-baseline",
        action="store_true",
        help="Also write this run to baseline.json for later comparison.",
    )
    p_run.set_defaults(func=_cmd_run)

    p_compare = sub.add_parser(
        "compare", help="Compare a skill's latest run against its baseline."
    )
    p_compare.add_argument("skill", help="Skill name (directory under the skills dir).")
    _add_skills_dir_flag(p_compare)
    p_compare.add_argument(
        "--json", action="store_true", help="Emit the comparison as JSON on stdout."
    )
    p_compare.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Regression threshold override (overall delta; default -0.05).",
    )
    p_compare.set_defaults(func=_cmd_compare)
