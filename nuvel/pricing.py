"""nuvel pricing — keep pricing.json in sync with OpenRouter.

OpenRouter is treated as the single source of truth: every model in
pricing.json — including direct-provider ids like `anthropic/claude-sonnet-4`
— is priced by what OpenRouter charges for the same route. This is
deliberate: OR mirrors provider list prices, and pinning to one fetcher
means cost numbers in traces stay consistent across agents.

Two file-resolution modes:

  * If the cwd looks like a generated agent (has `<pkg>/plugins/pricing.json`),
    operate on that file. This is what you want when iterating on a single
    agent.
  * Otherwise, operate on the template pricing.json under
    `nuvel/backends/adk/templates/`. Changes there affect every future
    scaffolded agent.

The default `sync` is **refresh existing only** — it updates the prices
of entries already in pricing.json but does not pull the full ~360-model
OpenRouter catalog. Use `nuvel pricing add <model>` to bring a new model
in explicitly. This keeps pricing.json a small, curated list of models
you actually use.

Comment keys (anything starting with `_`) are preserved verbatim.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_HTTP_TIMEOUT = 15.0
_TEMPLATE_PRICING = (
    Path(__file__).resolve().parent
    / "backends" / "adk" / "templates" / "{{agent_package}}" / "plugins" / "pricing.json"
)


# ── Path resolution ──────────────────────────────────────────────────


def _find_agent_pricing(cwd: Path) -> Path | None:
    """Locate a generated agent's pricing.json starting from cwd."""
    for sub in cwd.iterdir() if cwd.is_dir() else []:
        cand = sub / "plugins" / "pricing.json"
        if cand.is_file():
            return cand
    return None


def resolve_pricing_path(target: str | None, cwd: Path | None = None) -> Path:
    """Resolve which pricing.json to operate on.

    target:
      None         → agent in cwd if any, else template
      "template"   → bundled template pricing.json
      "agent"      → require an agent in cwd (error if none)
      <path>       → use as-is (must exist)
    """
    cwd = (cwd or Path.cwd()).resolve()
    if target and target not in ("template", "agent"):
        p = Path(target).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"pricing file not found: {p}")
        return p
    if target == "template":
        return _TEMPLATE_PRICING
    agent = _find_agent_pricing(cwd)
    if target == "agent":
        if agent is None:
            raise FileNotFoundError(
                f"no agent pricing.json found under {cwd} "
                "(expected <pkg>/plugins/pricing.json)"
            )
        return agent
    return agent or _TEMPLATE_PRICING


# ── File IO ──────────────────────────────────────────────────────────


@dataclass
class PricingFile:
    """In-memory representation of a pricing.json.

    `entries` holds real model rows; `comments` holds the `_comment` /
    `_format` keys we want to preserve untouched on write.
    """
    path: Path
    comments: dict[str, str] = field(default_factory=dict)
    entries: dict[str, dict[str, float]] = field(default_factory=dict)


def load(path: Path) -> PricingFile:
    raw = json.loads(path.read_text(encoding="utf-8"))
    pf = PricingFile(path=path)
    for k, v in raw.items():
        if k.startswith("_"):
            pf.comments[k] = v
        else:
            pf.entries[k] = v
    return pf


def save(pf: PricingFile) -> None:
    """Write pricing.json with comments first, then entries in sorted order."""
    ordered: dict = {}
    for k, v in pf.comments.items():
        ordered[k] = v
    for k in sorted(pf.entries):
        ordered[k] = pf.entries[k]
    pf.path.write_text(
        json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── OpenRouter fetcher ───────────────────────────────────────────────


def fetch_openrouter() -> dict[str, dict[str, float]]:
    """Fetch the OpenRouter model catalogue.

    Returns a dict keyed by OR's model id (e.g. `anthropic/claude-opus-4.7`)
    with values `{"input": <usd_per_token>, "output": <usd_per_token>}`.

    OR returns pricing as strings to preserve precision; we cast to float
    once at the boundary to match the existing schema.
    """
    req = urllib.request.Request(
        OPENROUTER_MODELS_URL,
        headers={"User-Agent": "nuvel-pricing-sync/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenRouter request failed: {exc}") from exc

    out: dict[str, dict[str, float]] = {}
    for model in payload.get("data", []):
        model_id = model.get("id")
        pricing = model.get("pricing") or {}
        prompt = pricing.get("prompt")
        completion = pricing.get("completion")
        if not model_id or prompt is None or completion is None:
            continue
        try:
            out[model_id] = {
                "input": float(prompt),
                "output": float(completion),
            }
        except (TypeError, ValueError):
            continue
    return out


# ── Merge logic ──────────────────────────────────────────────────────


@dataclass
class SyncDiff:
    """Per-model outcome of a sync."""
    added: dict[str, dict[str, float]] = field(default_factory=dict)
    updated: dict[str, tuple[dict[str, float], dict[str, float]]] = field(default_factory=dict)
    unchanged: list[str] = field(default_factory=list)
    missing_from_or: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.updated)


def _same(a: dict[str, float], b: dict[str, float]) -> bool:
    return (
        abs(a.get("input", 0) - b.get("input", 0)) < 1e-12
        and abs(a.get("output", 0) - b.get("output", 0)) < 1e-12
    )


def merge_refresh(existing: dict, catalog: dict, *, only_keys: Iterable[str] | None = None) -> SyncDiff:
    """Refresh-existing merge: update only the entries already present.

    A pricing.json key is considered "covered by OR" if OR's catalog has a
    matching id. Matching is exact OR by stripping leading `openrouter/`
    from either side — the same prefix-stripping cost_guard uses.

    `only_keys` restricts the merge to a subset (used by `add <model>`).
    """
    diff = SyncDiff()
    keys = list(only_keys) if only_keys is not None else list(existing.keys())
    for key in keys:
        fresh = _lookup_catalog(catalog, key)
        if fresh is None:
            diff.missing_from_or.append(key)
            continue
        if key in existing and _same(existing[key], fresh):
            diff.unchanged.append(key)
            continue
        if key in existing:
            diff.updated[key] = (existing[key], fresh)
        else:
            diff.added[key] = fresh
    return diff


def _lookup_catalog(catalog: dict, key: str) -> dict[str, float] | None:
    """Find OR's pricing for a pricing.json key, tolerating `openrouter/` prefix."""
    if key in catalog:
        return catalog[key]
    if key.startswith("openrouter/"):
        stripped = key[len("openrouter/"):]
        if stripped in catalog:
            return catalog[stripped]
    # Try adding the prefix in case pricing.json drops it.
    prefixed = f"openrouter/{key}"
    if prefixed in catalog:
        return catalog[prefixed]
    return None


def apply_diff(pf: PricingFile, diff: SyncDiff) -> None:
    """Mutate pricing entries in-place per the diff."""
    for k, v in diff.added.items():
        pf.entries[k] = v
    for k, (_old, new) in diff.updated.items():
        pf.entries[k] = new


# ── CLI commands ─────────────────────────────────────────────────────


def _fmt_price(v: float) -> str:
    return f"${v * 1_000_000:.4f}/Mtok"


def _cmd_list(args: argparse.Namespace) -> int:
    path = resolve_pricing_path(args.target)
    if not path.is_file():
        print(f"pricing.json not found: {path}", file=sys.stderr)
        return 1
    pf = load(path)
    print(f"file: {path}")
    print(f"models: {len(pf.entries)}")
    if not pf.entries:
        return 0
    width = max(len(k) for k in pf.entries)
    for k in sorted(pf.entries):
        v = pf.entries[k]
        print(f"  {k:<{width}}  in {_fmt_price(v['input'])}  out {_fmt_price(v['output'])}")
    return 0


def _print_diff(diff: SyncDiff, *, dry_run: bool) -> None:
    if diff.added:
        print(f"+ added ({len(diff.added)}):")
        for k, v in sorted(diff.added.items()):
            print(f"    {k}  in {_fmt_price(v['input'])}  out {_fmt_price(v['output'])}")
    if diff.updated:
        print(f"~ updated ({len(diff.updated)}):")
        for k, (old, new) in sorted(diff.updated.items()):
            print(f"    {k}")
            print(f"      input  {_fmt_price(old['input'])} → {_fmt_price(new['input'])}")
            print(f"      output {_fmt_price(old['output'])} → {_fmt_price(new['output'])}")
    if diff.missing_from_or:
        print(f"? not on OpenRouter ({len(diff.missing_from_or)}) — kept as-is:")
        for k in diff.missing_from_or:
            print(f"    {k}")
    if diff.unchanged:
        print(f"= unchanged: {len(diff.unchanged)}")
    if not diff.has_changes:
        print("\nNo changes.")
    elif dry_run:
        print("\n(dry-run — no file written)")


def _cmd_sync(args: argparse.Namespace) -> int:
    path = resolve_pricing_path(args.target)
    if not path.is_file():
        print(f"pricing.json not found: {path}", file=sys.stderr)
        return 1
    pf = load(path)
    print(f"file: {path}")
    print(f"fetching {OPENROUTER_MODELS_URL} …")
    catalog = fetch_openrouter()
    print(f"OpenRouter returned {len(catalog)} models\n")

    diff = merge_refresh(pf.entries, catalog)
    _print_diff(diff, dry_run=args.dry_run)

    if diff.has_changes and not args.dry_run:
        apply_diff(pf, diff)
        save(pf)
        print(f"\nwrote {path}")
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    path = resolve_pricing_path(args.target)
    if not path.is_file():
        print(f"pricing.json not found: {path}", file=sys.stderr)
        return 1
    pf = load(path)
    print(f"file: {path}")
    print(f"fetching {OPENROUTER_MODELS_URL} …")
    catalog = fetch_openrouter()

    fresh = _lookup_catalog(catalog, args.model)
    if fresh is None:
        print(f"error: {args.model!r} not found on OpenRouter", file=sys.stderr)
        return 1

    if args.model in pf.entries and _same(pf.entries[args.model], fresh):
        print(f"unchanged: {args.model}  in {_fmt_price(fresh['input'])}  "
              f"out {_fmt_price(fresh['output'])}")
        return 0

    action = "updated" if args.model in pf.entries else "added"
    pf.entries[args.model] = fresh
    if not args.dry_run:
        save(pf)
    print(f"{action}: {args.model}  in {_fmt_price(fresh['input'])}  "
          f"out {_fmt_price(fresh['output'])}")
    if args.dry_run:
        print("(dry-run — no file written)")
    return 0


# ── Parser wiring ────────────────────────────────────────────────────


def _add_target_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--target", "-t", default=None,
        help="Which pricing.json: 'template', 'agent', or an explicit path. "
             "Default: agent under cwd if present, else template.",
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("pricing", help="Inspect and sync model pricing.json.")
    sub = p.add_subparsers(dest="pricing_command", required=True)

    p_list = sub.add_parser("list", help="Show entries in the resolved pricing.json.")
    _add_target_flag(p_list)
    p_list.set_defaults(func=_cmd_list)

    p_sync = sub.add_parser(
        "sync",
        help="Refresh existing entries from OpenRouter (does not add new models).",
    )
    _add_target_flag(p_sync)
    p_sync.add_argument("--dry-run", action="store_true",
                        help="Print the diff without writing.")
    p_sync.set_defaults(func=_cmd_sync)

    p_add = sub.add_parser("add", help="Add (or refresh) a single model from OpenRouter.")
    _add_target_flag(p_add)
    p_add.add_argument("model", help="OpenRouter model id, e.g. 'anthropic/claude-opus-4.7'.")
    p_add.add_argument("--dry-run", action="store_true",
                       help="Print the result without writing.")
    p_add.set_defaults(func=_cmd_add)
