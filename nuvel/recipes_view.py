"""Recipe candidates: a read-only view over what the Skills MCP recipe store writes.

Layout (written by ``nuvel_mcp.recipes.LocalRecipeStore``)::

    {root}/_recipes/signals/{YYYY-MM-DD}.jsonl            nuvel.recipe_signal/2
    {root}/_recipes/recipes/{tenant_key}/{recipe_id}/v{N}.json   nuvel.recipe/3

Recipes are ``nuvel.recipe/3``: the closed structure (action sequence, slots)
and, only when the user opted in, the free text the server already masked
(``narrative`` with ``{?}`` and ``{type}`` placeholders). A recipe file or
signal of any other schema (an old ``/1`` or ``/2``) is skipped and counted,
never shown.

Signals are weekly snapshots whose counters are cumulative, so per
``(tenant, recipe_id)`` only the snapshot with the greatest ``at`` counts (ties:
the later line wins). Counter totals add up only recipes whose latest snapshot
is not ``retired`` (a merge already summed them into the recipe it kept);
retired recipes still count in ``recipes`` and ``statuses``. Unreadable or malformed lines and files are skipped. Tenant values are used for grouping only
and never appear in the result.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

RECIPES_DIRNAME = "_recipes"
_MAX_DEPTH = 3
UNKNOWN_CATEGORY = "unknown"
RECIPE_SCHEMA = "nuvel.recipe/3"
SIGNAL_SCHEMA = "nuvel.recipe_signal/2"
STATUSES = ("draft", "active", "retired")


class CandidateList(list):
    """A plain list of candidates plus the count of skipped old-schema recipe files."""

    skipped_old: int = 0


@dataclass
class RecipeCandidate:
    category: str
    recipes: int
    tenants: int
    uses: int
    successes: int
    corrections: int
    days_used: int
    statuses: dict
    success_rate: float
    latest: dict | None


def _recipe_dirs(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        try:
            rp = p.resolve()
        except OSError:
            return
        if rp not in seen:
            seen.add(rp)
            found.append(p)

    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        if root.name == RECIPES_DIRNAME:
            add(root)
            continue
        base = len(root.parts)
        for dirpath, dirnames, _ in os.walk(root):
            d = Path(dirpath)
            if RECIPES_DIRNAME in dirnames:
                add(d / RECIPES_DIRNAME)
                dirnames.remove(RECIPES_DIRNAME)
            if len(d.parts) - base >= _MAX_DEPTH:
                dirnames[:] = []
    return found


def _parse_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _category(value: object) -> str:
    return value if isinstance(value, str) and value else UNKNOWN_CATEGORY


def _read_signals(rdir: Path):
    sdir = rdir / "signals"
    try:
        files = sorted(sdir.glob("*.jsonl"))
    except OSError:
        return
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                yield rec


def _str_list(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _mask_ratio(masking: object) -> float | None:
    """masked / words, or None when it cannot be computed (words == 0)."""
    if not isinstance(masking, dict):
        return None
    masked, words = _count(masking.get("masked")), _count(masking.get("words"))
    if masked is None or not words:
        return None
    return masked / words


def _view_doc(doc: dict) -> dict | None:
    """Reduce a stored /3 recipe to the display shape; None if malformed.

    ``text`` is optional: a structure-only recipe has no title, narrative or
    notes, and ``has_text`` is False (the view shows no narrative block)."""
    steps = doc.get("steps")
    if not isinstance(steps, list):
        return None
    text = doc.get("text") if isinstance(doc.get("text"), dict) else {}
    notes = text.get("step_notes") if isinstance(text.get("step_notes"), list) else []
    clean: list[dict] = []
    slots: list[str] = []
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            continue
        s_slots = _str_list(st.get("slots"))
        for sl in s_slots:
            if sl not in slots:
                slots.append(sl)
        note = notes[i] if i < len(notes) else ""
        clean.append({
            "action": st["action"] if isinstance(st.get("action"), str) else "",
            "tool_kind": st["tool_kind"] if isinstance(st.get("tool_kind"), str) else "",
            "slots": s_slots,
            "note": note if isinstance(note, str) else "",
        })
    out = {k: v for k, v in doc.items() if k not in ("tenant", "install_id", "text")}
    out["steps"] = clean
    out["actions"] = [st["action"] for st in clean if st["action"]]
    out["slots"] = slots
    out["has_text"] = bool(text)
    for key in ("title", "when_to_use", "narrative"):
        value = text.get(key)
        out[key] = value if isinstance(value, str) else ""
    out["pitfalls"] = _str_list(text.get("pitfalls"))
    out["mask_ratio"] = _mask_ratio(doc.get("masking")) if text else None
    return out


def _latest_recipes(rdir: Path) -> tuple[dict[str, tuple[float, dict]], int]:
    """Newest /3 recipe (by mtime) per category, plus the count of skipped old-schema files."""
    best: dict[str, tuple[float, dict]] = {}
    skipped = 0
    try:
        files = list(rdir.glob("recipes/*/*/v*.json"))
    except OSError:
        return best, skipped
    for f in files:
        try:
            mtime = f.stat().st_mtime
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        if doc.get("schema") != RECIPE_SCHEMA:
            skipped += 1
            continue
        view = _view_doc(doc)
        if view is None:
            continue
        cat = _category(doc.get("category"))
        if cat not in best or mtime > best[cat][0]:
            best[cat] = (mtime, view)
    return best, skipped


def load_recipe_candidates(roots: list[Path]) -> CandidateList:
    """One candidate per category, sorted by ``uses`` descending."""
    # (tenant, recipe_id) -> (at, category, status, uses, successes, corrections, days_used)
    latest_sig: dict[tuple[object, str], tuple[datetime, str, str, int, int, int, int]] = {}
    latest_recipe: dict[str, tuple[float, dict]] = {}
    skipped_old = 0

    for rdir in _recipe_dirs(roots):
        for rec in _read_signals(rdir):
            if rec.get("schema") != SIGNAL_SCHEMA:
                continue
            rid = rec.get("recipe_id")
            at = _parse_at(rec.get("at"))
            uses = _count(rec.get("uses"))
            succ = _count(rec.get("successes"))
            corr = _count(rec.get("corrections"))
            days = _count(rec.get("days_used"))
            status = rec.get("status")
            if (not isinstance(rid, str) or at is None or status not in STATUSES
                    or None in (uses, succ, corr, days)):
                continue
            tenant = rec.get("tenant") if isinstance(rec.get("tenant"), str) else None
            key = (tenant, rid)
            cur = latest_sig.get(key)
            if cur is None or at >= cur[0]:
                latest_sig[key] = (at, _category(rec.get("category")), status, uses, succ, corr, days)
        found, skipped = _latest_recipes(rdir)
        skipped_old += skipped
        for cat, item in found.items():
            if cat not in latest_recipe or item[0] > latest_recipe[cat][0]:
                latest_recipe[cat] = item

    def blank() -> dict:
        return {"recipes": 0, "tenants": set(), "uses": 0, "successes": 0,
                "corrections": 0, "days_used": 0, "statuses": {}}

    agg: dict[str, dict] = {}
    for (tenant, _rid), (_at, cat, status, uses, succ, corr, days) in latest_sig.items():
        a = agg.setdefault(cat, blank())
        a["recipes"] += 1
        a["tenants"].add(tenant)
        a["statuses"][status] = a["statuses"].get(status, 0) + 1
        # A weekly merge sums a retired duplicate's counters into the recipe it
        # keeps, so a retired snapshot's counters would be counted twice.
        if status == "retired":
            continue
        a["uses"] += uses
        a["successes"] += succ
        a["corrections"] += corr
        a["days_used"] += days
    for cat in latest_recipe:
        agg.setdefault(cat, blank())

    out = CandidateList(
        RecipeCandidate(
            category=cat,
            recipes=a["recipes"],
            tenants=len(a["tenants"]),
            uses=a["uses"],
            successes=a["successes"],
            corrections=a["corrections"],
            days_used=a["days_used"],
            statuses=a["statuses"],
            success_rate=(a["successes"] / a["uses"]) if a["uses"] else 0.0,
            latest=latest_recipe[cat][1] if cat in latest_recipe else None,
        )
        for cat, a in agg.items()
    )
    out.sort(key=lambda c: (-c.uses, c.category))
    out.skipped_old = skipped_old
    return out
