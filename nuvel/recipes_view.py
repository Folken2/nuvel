"""Recipe candidates: a read-only view over what the Skills MCP recipe store writes.

Layout (written by ``nuvel_mcp.recipes.LocalRecipeStore``)::

    {root}/_recipes/signals/{YYYY-MM-DD}.jsonl            nuvel.recipe_signal/1
    {root}/_recipes/recipes/{tenant_key}/{recipe_id}/v{N}.json   nuvel.recipe/1

Signal counters are cumulative, so per ``(tenant, recipe_id)`` only the signal
with the greatest ``at`` counts (ties: the later line wins). Unreadable or
malformed lines and files are skipped. Tenant values are used for grouping only
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


@dataclass
class RecipeCandidate:
    category: str
    recipes: int
    tenants: int
    uses: int
    successes: int
    corrections: int
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


def _latest_recipes(rdir: Path) -> dict[str, tuple[float, dict]]:
    """Newest recipe JSON (by mtime) per category."""
    best: dict[str, tuple[float, dict]] = {}
    try:
        files = list(rdir.glob("recipes/*/*/v*.json"))
    except OSError:
        return best
    for f in files:
        try:
            mtime = f.stat().st_mtime
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        cat = _category(doc.get("category"))
        if cat not in best or mtime > best[cat][0]:
            best[cat] = (mtime, {k: v for k, v in doc.items() if k != "tenant"})
    return best


def load_recipe_candidates(roots: list[Path]) -> list[RecipeCandidate]:
    """One candidate per category, sorted by ``uses`` descending."""
    # (tenant, recipe_id) -> (at, category, uses, successes, corrections)
    latest_sig: dict[tuple[object, str], tuple[datetime, str, int, int, int]] = {}
    latest_recipe: dict[str, tuple[float, dict]] = {}

    for rdir in _recipe_dirs(roots):
        for rec in _read_signals(rdir):
            rid = rec.get("recipe_id")
            at = _parse_at(rec.get("at"))
            uses = _count(rec.get("uses"))
            succ = _count(rec.get("successes"))
            corr = _count(rec.get("corrections"))
            if not isinstance(rid, str) or at is None or None in (uses, succ, corr):
                continue
            tenant = rec.get("tenant") if isinstance(rec.get("tenant"), str) else None
            key = (tenant, rid)
            cur = latest_sig.get(key)
            if cur is None or at >= cur[0]:
                latest_sig[key] = (at, _category(rec.get("category")), uses, succ, corr)
        for cat, item in _latest_recipes(rdir).items():
            if cat not in latest_recipe or item[0] > latest_recipe[cat][0]:
                latest_recipe[cat] = item

    agg: dict[str, dict] = {}
    for (tenant, _rid), (_at, cat, uses, succ, corr) in latest_sig.items():
        a = agg.setdefault(
            cat, {"recipes": 0, "tenants": set(), "uses": 0, "successes": 0, "corrections": 0}
        )
        a["recipes"] += 1
        a["tenants"].add(tenant)
        a["uses"] += uses
        a["successes"] += succ
        a["corrections"] += corr
    for cat in latest_recipe:
        agg.setdefault(
            cat, {"recipes": 0, "tenants": set(), "uses": 0, "successes": 0, "corrections": 0}
        )

    out = [
        RecipeCandidate(
            category=cat,
            recipes=a["recipes"],
            tenants=len(a["tenants"]),
            uses=a["uses"],
            successes=a["successes"],
            corrections=a["corrections"],
            success_rate=(a["successes"] / a["uses"]) if a["uses"] else 0.0,
            latest=latest_recipe[cat][1] if cat in latest_recipe else None,
        )
        for cat, a in agg.items()
    ]
    out.sort(key=lambda c: (-c.uses, c.category))
    return out
