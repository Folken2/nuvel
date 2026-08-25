"""Feedback storage and health computation for Nuvel Skills MCP server.

Stdlib-only — no asyncio, no third-party deps. Writes structured feedback as
JSON files under ``{feedback_dir}/{skill_name}/`` and computes health
signals from that history.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

OUTCOME_VALUES = {"success", "partial", "failure", "blocked"}
SEVERITY_VALUES = {"blocking", "misleading", "minor"}


def _feedback_dir(feedback_dir: Path, skill_name: str) -> Path:
    """Return the feedback directory for a skill, without creating it."""
    return feedback_dir / skill_name


def _compute_dedup_key(skill_name: str, section: str, what_didnt: str) -> str:
    """SHA-256 of ``normalized(skill_name|section|what_didnt)`` → first 16 hex chars.

    Normalisation: lowercase, strip whitespace, collapse multiple spaces to one.
    Used as a stable prefix for the feedback-id; not yet used for dedup
    (Phase 2).
    """
    raw = f"{skill_name}|{section}|{what_didnt}"
    # Normalise: lowercase, collapse whitespace
    parts = raw.lower().split()
    normalised = " ".join(parts)
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    return digest[:16]


def write_feedback(feedback_dir: Path, data: dict) -> dict:
    """Write a feedback entry to disk.

    *data* must contain at least: ``skill_name``, ``skill_version``, ``outcome``,
    ``severity``, ``what_didnt``.  Optional fields: ``section``, ``what_worked``,
    ``proposed_patch``, ``harness``, ``user_corrected``, ``attribution``,
    ``correlation_id``.

    Returns ``{"status": "recorded", "feedback_id": "<id>", "message": "..."}``
    on success, or ``{"status": "error", ...}`` on failure.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    skill_name = (data.get("skill_name") or "").strip()

    if not skill_name:
        return {"status": "error", "message": "Missing required field: skill_name"}

    what_didnt = (data.get("what_didnt") or "").strip()
    if not what_didnt:
        return {"status": "error", "message": "Missing required field: what_didnt"}

    outcome = (data.get("outcome") or "").strip().lower()
    if outcome not in OUTCOME_VALUES:
        return {
            "status": "error",
            "message": f"Invalid outcome '{outcome}'. Must be one of: {', '.join(sorted(OUTCOME_VALUES))}",
        }

    severity = (data.get("severity") or "").strip().lower()
    if severity not in SEVERITY_VALUES:
        return {
            "status": "error",
            "message": f"Invalid severity '{severity}'. Must be one of: {', '.join(sorted(SEVERITY_VALUES))}",
        }

    section = (data.get("section") or "").strip()
    dedup_prefix = _compute_dedup_key(skill_name, section, what_didnt)
    # Avoid collisions by appending a microsecond timestamp suffix.
    micro = datetime.now(timezone.utc).strftime("%H%M%S%f")
    feedback_id = f"{today}-{dedup_prefix}-{micro}"

    record = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "skill_name": skill_name,
        "skill_version": (data.get("skill_version") or "unknown").strip(),
        "outcome": outcome,
        "severity": severity,
        "section": section if section else "",
        "what_worked": (data.get("what_worked") or "").strip(),
        "what_didnt": what_didnt,
        "proposed_patch": (data.get("proposed_patch") or "").strip(),
        "harness": (data.get("harness") or "unknown").strip(),
        "user_corrected": bool(data.get("user_corrected", False)),
        "attribution": (data.get("attribution") or "").strip(),
        "correlation_id": (data.get("correlation_id") or "").strip(),
        "issue_filed": False,  # Phase 2 sets this
    }

    try:
        dest_dir = _feedback_dir(feedback_dir, skill_name)
        dest_dir.mkdir(parents=True, exist_ok=True)
        file_path = dest_dir / f"{feedback_id}.json"
        file_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "message": f"Failed to write feedback: {exc}"}

    return {
        "status": "recorded",
        "feedback_id": feedback_id,
        "message": f"Feedback recorded for skill '{skill_name}'",
    }


def read_feedback(feedback_dir: Path, skill_name: str) -> list[dict]:
    """Return all feedback files for *skill_name*, sorted newest-first.

    Returns ``[]`` when the skill has no feedback (or the feedback directory
    doesn't exist).
    """
    fdir = _feedback_dir(feedback_dir, skill_name)
    if not fdir.is_dir():
        return []
    entries: list[dict] = []
    for fpath in sorted(fdir.glob("*.json"), reverse=True):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            # Store the file stem as feedback_id if not already present.
            if "feedback_id" not in data:
                data["feedback_id"] = fpath.stem
            entries.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return entries


def compute_health(feedback_dir: Path, skill_name: str) -> dict:
    """Compute health signals for a skill from its feedback history.

    Returns a dict with keys: ``total_feedback``, ``recent_30d`` (counts by
    outcome), ``trend``, ``flagged_sections``, ``open_issues``, and
    ``recommendation``.  When there is no feedback the response is minimal:
    ``{"total_feedback": 0, "recommendation": "ok"}``.
    """
    feedback = read_feedback(feedback_dir, skill_name)
    if not feedback:
        return {"total_feedback": 0, "recommendation": "ok"}

    now = datetime.now(timezone.utc)
    thirty_days_ago = now.timestamp() - 30 * 86400
    sixty_days_ago = now.timestamp() - 60 * 86400

    # Bucket by outcome within the last 30 days.
    recent_30d: dict[str, int] = {"success": 0, "partial": 0, "failure": 0, "blocked": 0}
    recent_outcomes: list[str] = []
    prior_outcomes: list[str] = []  # 31-60 days ago (prior period)

    flagged: dict[str, list[dict]] = {}  # section -> list of feedback entries
    open_issues = 0

    for fb in feedback:
        ts_str = fb.get("timestamp", "")
        outcome = fb.get("outcome", "")
        severity = fb.get("severity", "")
        section = fb.get("section", "")

        try:
            ts = datetime.fromisoformat(ts_str).timestamp()
        except (ValueError, OSError):
            ts = 0

        if fb.get("issue_filed"):
            open_issues += 1

        if ts >= thirty_days_ago:
            if outcome in recent_30d:
                recent_30d[outcome] += 1
            if outcome:
                recent_outcomes.append(outcome)
        elif ts >= sixty_days_ago:
            if outcome:
                prior_outcomes.append(outcome)

        # Flagged-sections accumulation.
        sec = section.strip() if section else "unspecified"
        if sec not in flagged:
            flagged[sec] = []
        flagged[sec].append(fb)

    # Trend: compare recent success rate vs prior 30 days.
    trend = _compute_trend(recent_outcomes, prior_outcomes, feedback)

    # Flagged sections: count >= 2, at least one blocking or misleading.
    flagged_sections = _build_flagged_sections(flagged)

    # Recommendation from flagged sections.
    recommendation = _make_recommendation(flagged_sections)

    return {
        "skill_name": skill_name,
        "total_feedback": len(feedback),
        "recent_30d": recent_30d,
        "trend": trend,
        "flagged_sections": flagged_sections,
        "open_issues": open_issues,
        "recommendation": recommendation,
    }


def _compute_trend(
    recent_outcomes: list[str],
    prior_outcomes: list[str],
    all_feedback: list[dict],
) -> str:
    """Determine trend: improving, stable, declining, or insufficient_data."""
    if len(all_feedback) < 5:
        return "insufficient_data"

    def _success_rate(outcomes: list[str]) -> float:
        if not outcomes:
            return 0.0
        success = outcomes.count("success")
        return success / len(outcomes)

    recent_rate = _success_rate(recent_outcomes)
    prior_rate = _success_rate(prior_outcomes)

    # If we have no prior data, check if recent shows improvement.
    if not prior_outcomes:
        return "insufficient_data"

    if recent_rate - prior_rate > 0.10:
        return "improving"
    elif prior_rate - recent_rate > 0.10:
        return "declining"
    else:
        return "stable"


def _build_flagged_sections(flagged: dict[str, list[dict]]) -> list[dict]:
    """Build the flagged_sections list from section-grouped feedback."""
    result: list[dict] = []
    for section, entries in sorted(flagged.items()):
        if len(entries) < 2:
            continue
        severities = {e.get("severity", "") for e in entries}
        if not (severities & {"blocking", "misleading"}):
            continue

        # Most common what_didnt.
        what_didnt_counts: dict[str, int] = {}
        for e in entries:
            wd = (e.get("what_didnt") or "").strip()
            if wd:
                what_didnt_counts[wd] = what_didnt_counts.get(wd, 0) + 1
        summary = ""
        if what_didnt_counts:
            summary = max(what_didnt_counts, key=lambda k: what_didnt_counts[k])

        # Use the highest severity present.
        worst = "minor"
        if "blocking" in severities:
            worst = "blocking"
        elif "misleading" in severities:
            worst = "misleading"

        result.append({
            "section": section,
            "severity": worst,
            "count": len(entries),
            "summary": summary,
        })
    return result


def _make_recommendation(flagged_sections: list[dict]) -> str:
    severities = {fs["severity"] for fs in flagged_sections}
    if "blocking" in severities:
        return "use_cautiously"
    if "misleading" in severities:
        return "review_before_use"
    return "ok"