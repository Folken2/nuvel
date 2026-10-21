"""Shell tool — runs a fixed set of allowlisted CLI shapes for the meta-agent.

Only whole command *shapes* are allowed, not base commands: an allowed
interpreter or package manager (``python -c``, ``npx <pkg>``, ``pip install
<url>``) or a git global option (``-c core.pager=…``, ``--upload-pack=…``)
would hand the model — or prompt-injected content it reads — arbitrary code
execution. Every argument is checked against the shape's option table;
anything unknown is rejected before a process is spawned.

Allowed shapes:

- ``nuvel agent create <name> [--framework X] [--description T]
  [--system-prompt T] [--persona] [--with-*] [--workflow]``
- ``nuvel skills list [--framework X]``
- ``nuvel skills search <query> [--framework X]``
- ``nuvel doctor``
- ``git status|log|diff|show`` with a small read-only option set
- ``--help`` / ``-h`` on any nuvel shape
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess

from google.adk.tools import FunctionTool

from .file_tools import resolve_safe_path

logger = logging.getLogger(__name__)

_OUTPUT_DIR = os.getenv("AGENTS_OUTPUT_DIR", "./generated-agents")

_MAX_TIMEOUT = 120  # seconds
_MAX_OUTPUT_CHARS = 20_000  # per stream
_MAX_ARG_CHARS = 4_000

_FRAMEWORKS = frozenset(
    {"adk", "buzz", "hermes", "claude-agent-sdk", "anthropic-managed-agents"}
)
_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
# Revisions / paths for read-only git commands: no leading "-", no shell or
# control characters. `:` and `^~@{}` cover rev syntax like HEAD~1 or main@{1}.
_GIT_POSITIONAL_RE = re.compile(r"^[A-Za-z0-9._/~^@{}:+-]{1,256}$")
_HELP_FLAGS = frozenset({"--help", "-h"})


class _Reject(ValueError):
    """Raised when a command does not match an allowed shape."""


# ── Option tables ──────────────────────────────────────────────────────
# Each table maps an option to its kind: "flag" (no value), "text" (any value),
# or a frozenset of allowed values. Values may be given as "--opt=value" or as
# the next token; a next-token value may never start with "-".

_FRAMEWORK_OPTS: dict[str, object] = {"--framework": _FRAMEWORKS, "-f": _FRAMEWORKS}

_AGENT_CREATE_OPTS: dict[str, object] = {
    **_FRAMEWORK_OPTS,
    "--description": "text",
    "--system-prompt": "text",
    "--persona": "flag",
    "--with-composio": "flag",
    "--with-slack": "flag",
    "--with-telegram": "flag",
    "--with-teams": "flag",
    "--workflow": "flag",
    "--with-acp": "flag",
    "--with-eval": "flag",
    "--with-litellm": "flag",
}

_GIT_STATUS_OPTS: dict[str, object] = {
    "-s": "flag", "--short": "flag", "-b": "flag", "--branch": "flag",
    "--porcelain": "flag", "-uno": "flag",
}
_GIT_DIFF_OPTS: dict[str, object] = {
    "--stat": "flag", "--shortstat": "flag", "--numstat": "flag",
    "--name-only": "flag", "--name-status": "flag",
    "--cached": "flag", "--staged": "flag",
}
_GIT_LOG_OPTS: dict[str, object] = {
    **_GIT_DIFF_OPTS,
    "--oneline": "flag", "--graph": "flag", "--decorate": "flag",
    "--all": "flag", "--reverse": "flag", "-p": "flag", "--patch": "flag",
    "-n": "count", "--max-count": "count",
}
_GIT_SHOW_OPTS: dict[str, object] = {
    **_GIT_DIFF_OPTS, "--oneline": "flag", "--no-patch": "flag", "-s": "flag",
}
# Prepended to every git invocation: never page, and switch off the config
# keys that make read-only commands run a program (fsmonitor runs on status,
# hooks, bare-repo discovery of a model-written directory). External diff and
# textconv drivers are disabled per subcommand below.
_GIT_PREFIX = [
    "git",
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "safe.bareRepository=explicit",
    "--no-pager",
]
_GIT_SAFETY_ARGS = {
    "log": ["--no-ext-diff", "--no-textconv"],
    "diff": ["--no-ext-diff", "--no-textconv"],
    "show": ["--no-ext-diff", "--no-textconv"],
    "status": [],
}
_GIT_SUBCOMMANDS = {
    "status": _GIT_STATUS_OPTS,
    "log": _GIT_LOG_OPTS,
    "diff": _GIT_DIFF_OPTS,
    "show": _GIT_SHOW_OPTS,
}


def _check_value(opt: str, kind: object, value: str) -> None:
    if kind == "count":
        if not value.isdigit() or not (1 <= int(value) <= 500):
            raise _Reject(f"{opt} needs a count between 1 and 500")
    elif isinstance(kind, frozenset):
        if value not in kind:
            raise _Reject(f"{opt} must be one of: {', '.join(sorted(kind))}")
    elif kind == "text":
        if len(value) > _MAX_ARG_CHARS:
            raise _Reject(f"{opt} value is too long")


def _parse_options(
    args: list[str], table: dict[str, object]
) -> tuple[list[str], list[str]]:
    """Split *args* into (options, positionals), validating each option.

    Raises ``_Reject`` on any option not in *table*, on ``--``, and on a
    value-taking option whose value is missing or starts with ``-``.
    """
    options: list[str] = []
    positionals: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            raise _Reject("'--' is not allowed")
        if not arg.startswith("-"):
            positionals.append(arg)
            i += 1
            continue

        name, has_eq, inline = arg.partition("=")
        kind = table.get(name)
        if kind is None:
            # git-style "-n5" / "-5" counts
            if table.get("-n") == "count" and re.fullmatch(r"-n?\d+", arg):
                _check_value("-n", "count", arg.lstrip("-n"))
                options.append(arg)
                i += 1
                continue
            raise _Reject(f"Option '{name}' is not allowed")

        if kind == "flag":
            if has_eq:
                raise _Reject(f"Option '{name}' takes no value")
            options.append(arg)
            i += 1
            continue

        if has_eq:
            value = inline
            i += 1
        else:
            if i + 1 >= len(args):
                raise _Reject(f"Option '{name}' needs a value")
            value = args[i + 1]
            if value.startswith("-"):
                raise _Reject(
                    f"Value for '{name}' may not start with '-' "
                    f"(use {name}=<value>)"
                )
            i += 2
        _check_value(name, kind, value)
        # Re-emit as "--opt=value" for long options so a value that starts
        # with "-" can't be re-read as an option; short options ("-f", "-n")
        # only take vetted choice/count values, so two tokens are safe.
        if name.startswith("--"):
            options.append(f"{name}={value}")
        else:
            options.extend([name, value])
    return options, positionals


# ── Shape validators ───────────────────────────────────────────────────


def _validate_nuvel(args: list[str]) -> list[str]:
    if args[-1:] and args[-1] in _HELP_FLAGS and not set(args[:-1]) & _HELP_FLAGS:
        # `nuvel [shape…] --help` — only the leading words of a known shape.
        prefix = args[:-1]
        if prefix in ([], ["agent"], ["agent", "create"], ["skills"],
                      ["skills", "list"], ["skills", "search"], ["doctor"]):
            return ["nuvel", *args]
        raise _Reject(f"'nuvel {' '.join(prefix)} --help' is not an allowed shape")

    head = args[:2]
    if head == ["agent", "create"]:
        options, positionals = _parse_options(args[2:], _AGENT_CREATE_OPTS)
        if len(positionals) != 1:
            raise _Reject("nuvel agent create takes exactly one agent name")
        name = positionals[0]
        if not _AGENT_NAME_RE.match(name):
            raise _Reject(f"Agent name must be kebab-case: {name!r}")
        return ["nuvel", "agent", "create", name, *options]

    if head == ["skills", "list"]:
        options, positionals = _parse_options(args[2:], _FRAMEWORK_OPTS)
        if positionals:
            raise _Reject("nuvel skills list takes no positional arguments")
        return ["nuvel", "skills", "list", *options]

    if head == ["skills", "search"]:
        options, positionals = _parse_options(args[2:], _FRAMEWORK_OPTS)
        if len(positionals) != 1:
            raise _Reject("nuvel skills search takes exactly one query")
        if len(positionals[0]) > 200:
            raise _Reject("Search query is too long")
        return ["nuvel", "skills", "search", positionals[0], *options]

    if args == ["doctor"]:
        return ["nuvel", "doctor"]

    raise _Reject(
        f"'nuvel {' '.join(args)}' is not an allowed shape. Allowed: "
        "nuvel agent create, nuvel skills list|search, nuvel doctor"
    )


def _validate_git(args: list[str]) -> list[str]:
    if not args or args[0] not in _GIT_SUBCOMMANDS:
        raise _Reject(
            "Only read-only git subcommands are allowed: status, log, diff, show "
            "(no global options such as -c, -C, --git-dir, --exec-path)"
        )
    sub = args[0]
    options, positionals = _parse_options(args[1:], _GIT_SUBCOMMANDS[sub])
    for pos in positionals:
        # Absolute or home-relative paths make `git diff` fall back to
        # --no-index and read arbitrary files; ".." walks out of the repo.
        if (
            not _GIT_POSITIONAL_RE.match(pos)
            or pos.startswith(("/", "~"))
            or os.path.isabs(pos)
            or ".." in pos.split("/")
        ):
            raise _Reject(f"Argument {pos!r} is not an allowed revision or path")
    return [*_GIT_PREFIX, sub, *_GIT_SAFETY_ARGS[sub], *options, *positionals]


def validate_command(parts: list[str]) -> list[str]:
    """Return the argv to execute for *parts*, or raise ``_Reject``."""
    if not parts:
        raise _Reject("Empty command")
    if any("\x00" in p or "\n" in p for p in parts):
        raise _Reject("Control characters are not allowed")
    base, rest = parts[0], parts[1:]
    if base == "nuvel":
        if not rest:
            raise _Reject("nuvel needs a subcommand")
        return _validate_nuvel(rest)
    if base == "git":
        return _validate_git(rest)
    raise _Reject(
        f"Command '{base}' is not allowed. Allowed: nuvel agent create, "
        "nuvel skills list|search, nuvel doctor, git status|log|diff|show"
    )


# ── Execution ──────────────────────────────────────────────────────────


def _cap(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    dropped = len(text) - _MAX_OUTPUT_CHARS
    return text[:_MAX_OUTPUT_CHARS] + f"\n… [truncated {dropped} chars]"


def _run_cli_impl(command: str, cwd: str = "", base_dir: str | None = None) -> dict:
    if not command or not command.strip():
        return {"status": "error", "message": "Empty command"}

    try:
        parts = shlex.split(command)
    except ValueError as e:
        return {"status": "error", "message": f"Cannot parse command: {e}"}

    try:
        argv = validate_command(parts)
    except _Reject as e:
        return {"status": "error", "message": str(e)}

    base_abs = os.path.abspath(base_dir or _OUTPUT_DIR)
    if argv[0] == "git":
        # Everything under the output dir is model-writable (write_file), so a
        # planted .git/config or bare-repo layout there could run commands.
        # git only ever runs in the server's own working directory.
        if cwd:
            return {"status": "error", "message": "git commands do not accept cwd"}
        here = os.path.abspath(os.getcwd())
        if here == base_abs or here.startswith(base_abs + os.sep):
            return {
                "status": "error",
                "message": "git is disabled when the working directory is inside AGENTS_OUTPUT_DIR",
            }
    run_cwd = None
    if cwd:
        try:
            run_cwd = resolve_safe_path(cwd, base_abs)
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        if not os.path.isdir(run_cwd):
            return {"status": "error", "message": f"cwd does not exist: {cwd}"}

    env = dict(os.environ)
    # Pin the scaffold output dir absolutely so a cwd change can't move it.
    env["AGENTS_OUTPUT_DIR"] = base_abs
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    for key in list(env):
        # Env-injected config (GIT_CONFIG_COUNT/KEY_n/VALUE_n, GIT_CONFIG_PARAMETERS)
        # and repo redirection (GIT_DIR, GIT_WORK_TREE, ...) are dropped.
        if key.startswith("GIT_CONFIG_") and key not in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL"):
            del env[key]
    for key in ("GIT_CONFIG_PARAMETERS", "GIT_DIR", "GIT_WORK_TREE", "GIT_EXEC_PATH",
                "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS"):
        env.pop(key, None)
    env.pop("GIT_EXTERNAL_DIFF", None)

    try:
        result = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=_MAX_TIMEOUT,
            cwd=run_cwd,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        output = _cap(result.stdout)
        if result.stderr:
            output += "\n--- stderr ---\n" + _cap(result.stderr)

        return {
            "status": "success" if result.returncode == 0 else "error",
            "exit_code": result.returncode,
            "output": output,
            "message": f"Command exited with code {result.returncode}",
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": f"Command timed out after {_MAX_TIMEOUT}s"}
    except FileNotFoundError:
        return {"status": "error", "message": f"Command not found: {argv[0]}"}
    except Exception as e:
        logger.exception("Shell command failed")
        return {"status": "error", "message": str(e)}


def run_cli(command: str, cwd: str = "", tool_context=None) -> dict:
    """Run one of a fixed set of nuvel / read-only git commands.

    Allowed shapes (anything else, including python/pip/npm/npx/uv, is rejected):
    - `nuvel agent create <name> [--framework X] [--description '...']
      [--system-prompt '...'] [--persona] [--with-composio] [--with-slack]
      [--with-telegram] [--with-teams] [--workflow] [--with-acp] [--with-eval]
      [--with-litellm]` — agents always land in AGENTS_OUTPUT_DIR
    - `nuvel skills list [--framework X]`, `nuvel skills search <query>`
    - `nuvel doctor`
    - `nuvel agent create --help` (or --help on any shape above)
    - `git status|log|diff|show` with read-only options (--oneline, --stat,
      -n N, --cached, --name-only, ...) — runs in the server's working
      directory only; repo-relative paths, no `cwd`

    A value that starts with '-' must use the `--opt=value` form.

    Args:
        command: The full CLI command string, e.g.
            "nuvel agent create my-agent --description '...' --with-composio".
        cwd: Optional directory to run a nuvel command in, relative to the
            generated-agents output dir (e.g. "my-agent"). Paths escaping it
            are rejected. Not accepted for git commands.
    """
    return _run_cli_impl(command, cwd)


run_cli_tool = FunctionTool(func=run_cli)
