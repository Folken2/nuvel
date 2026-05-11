"""
Deck structure / reordering helpers.

Heuristic tools the LLM uses when looking at the whole deck rather than
a single slide. Both take the deck outline as a JSON string (the same
shape ``get_deck_outline`` returns from session state) so the agent can
feed in either the live outline or a hypothetical one.

    analyze_deck_flow   structural observations (repeats, breaks, gaps)
    suggest_reordering  ordered list of move suggestions, each with reason

Both tools are deliberately conservative — they surface evidence, not
prescriptions. The agent decides whether a change earns its cost.
"""

from __future__ import annotations

import json
import re

from google.adk.tools import FunctionTool


# Keyword sets used to spot canonical slide roles.
_AGENDA_TOKENS = ("agenda", "outline", "what we'll cover", "today's session", "in this deck")
_CTA_TOKENS = ("call to action", "cta", "the ask", "next steps", "what we need", "our ask")
_THANKS_TOKENS = ("thank you", "thanks", "q&a", "questions?", "questions")
_METHOD_TOKENS = ("method", "methodology", "approach", "how we", "process")
_RESULTS_TOKENS = ("results", "findings", "outcomes", "what we found", "data")
_PROBLEM_TOKENS = ("problem", "challenge", "pain", "the gap")
_SOLUTION_TOKENS = ("solution", "our approach", "how we solve", "the fix")


def _parse_outline(deck_outline_json: str) -> tuple[list[dict] | None, str | None]:
    """Decode the outline payload. Tolerates already-parsed objects."""
    if isinstance(deck_outline_json, list):
        return deck_outline_json, None
    if isinstance(deck_outline_json, dict):
        return deck_outline_json.get("slides") or [], None
    if not isinstance(deck_outline_json, str) or not deck_outline_json.strip():
        return None, "Empty outline."
    try:
        data = json.loads(deck_outline_json)
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"
    if isinstance(data, dict):
        slides = data.get("slides")
    elif isinstance(data, list):
        slides = data
    else:
        return None, "Expected an outline dict or list of slides."
    if not isinstance(slides, list):
        return None, "Outline missing a list of slides."
    return slides, None


def _norm(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def _matches_any(title: str, tokens: tuple[str, ...]) -> bool:
    t = _norm(title)
    return any(tok in t for tok in tokens)


def analyze_deck_flow(deck_outline_json: str) -> dict:
    """Return structural observations about the whole deck.

    Looks for run-of-the-same-title blocks, title-style breaks (e.g. one
    question mark in a deck of statements), section imbalances, missing
    agenda for long decks, and missing CTA / closing. Output is a flat
    list of ``observations`` — the agent picks which to act on.

    Args:
        deck_outline_json: JSON string from ``get_deck_outline``, or the
            equivalent dict / list. Slides need ``index`` and ``title``;
            ``bullet_count`` and ``has_notes`` are read if present.

    Returns:
        ``{"status": "ok", "slide_count": N, "observations":
        [{"kind": str, "indices": [int, ...], "message": str}, ...]}``
        or ``{"status": "error", "message": str}``.
    """
    slides, err = _parse_outline(deck_outline_json)
    if err is not None:
        return {"status": "error", "message": err}
    if not slides:
        return {"status": "empty", "message": "No slides to analyse."}

    obs: list[dict] = []
    titles = [s.get("title") or "" for s in slides]
    norm_titles = [_norm(t) for t in titles]
    count = len(slides)

    # 1. Long stretches of similar-titled slides.
    i = 0
    while i < count:
        j = i + 1
        while j < count and norm_titles[j] == norm_titles[i] and norm_titles[i]:
            j += 1
        run = j - i
        if run >= 3:
            obs.append({
                "kind": "repeated_titles",
                "indices": list(range(i, j)),
                "message": (
                    f"{run} slides in a row titled \"{titles[i]}\" "
                    "— consider merging or numbering."
                ),
            })
        i = j

    # 2. Title-style breaks: question marks among statements (or vice versa).
    question_idx = [i for i, t in enumerate(titles) if t.strip().endswith("?")]
    if 0 < len(question_idx) < max(2, count // 4):
        obs.append({
            "kind": "punctuation_break",
            "indices": question_idx,
            "message": (
                f"Slide(s) {question_idx} end in a question mark while the rest "
                "use statements. Pick one pattern."
            ),
        })

    # 3. Title-length outliers.
    word_counts = [len(re.findall(r"\b\w+\b", t)) for t in titles]
    if word_counts:
        avg = sum(word_counts) / len(word_counts)
        outliers = [
            i for i, w in enumerate(word_counts)
            if w > 0 and (w > avg * 2.2 + 2 or (avg >= 4 and w == 1))
        ]
        if outliers and len(outliers) <= max(1, count // 3):
            obs.append({
                "kind": "title_length_outlier",
                "indices": outliers,
                "message": (
                    f"Title length on slide(s) {outliers} drifts from the rest of "
                    "the deck — tighten or expand for consistency."
                ),
            })

    # 4. Missing agenda for decks > 8 slides.
    has_agenda = any(_matches_any(t, _AGENDA_TOKENS) for t in titles[: min(3, count)])
    if count > 8 and not has_agenda:
        obs.append({
            "kind": "missing_agenda",
            "indices": [0],
            "message": (
                f"Deck has {count} slides but no agenda in the first three. "
                "Add one between the title and the body."
            ),
        })

    # 5. Missing CTA / closing.
    tail = titles[-3:] if count >= 3 else titles
    has_cta = any(_matches_any(t, _CTA_TOKENS) for t in tail)
    is_thanks_only = bool(tail) and _matches_any(tail[-1], _THANKS_TOKENS)
    if not has_cta and is_thanks_only:
        obs.append({
            "kind": "missing_cta",
            "indices": [count - 1],
            "message": (
                "Deck closes on a 'Thanks / Questions' slide with no explicit ask. "
                "Add a CTA before the thank-you."
            ),
        })
    elif not has_cta and not is_thanks_only and count >= 5:
        obs.append({
            "kind": "missing_cta",
            "indices": [count - 1],
            "message": "No explicit CTA / next-steps slide near the end.",
        })

    # 6. Uneven bullet load — slides with way more bullets than the rest.
    bullet_counts = [int(s.get("bullet_count") or 0) for s in slides]
    if bullet_counts:
        avg_b = sum(bullet_counts) / len(bullet_counts)
        heavy = [
            i for i, b in enumerate(bullet_counts)
            if b > 0 and b >= 7 and b > avg_b * 2
        ]
        if heavy:
            obs.append({
                "kind": "bullet_overload",
                "indices": heavy,
                "message": (
                    f"Slide(s) {heavy} carry far more bullets than the rest "
                    "— split or cut."
                ),
            })

    # 7. Section-size imbalance: a single >50% block in a deck of >6.
    if count > 6:
        # Group by first word of the title — crude but useful.
        groups: dict[str, list[int]] = {}
        for i, t in enumerate(norm_titles):
            head = (t.split() or [""])[0]
            if head:
                groups.setdefault(head, []).append(i)
        for head, idxs in groups.items():
            if len(idxs) >= max(4, count // 2):
                obs.append({
                    "kind": "section_imbalance",
                    "indices": idxs,
                    "message": (
                        f"{len(idxs)} of {count} slides cluster under \"{head}\" "
                        "— consider splitting that section across two arcs."
                    ),
                })

    return {
        "status": "ok",
        "slide_count": count,
        "observations": obs,
    }


def suggest_reordering(deck_outline_json: str) -> dict:
    """Return a list of move suggestions for the deck, with reasons.

    Heuristics applied (in order):
      - Agenda should sit at index 1 (right after the title slide).
      - Methodology should precede results in reports.
      - Problem before solution before evidence before ask in pitches.
      - CTA / next-steps should be the last (or second-to-last) slide.
      - 'Thanks / Q&A' slides should not appear before the CTA.

    Args:
        deck_outline_json: JSON string from ``get_deck_outline`` (or the
            equivalent dict / list).

    Returns:
        ``{"status": "ok", "moves": [{"from_index": int, "to_index": int,
        "reason": str}, ...]}``. ``moves`` may be empty — that's the
        "deck already flows well" answer.
    """
    slides, err = _parse_outline(deck_outline_json)
    if err is not None:
        return {"status": "error", "message": err}
    if not slides:
        return {"status": "empty", "message": "No slides to reorder."}

    titles = [s.get("title") or "" for s in slides]
    count = len(slides)
    moves: list[dict] = []
    # Track planned post-move positions to avoid stacking conflicts.
    planned_to: set[int] = set()

    def _add(from_i: int, to_i: int, reason: str) -> None:
        if from_i == to_i:
            return
        if to_i in planned_to:
            return
        moves.append({"from_index": from_i, "to_index": to_i, "reason": reason})
        planned_to.add(to_i)

    # 1. Agenda → index 1 (right after the title).
    agenda_idx = next(
        (i for i, t in enumerate(titles) if _matches_any(t, _AGENDA_TOKENS)),
        None,
    )
    if agenda_idx is not None and count > 3 and agenda_idx > 2:
        _add(agenda_idx, 1, "Agenda belongs right after the title.")

    # 2. Methodology before results (last methodology before first results).
    method_idxs = [i for i, t in enumerate(titles) if _matches_any(t, _METHOD_TOKENS)]
    result_idxs = [i for i, t in enumerate(titles) if _matches_any(t, _RESULTS_TOKENS)]
    if method_idxs and result_idxs:
        first_result = min(result_idxs)
        last_method = max(method_idxs)
        if last_method > first_result:
            _add(
                last_method,
                max(0, first_result),
                "Methodology should precede results in reports.",
            )

    # 3. Problem before solution.
    problem_idxs = [i for i, t in enumerate(titles) if _matches_any(t, _PROBLEM_TOKENS)]
    solution_idxs = [i for i, t in enumerate(titles) if _matches_any(t, _SOLUTION_TOKENS)]
    if problem_idxs and solution_idxs:
        first_solution = min(solution_idxs)
        last_problem = max(problem_idxs)
        if last_problem > first_solution:
            _add(
                last_problem,
                max(0, first_solution),
                "State the problem before introducing the solution.",
            )

    # 4. CTA → last slide (or second-to-last if a Thanks slide closes).
    cta_idx = next(
        (i for i, t in enumerate(titles) if _matches_any(t, _CTA_TOKENS)),
        None,
    )
    thanks_idx = next(
        (i for i in range(count - 1, -1, -1) if _matches_any(titles[i], _THANKS_TOKENS)),
        None,
    )
    if cta_idx is not None:
        target = count - 1
        if thanks_idx is not None and thanks_idx == count - 1:
            target = count - 1  # CTA goes last; the Thanks slide moves out
            if cta_idx != target:
                _add(cta_idx, target, "The ask should be the closing slide.")
                # Bump Thanks to before-CTA so the deck doesn't end on chit-chat.
                if thanks_idx not in planned_to and thanks_idx != target - 1:
                    _add(thanks_idx, max(0, target - 1), "Thanks belongs before the ask, if anywhere.")
        elif cta_idx != target:
            _add(cta_idx, target, "The ask should be the closing slide.")

    # 5. Thanks slide appearing before the CTA when CTA exists.
    if cta_idx is not None and thanks_idx is not None and thanks_idx < cta_idx:
        _add(
            thanks_idx,
            count - 1,
            "Don't say thanks before the ask — move it after the CTA or drop it.",
        )

    return {
        "status": "ok",
        "slide_count": count,
        "moves": moves,
    }


structure_tool_list = [
    FunctionTool(analyze_deck_flow),
    FunctionTool(suggest_reordering),
]
