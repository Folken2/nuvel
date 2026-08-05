"""Structural (argv-level) shell-command safety classifier.

Instead of substring/regex-matching the raw command string — which is easy to
fool with quoting or spacing — this lexes each segment into an argv list with
``shlex`` and inspects the *tokens*. It returns a coarse verdict:

* ``("deny", reason)`` — catastrophic, block everywhere (e.g. ``rm -rf /``).
* ``("ask", reason)`` — risky, prompt for confirmation (e.g. ``git push -f``).
* ``None`` — no opinion; the normal permission flow decides.

It unwraps env-var prefixes, ``sudo``/``doas``/``env`` and transparent launchers
(``timeout``, ``nice``, ``xargs`` …), and recurses into a ``bash -c '<inner>'``
so the real binary — not the wrapper — is what gets judged.
"""

from __future__ import annotations

import os.path
import re
import shlex

from .command_classify import split_segments, strip_wrapper

_CONTROL_OPS = frozenset({"|", "||", "&&", ";", "&"})
_PIPE_OPS = frozenset({"|", "||"})

# Transparent launchers: they run the rest of the line as a command but grant no
# privilege, so they are unwrapped without marking the command as escalated.
_LAUNCHERS = frozenset(
    {"command", "nice", "nohup", "stdbuf", "setsid", "ionice", "xargs", "timeout"}
)
# A shell ``-c`` flag, possibly clustered with other short flags (``-lc``).
_SHELL_C_FLAG = re.compile(r"^-[a-z]*c[a-z]*$")
# Launcher flags whose VALUE is the next separate token — skip both so the value
# is not mistaken for the wrapped command (``timeout -s KILL 5 rm -rf /``).
_LAUNCHER_VALUE_FLAGS = {
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-P", "-u", "--class", "--classdata", "--pid"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "xargs": {
        "-I", "-i", "-d", "--delimiter", "-E", "-e", "--eof", "-n",
        "--max-args", "-L", "-l", "--max-lines", "-P", "--max-procs",
        "-s", "--max-chars", "--replace",
    },
}

_SYSTEM_ROOTS = frozenset(
    {
        "/", "~", "$HOME", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
        "/boot", "/sys", "/proc", "/dev", "/var", "/root", "/home", "/Users",
    }
)
# Containers whose one-level child is itself an account root: /home/bob wipes a
# whole user, /home/bob/build does not — so these can't join the prefixes below.
_HOME_CONTAINERS = ("/home", "/Users")
_SYSTEM_PREFIXES = (
    "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
    "/boot", "/sys", "/proc", "/dev", "/var", "/root",
)
_FIND_DESTRUCTIVE = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fls", "-fprint", "-fprint0"}
)
_PIPE_INTERPRETERS = frozenset(
    {"sh", "bash", "zsh", "python", "python3", "perl", "ruby", "node", "php"}
)
# Cloud/infra CLIs whose delete/destroy verbs are irreversible. Token-matched
# (not substring) so a quoted SQL ``DELETE`` or a resource named "delete" is safe.
_CLOUD_DELETE: dict[str, frozenset[str]] = {
    "bq": frozenset({"rm"}),
    "gcloud": frozenset({"delete", "rm"}),
    "gsutil": frozenset({"rm"}),
    "kubectl": frozenset({"delete"}),
    "terraform": frozenset({"destroy"}),
    "docker": frozenset({"rm", "rmi", "prune"}),
}


def lex(command: str) -> list[str] | None:
    """shlex tokens (quote- and operator-aware); ``None`` on a parse error."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def _split_tokens(tokens: list[str]) -> list[tuple[list[str], bool]]:
    """Split a token stream on control operators.

    Each segment carries whether the operator *before* it was a pipe — only
    then is the segment a pipe target (``... | sh``). ``&&``/``;``/``&`` are
    sequence operators, not pipes.
    """
    out: list[tuple[list[str], bool]] = []
    current: list[str] = []
    is_pipe_target = False
    for tok in tokens:
        if tok in _CONTROL_OPS:
            if current:
                out.append((current, is_pipe_target))
                current = []
            is_pipe_target = tok in _PIPE_OPS
            continue
        current.append(tok)
    if current:
        out.append((current, is_pipe_target))
    return out


def _segments_with_pipe(command: str) -> list[tuple[list[str], bool]]:
    inner = strip_wrapper(command)
    tokens = lex(inner)
    if tokens is None:
        # Conservative fallback: regex-split, shlex each piece, drop unparseable.
        out: list[tuple[list[str], bool]] = []
        for seg in split_segments(inner) or [inner]:
            try:
                out.append((shlex.split(seg), False))
            except ValueError:
                continue
        return out
    # A ``bash -c <inner>`` / ``bash -lc <inner>`` that reached here (strip_wrapper
    # only matched the bare ``-c '...'`` form) -> recurse on the inner string.
    if len(tokens) >= 3 and os.path.basename(tokens[0]) in {"bash", "sh", "zsh"}:
        ci = next((j for j, t in enumerate(tokens) if _SHELL_C_FLAG.match(t)), None)
        if ci is not None and ci + 1 < len(tokens):
            return _segments_with_pipe(tokens[ci + 1])
    return _split_tokens(tokens)


def segments(command: str) -> list[list[str]]:
    """Per-segment argv. Unwraps a single ``bash -c '<inner>'`` and recurses."""
    return [argv for argv, _ in _segments_with_pipe(command)]


def _effective(argv: list[str]) -> tuple[list[str], bool]:
    """Strip env-var prefixes and unwrap sudo/doas/env/launchers.

    Returns ``(effective argv, escalated)`` where ``escalated`` is True if a
    privilege wrapper (sudo/doas) was stripped.
    """
    i, escalated = 0, False
    while i < len(argv):
        tok = argv[i]
        # VAR=value prefix (name has no slash) — a temporary env assignment.
        if "=" in tok and not tok.startswith("-") and "/" not in tok.split("=", 1)[0]:
            i += 1
            continue
        base = os.path.basename(tok)
        if base in {"sudo", "doas"}:
            escalated = True
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                i += 1
            continue
        if base == "env":
            i += 1
            continue
        if base in _LAUNCHERS:
            i += 1
            value_flags = _LAUNCHER_VALUE_FLAGS.get(base, set())
            while i < len(argv):
                cur = argv[i]
                if cur.startswith("-"):
                    if "=" in cur:  # --signal=KILL is self-contained
                        i += 1
                    elif cur in value_flags:  # consumes the next token as value
                        i += 2
                    else:  # boolean flag
                        i += 1
                elif cur.lstrip("+-").isdigit():  # numeric positional (timeout 5)
                    i += 1
                else:  # the wrapped command
                    break
            continue
        break
    return argv[i:], escalated


def _is_user_home_root(target: str) -> bool:
    parts = target.split("/")
    return len(parts) == 3 and f"/{parts[1]}" in _HOME_CONTAINERS and bool(parts[2])


def _dangerous_target(target: str) -> bool:
    trimmed = target.rstrip("/*")
    if (
        target == "/*"
        or target in _SYSTEM_ROOTS
        or trimmed in _SYSTEM_ROOTS
        or _is_user_home_root(trimmed)
    ):
        return True
    return any(target == p or target.startswith(p + "/") for p in _SYSTEM_PREFIXES)


def _short_flags(argv: list[str]) -> list[str]:
    return [t[1:] for t in argv[1:] if t.startswith("-") and not t.startswith("--")]


def _positionals(argv: list[str]) -> list[str]:
    return [t for t in argv[1:] if not t.startswith("-")]


def _rm_verdict(argv: list[str]) -> tuple[str, str] | None:
    shorts = _short_flags(argv)
    has_r = "--recursive" in argv or any("r" in f.lower() for f in shorts)
    has_f = "--force" in argv or any("f" in f.lower() for f in shorts)
    if not (has_r and has_f):
        return None
    targets = _positionals(argv)
    if any(_dangerous_target(t) for t in targets):
        return ("deny", "recursive force-delete of a system/home root")
    if any(t in {".", "./", "..", "../", "*"} for t in targets):
        return ("ask", "recursive force-delete of the working directory or a glob")
    return None


def _git_verdict(argv: list[str]) -> tuple[str, str] | None:
    sub = next((t for t in argv[1:] if not t.startswith("-")), None)
    if sub is None:
        return None
    flags = [t for t in argv[1:] if t.startswith("-")]
    if sub == "push":
        if any(f in {"-f", "--force"} or f.startswith("--force-with-lease") for f in flags):
            return ("ask", "git force-push rewrites remote history")
        if any(f in {"--delete", "-d", "--mirror"} for f in flags):
            return ("ask", "git push deletes or mirrors a remote ref")
        if any(p.startswith("+") for p in _positionals(argv)):
            return ("ask", "git force-push via +refspec rewrites remote history")
        return None
    if sub == "reset" and "--hard" in flags:
        return ("ask", "git reset --hard discards changes")
    if sub == "clean":
        shorts = [f[1:] for f in flags if not f.startswith("--")]
        if "--force" in flags or any("f" in s for s in shorts):
            return ("ask", "git clean deletes untracked files")
        return None
    if sub in {"filter-branch", "filter-repo"}:
        return ("ask", "git history rewrite")
    return None


def _segment_verdict(argv: list[str], *, is_pipe_target: bool) -> tuple[str, str] | None:
    if not argv:
        return None
    eff, escalated = _effective(argv)
    if not eff:
        return None
    binary = os.path.basename(eff[0])

    if is_pipe_target and binary in _PIPE_INTERPRETERS:
        return ("ask", "piping into an interpreter")
    if binary == "rm":
        verdict = _rm_verdict(eff)
        if verdict:
            return verdict
    if binary == "find" and any(t in _FIND_DESTRUCTIVE for t in eff[1:]):
        return ("ask", "find with a side-effecting action")
    if binary == "git":
        verdict = _git_verdict(eff)
        if verdict:
            return verdict
    if binary in {"chmod", "chown"}:
        recursive = "--recursive" in eff[1:] or any(
            "r" in f.lower() for f in _short_flags(eff)
        )
        if recursive and any(_dangerous_target(t) for t in _positionals(eff)):
            return ("ask", f"recursive {binary} on a system/home root")
    if binary == "mv":
        if any(t == "/dev/null" or t.startswith("/dev/null/") for t in _positionals(eff)):
            return ("ask", "mv into /dev/null destroys data")
    delete_verbs = _CLOUD_DELETE.get(binary)
    if delete_verbs and any(t in delete_verbs for t in _positionals(eff)):
        return ("ask", f"{binary} destructive delete")
    if binary in {"sudo", "su"}:
        return ("ask", f"privilege escalation via {binary}")
    if escalated:
        return ("ask", "privilege escalation")
    return None


def classify(command: str) -> tuple[str, str] | None:
    """Strongest verdict across all segments.

    A command that cannot be parsed is treated conservatively as ``ask``.
    """
    if lex(strip_wrapper(command)) is None and not segments(command):
        return ("ask", "command could not be parsed")
    segs = _segments_with_pipe(command)
    if not segs:
        return ("ask", "command could not be parsed")
    verdict: tuple[str, str] | None = None
    for argv, is_pipe_target in segs:
        current = _segment_verdict(argv, is_pipe_target=is_pipe_target)
        if current is None:
            continue
        if current[0] == "deny":
            return current
        verdict = current
    return verdict


__all__ = ["classify", "lex", "segments"]
