"""Quote-aware shell-command parsing used by the safety classifier.

These helpers unwrap shell launchers, split a chained command line into its
individual segments without shredding quoted operators, and answer structural
questions (redirection? command substitution?) about a single segment. They do
string-level work; ``command_safety`` does the argv-level verdict on top.
"""

from __future__ import annotations

import re

# A single ``bash -c '...'`` / ``sh -c "..."`` / ``zsh -c '...'`` wrapper.
_WRAPPER_RE = re.compile(
    r"""^(?:bash|sh|zsh)\s+-c\s+(['"])(.*)\1\s*$""", re.DOTALL
)
_REDIRECTION_RE = re.compile(r"(>>|>|<)")
_BARE_WORD_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_SKIP_PREFIXES = frozenset({"sudo", "doas", "env"})

# fd-dup (`2>&1`, `>&2`, `2>&-`) duplicates/closes a descriptor — no file is
# opened, so it isn't a redirect worth gating.
_FD_DUP_RE = re.compile(r"\d*[<>]&(?:\d+|-)")
# Redirecting to /dev/null is a discard sink, not a real file write. The target
# must end at a shell boundary so `/dev/null.bak` stays a gated redirect.
_DEVNULL_RE = re.compile(r"(?:\d*|&)>>?\s*/dev/null(?=\s|$)")
# Nested-command constructs: $(...), backticks, and process substitution <()/>()
_CMD_SUBSTITUTION_RE = re.compile(r"\$\(|`|<\(|>\(")


def strip_wrapper(command: str) -> str:
    """Unwrap one ``bash -c '<inner>'`` shell wrapper; else return the command."""
    match = _WRAPPER_RE.match(command.strip())
    return match.group(2).strip() if match else command.strip()


def split_segments(command: str) -> list[str]:
    """Split on top-level ``&&`` / ``||`` / ``|`` / ``;`` / bare ``&`` / newline.

    Quote-aware: operators inside single/double quotes are literal, so a quoted
    interpreter body (``python3 -c "a; b | c"``) stays one segment. A bare ``&``
    (background) splits; an fd-dup/redirect ``&`` next to ``<``/``>``/``&``
    (``2>&1``, ``&>out``) does not.
    """
    parts: list[str] = []
    buf: list[str] = []
    in_single = in_double = False
    i, n = 0, len(command)

    def flush() -> None:
        segment = "".join(buf).strip()
        if segment:
            parts.append(segment)
        buf.clear()

    while i < n:
        ch = command[i]
        if in_single:
            buf.append(ch)
            in_single = ch != "'"
            i += 1
        elif in_double:
            if ch == "\\" and i + 1 < n:
                buf.append(ch + command[i + 1])
                i += 2
            else:
                buf.append(ch)
                in_double = ch != '"'
                i += 1
        elif ch == "\\" and i + 1 < n:
            buf.append(ch + command[i + 1])
            i += 2
        elif ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
        elif ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
        elif ch in ";\n":
            flush()
            i += 1
        elif ch == "&":
            # Use slices (not command[i-1]) so i==0 yields '' and a leading `&`
            # stays a bare-background split rather than a phantom fd-dup.
            prev, nxt = command[i - 1 : i], command[i + 1 : i + 2]
            if nxt == "&":
                flush()
                i += 2
            elif prev not in ("<", ">", "&") and nxt not in ("<", ">", "&"):
                flush()  # bare background &
                i += 1
            else:  # fd-dup / &>file redirect — keep as part of the segment
                buf.append(ch)
                i += 1
        elif ch == "|":
            flush()
            i += 2 if command[i + 1 : i + 2] == "|" else 1
        else:
            buf.append(ch)
            i += 1
    flush()
    return parts


def command_prefix(segment: str) -> str:
    """Binary + first bare-word subcommand — the auto-scope approval prefix."""
    tokens = segment.split()
    i = 0
    while i < len(tokens) and ("=" in tokens[i] or tokens[i] in _SKIP_PREFIXES):
        i += 1
    if i >= len(tokens):
        return segment.strip()
    binary = tokens[i]
    nxt = tokens[i + 1] if i + 1 < len(tokens) else None
    # Only a plain-name binary takes a bare-word subcommand; a path-like binary
    # (``./run.sh``) has positional args, not subcommands.
    if _BARE_WORD_RE.match(binary) and nxt and _BARE_WORD_RE.match(nxt):
        return f"{binary} {nxt}"
    return binary


def has_redirection(segment: str) -> bool:
    """True if the segment redirects to/from a file (fd-dups and the /dev/null
    sink do not count)."""
    cleaned = _DEVNULL_RE.sub("", _FD_DUP_RE.sub("", segment))
    return _REDIRECTION_RE.search(cleaned) is not None


def has_command_substitution(segment: str) -> bool:
    """True if the segment embeds a nested command via $(...), backticks, or <()/>()."""
    return _CMD_SUBSTITUTION_RE.search(segment) is not None


__all__ = [
    "strip_wrapper",
    "split_segments",
    "command_prefix",
    "has_redirection",
    "has_command_substitution",
]
