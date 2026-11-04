"""nuvel doctor — diagnose nuvel install and the agent in cwd.

Inspired by `hermes doctor`. Prints a categorised checklist with
[OK] / [WARN] / [FAIL] markers and exits non-zero on any [FAIL]; every
[WARN] and [FAIL] carries one next step. `--json` emits the same findings
as structured output. Every check is wrapped so a single missing dependency
cannot abort the whole report. Paths in a next-step command are shell-quoted,
so the command can be pasted as is.

Filesystem probes use os.path rather than Path.is_file()/is_dir(), which raise
PermissionError below an unreadable directory on the Python versions we support.

A key can come from the process environment or a .env file. The environment
wins, as load_dotenv() does at runtime, except in a Hermes profile: Hermes loads
its .env with override=True, so there the file wins. Secret values are never
printed, only the names of the variables that hold them.
"""

from __future__ import annotations

import contextlib
import http.client
import importlib
import importlib.metadata
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

_PLACEHOLDER_HINTS = (
    "your_", "your-", "your ", "_here", "changeme", "change_me", "xxx", "todo",
    "placeholder", "example", "sample", "dummy", "fake", "replace", "insert",
    "not-a-key", "not_a_key", "notakey",
)
# Well below any provider's real keys, so only an obvious non-key trips these.
_MIN_KEY_LENGTH = 16
_MIN_KEY_DISTINCT_CHARS = 8
_NETWORK_TIMEOUT = 5.0
_STALE_CACHE_DAYS = 30
_ASCII_GLYPHS = str.maketrans({"→": "->", "—": "-"})
_REPORT_FIX = (
    "Report this with the output of `nuvel doctor --json`: "
    "https://github.com/Folken2/nuvel/issues"
)


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""

    def format(self) -> str:
        tag = f"[{self.status}]"
        line = f"  {tag:<7} {self.name}"
        if self.detail:
            line += f" — {self.detail}"
        if self.fix:
            line += f"\n          → {self.fix}"
        return line


@dataclass
class AgentInfo:
    is_agent: bool = False
    framework: str | None = None
    reasons: list[str] = field(default_factory=list)
    root: Path | None = None  # Where the agent's .env lives, when not the cwd.
    problems: list[Check] = field(default_factory=list)  # Found while detecting the agent.


# ── helpers ─────────────────────────────────────────────────────────


def _crashed(name: str, exc: Exception) -> Check:
    fix = _REPORT_FIX
    if isinstance(exc, OSError) and exc.filename:
        fix = f"Check that the path exists and you can read it: ls -ld {shlex.quote(str(exc.filename))}"
    return Check(name, FAIL, f"check raised {type(exc).__name__}: {exc}", fix)


def _safe(fn, name: str, *args, **kwargs) -> Check:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - never crash doctor
        return _crashed(name, exc)


def _safe_many(fn, name: str, *args, **kwargs) -> list[Check]:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - never crash doctor
        return [_crashed(name, exc)]


def parse_env_file(path: Path) -> dict[str, str]:
    """Best-effort .env parser, using python-dotenv's own grammar when installed."""
    env: dict[str, str] = {}
    if not os.path.isfile(path):
        return env
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return env
    try:
        from dotenv.parser import parse_stream
    except ImportError:
        return _parse_env_lines(text)
    for binding in parse_stream(io.StringIO(text)):
        if binding.key is not None and not binding.error:
            env[binding.key] = binding.value or ""
    return env


def _parse_env_lines(text: str) -> dict[str, str]:
    """KEY=value fallback for an install too broken to import python-dotenv."""
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key] = value
    return env


def _is_placeholder(value: str) -> bool:
    value = value.strip()
    # The <...> form is what our own next steps tell people to fill in; *** and ... are redactions.
    if not value or value.startswith("<") or value.endswith(">") or not set(value) - set("*.-_"):
        return True
    low = value.lower()
    return any(hint in low for hint in _PLACEHOLDER_HINTS)


# ── install checks ──────────────────────────────────────────────────


def _source_root() -> Optional[Path]:
    """The repo root when nuvel runs from a source checkout, else None."""
    root = Path(__file__).resolve().parent.parent
    return root if os.path.isfile(root / "pyproject.toml") else None


def _pip_install(*args: str) -> str:
    """A pip install for the Python doctor runs under; venvs made by uv have no pip."""
    pip = shlex.join([sys.executable, "-m", "pip", "install", *args])
    if importlib.util.find_spec("pip"):
        return pip
    if shutil.which("uv"):
        return shlex.join(["uv", "pip", "install", "--python", sys.executable, *args])
    return f"{shlex.join([sys.executable, '-m', 'ensurepip'])} && {pip}"


def _reinstall_fix() -> str:
    root = _source_root()
    if root:
        # This is `make install`, except that it can't pick up another environment's pip.
        command = _pip_install("-e", str(root))
        return f"Reinstall this source checkout: {command}"
    if "pipx" in Path(sys.prefix).parts and shutil.which("pipx"):
        return "Reinstall the published package: pipx reinstall nuvel-cli"
    return f"Reinstall the published package: {_pip_install('--force-reinstall', 'nuvel-cli')}"


def _usable_python() -> Optional[str]:
    """A Python on PATH that can build nuvel's venv; Debian splits ensurepip out of its python3."""
    probe = "import sys, ensurepip, venv; sys.exit(sys.version_info < (3, 11))"
    for name in ("python3", *(f"python3.{minor}" for minor in range(20, 10, -1))):
        if not shutil.which(name):
            continue
        try:
            if subprocess.run([name, "-c", probe], capture_output=True, timeout=5).returncode == 0:
                return name
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _python_upgrade_fix() -> str:
    """The running Python is too old, so anything installed into it has to be rebuilt."""
    root = _source_root()
    if root:
        venv = f"{root}/.venv"
        install = ["-e", str(root)]
        where = "rebuild this source checkout's venv"
    else:
        venv = f"{Path.home()}/.nuvel-venv"
        install = ["nuvel-cli"]
        where = "install the published package into a new venv"
    python = _usable_python()
    if not python:
        return (
            "Install Python 3.11 or newer from https://www.python.org/downloads/ (on Debian or Ubuntu, "
            f"also its venv package, such as python3-venv), then run nuvel doctor again for the commands to {where}"
        )
    create = shlex.join([python, "-m", "venv", venv])
    pip = shlex.join([f"{venv}/bin/python", "-m", "pip", "install", *install])
    return f"Use {python} (Python 3.11 or newer, already on PATH) to {where}: {create} && {pip}"


def check_python_version() -> Check:
    major, minor = sys.version_info[:2]
    detail = f"{major}.{minor}.{sys.version_info.micro}"
    if (major, minor) >= (3, 11):
        return Check("Python version", OK, detail)
    return Check(
        "Python version", FAIL, f"{detail} (need >= 3.11)", _python_upgrade_fix(),
    )


def check_import(module: str, *, optional: bool = False, label: str | None = None) -> Check:
    name = label or f"import {module}"
    try:
        importlib.import_module(module)
        return Check(name, OK)
    except Exception as exc:  # noqa: BLE001 - a broken package can raise anything on import
        return Check(name, WARN if optional else FAIL, str(exc), _reinstall_fix())


def check_command(cmd: str, *, optional: bool = False, fix: str = "") -> Check:
    name = f"{cmd} on PATH"
    if shutil.which(cmd):
        return Check(name, OK)
    return Check(name, WARN if optional else FAIL, "not found", fix or f"Install {cmd}")


def check_distribution() -> Check:
    """nuvel-cli is installed, and at the version a source checkout declares."""
    name = "nuvel-cli package"
    try:
        version = importlib.metadata.version("nuvel-cli")
    except importlib.metadata.PackageNotFoundError:
        return Check(name, FAIL, "no installed package metadata", _reinstall_fix())
    root = _source_root()
    try:
        # Local: it's stdlib only from 3.11, and doctor has to report an older Python.
        import tomllib
    except ImportError:
        return Check(name, OK, version)
    if root:
        pyproject = root / "pyproject.toml"
        try:
            declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # TOMLDecodeError and UnicodeDecodeError are ValueErrors.
            return Check(
                name, FAIL, f"cannot read {pyproject}",
                f"Fix or restore it: git -C {shlex.quote(str(root))} checkout -- pyproject.toml",
            )
        wanted = declared.get("project", {}).get("version")
        if wanted and wanted != version:
            return Check(
                name, FAIL, f"installed {version}, but this checkout is {wanted}",
                _reinstall_fix(),
            )
    return Check(name, OK, version)


def _unmet_requirement(spec: str) -> Optional[str]:
    """Describe why a declared requirement isn't met, or None if it is."""
    try:
        from packaging.requirements import Requirement
    except ImportError:
        # Not a declared dependency; fall back to checking the name alone.
        dist = spec.split(";")[0]
        for sep in "<>=!~ [(":
            dist = dist.split(sep)[0]
        requirement = None
    else:
        requirement = Requirement(spec)
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            return None
        dist = requirement.name
    try:
        version = importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return f"{dist} is not installed"
    if requirement and not requirement.specifier.contains(version, prereleases=True):
        return f"{dist} {version} does not satisfy {requirement.specifier}"
    return None


def check_declared_dependencies() -> Check:
    """Every dependency nuvel-cli declares is installed at an allowed version."""
    name = "declared dependencies"
    try:
        specs = importlib.metadata.requires("nuvel-cli") or []
    except importlib.metadata.PackageNotFoundError:
        return Check(name, WARN, "no installed package metadata to compare", _reinstall_fix())
    unmet = [problem for spec in specs if (problem := _unmet_requirement(spec))]
    if unmet:
        return Check(name, FAIL, "; ".join(unmet), _reinstall_fix())
    return Check(name, OK, f"{len(specs)} checked")


def check_bundled_files() -> Check:
    """The data files `nuvel agent create` and the plugins read at runtime shipped."""
    pkg = Path(__file__).resolve().parent
    expected = (
        "backends/adk/templates",
        "backends/adk/templates_overlays",
        "backends/adk/skills",
        "backends/hermes/templates",
        "plugins/pricing.json",
        "plugins/context_windows.json",
    )
    missing = [rel for rel in expected if not (pkg / rel).exists()]
    if missing:
        return Check("bundled files", FAIL, f"missing: {', '.join(missing)}", _reinstall_fix())
    return Check("bundled files", OK)


def run_install_checks() -> list[Check]:
    checks: list[Check] = [
        _safe(check_python_version, "Python version"),
        _safe(check_distribution, "nuvel-cli package"),
        _safe(check_declared_dependencies, "declared dependencies"),
        _safe(check_bundled_files, "bundled files"),
    ]
    # Core runtime deps from requirements.txt.
    for mod in ("yaml", "fastapi", "uvicorn", "dotenv", "litellm", "composio", "google.adk"):
        checks.append(_safe(check_import, f"import {mod}", mod))
    checks.append(_safe(
        check_command, "git", "git", optional=True,
        fix="Install git; `nuvel bots` needs it to fetch the skills hub",
    ))
    return checks


# ── agent detection ─────────────────────────────────────────────────


def _find_hermes_profile(cwd: Path) -> Optional[Path]:
    """Hermes projects have no requirements.txt: a profile is config.yaml + SOUL.md."""
    if not os.path.isdir(cwd):
        return None
    for candidate in (cwd, *sorted(p for p in cwd.iterdir() if os.path.isdir(p))):
        if os.path.isfile(candidate / "config.yaml") and os.path.isfile(candidate / "SOUL.md"):
            return candidate
    return None


def detect_agent(cwd: Path) -> AgentInfo:
    info = AgentInfo()
    # A nuvel checkout looks like an ADK agent, but what runs from it is the
    # meta-agent, which has its own checks.
    if os.path.isfile(cwd / "nuvel" / "doctor.py"):
        return info
    profile = _find_hermes_profile(cwd)
    if profile:
        return AgentInfo(is_agent=True, framework="hermes", root=profile)
    req = cwd / "requirements.txt"
    has_entry = (
        os.path.isfile(cwd / "agent.py")
        or os.path.isfile(cwd / "server.py")
        or os.path.isfile(cwd / "run_adk.py")
    )
    if not has_entry and os.path.isdir(cwd):
        # ADK agents put agent.py inside a package subdir.
        for sub in cwd.iterdir():
            if os.path.isdir(sub) and os.path.isfile(sub / "agent.py"):
                has_entry = True
                break
    if not os.path.isfile(req) or not has_entry:
        return info
    try:
        raw = req.read_bytes()
    except OSError:
        return info
    info.is_agent = True
    try:
        text = raw.decode("utf-8").lower()
    except UnicodeDecodeError:
        # A stray byte need not hide the framework; a UTF-16 file will, until re-saved.
        text = raw.decode("utf-8", errors="replace").lower()
        info.problems.append(Check("requirements.txt", FAIL, "not valid UTF-8", f"Re-save {req} as UTF-8"))
    if "google-adk" in text:
        info.framework = "adk"
    elif "claude-agent-sdk" in text:
        info.framework = "claude-agent-sdk"
    elif "anthropic" in text:
        info.framework = "anthropic-managed-agents"
    return info


# ── agent checks ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Provider:
    key_env: Optional[str]  # None for a local provider that takes no key.
    url: str  # Probed without credentials, so any HTTP response proves reachability.
    keys_page: str = ""
    key_prefix: str = ""  # Left out where a provider's key format isn't a stable promise.
    base_env: Optional[str] = None  # Env var that moves the endpoint away from ``url``.


_PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider(
        "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/models",
        "https://openrouter.ai/keys", "sk-or-",
    ),
    "anthropic": Provider(
        "ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/models",
        "https://console.anthropic.com/settings/keys", "sk-ant-",
    ),
    "openai": Provider(
        "OPENAI_API_KEY", "https://api.openai.com/v1/models",
        "https://platform.openai.com/api-keys", "sk-",
    ),
    "groq": Provider(
        "GROQ_API_KEY", "https://api.groq.com/openai/v1/models",
        "https://console.groq.com/keys", "gsk_",
    ),
    "gemini": Provider(
        "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/",
        "https://aistudio.google.com/apikey",
    ),
    "ollama": Provider(None, "http://localhost:11434/", base_env="OLLAMA_API_BASE"),
    "ollama_chat": Provider(None, "http://localhost:11434/", base_env="OLLAMA_API_BASE"),
}

# Known to LiteLLM, but no single API key to look for and no one URL to probe.
_NON_KEY_PROVIDERS = ("bedrock", "vertex_ai", "sagemaker")

# LiteLLM infers the provider of a model id with no provider/ prefix.
_BARE_MODEL_PREFIXES = {
    "gpt-": "openai", "chatgpt-": "openai", "o1": "openai", "o3": "openai", "o4": "openai",
    "claude-": "anthropic",
    "gemini-": "gemini",
}

# Frameworks pinned to one provider; ADK and Hermes read theirs from config.
_FIXED_PROVIDERS: dict[str, str] = {
    "claude-agent-sdk": "anthropic",
    "anthropic-managed-agents": "anthropic",
}

_COMPOSIO_KEYS_PAGE = "https://composio.dev"

# The gateway refuses every request without these.
_GATEWAY_KEYS: dict[str, tuple[str, ...]] = {
    "slack": ("COMPOSIO_WEBHOOK_SECRET",),
    "telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET"),
}

_TEAMS_SDK_FIX = "Only needed for Azure Bot Service; without it the bridge runs in anonymous mode. Add {key}=<value> to .env"

# The gateway runs without these, in a reduced mode the next step describes.
_OPTIONAL_GATEWAY_KEYS: dict[str, dict[str, str]] = {
    "slack": {
        "SLACK_BOT_TOKEN": "Only needed to download uploaded files; without it the agent gets just their URLs. Add {key}=<value> to .env",
    },
    "teams_bridge": {
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": _TEAMS_SDK_FIX,
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": _TEAMS_SDK_FIX,
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": _TEAMS_SDK_FIX,
    },
}

# Hermes refuses to start an enabled platform without these.
_HERMES_PLATFORM_KEYS: dict[str, tuple[str, ...]] = {
    "telegram": ("TELEGRAM_BOT_TOKEN",),
}


def _check_env_key(key: str, env: dict[str, str], fix: str = "", missing: str = FAIL) -> Check:
    fix = fix or f"Add {key}=<value> to .env"
    if key not in env:
        return Check(key, missing, "not set in .env or the environment", fix)
    if _is_placeholder(env[key]):
        return Check(key, missing, "empty or still a placeholder", fix)
    return Check(key, OK, "set")


def _key_problem(key: str, env: dict[str, str], prefix: str = "") -> Optional[str]:
    """Why this value can't be a real API key, or None. Never quotes the value."""
    value = env[key]
    if not value.isascii() or any(char.isspace() for char in value):
        return "malformed: contains spaces or non-ASCII characters"
    if prefix and not value.startswith(prefix):
        return f"malformed: does not start with {prefix}"
    if len(value) < _MIN_KEY_LENGTH:
        return "malformed: too short to be a real key"
    if len(set(value)) < _MIN_KEY_DISTINCT_CHARS:
        return "malformed: too repetitive to be a real key"
    return None


def _check_api_key(key: str, env: dict[str, str], fix: str, prefix: str = "") -> Check:
    check = _check_env_key(key, env, fix)
    if check.status == OK and (problem := _key_problem(key, env, prefix)):
        return Check(key, FAIL, problem, fix)
    return check


def _find_env_file(start: Path) -> Optional[Path]:
    """Find the .env load_dotenv() would load for code living in ``start``."""
    for directory in (start, *start.parents):
        if os.path.isfile(directory / ".env"):
            return directory / ".env"
    return None


def _effective_env(env_file: Optional[Path], *, file_wins: bool = False) -> dict[str, str]:
    env = parse_env_file(env_file) if env_file else {}
    return {**os.environ, **env} if file_wins else {**env, **os.environ}


def check_env_file_valid(path: Path) -> Check:
    """Line numbers only: a malformed line may well hold a secret."""
    name = ".env file"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Check(name, FAIL, f"unreadable: {exc.strerror}", f"Fix its permissions: chmod u+r {shlex.quote(str(path))}")
    except UnicodeDecodeError:
        return Check(name, FAIL, "not valid UTF-8", f"Re-save {path} as UTF-8")
    try:
        from dotenv.parser import parse_stream
    except ImportError:
        return Check(name, OK, str(path))
    bad = [str(b.original.line) for b in parse_stream(io.StringIO(text)) if b.error]
    if bad:
        return Check(
            name, FAIL, f"cannot parse line(s) {', '.join(bad)}",
            f"Rewrite line(s) {', '.join(bad)} of {path} as KEY=value, or comment them out",
        )
    return Check(name, OK, str(path))


def _key_is_set(key: str, env: dict[str, str]) -> bool:
    return key in env and not _is_placeholder(env[key])


def _model_provider(model: str) -> str:
    if "/" in model:
        return model.split("/", 1)[0]
    for prefix, provider in _BARE_MODEL_PREFIXES.items():
        if model.startswith(prefix):
            return provider
    return model


def _provider_key(name: str) -> Optional[str]:
    """The env var holding this provider's key; unknown ones follow LiteLLM's naming."""
    if name in _PROVIDERS:
        return _PROVIDERS[name].key_env
    if name in _NON_KEY_PROVIDERS:
        return None
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()) + "_API_KEY"


def configured_providers(
    info: AgentInfo, env: dict[str, str], hermes_provider: Optional[str] = None,
) -> list[str]:
    """Names of the providers this agent (or the meta-agent) will call."""
    framework = info.framework if info.is_agent else "adk"
    if framework == "adk":
        models = (env.get(var) or default for var, default in _MODEL_DEFAULTS.items())
        return list(dict.fromkeys(_model_provider(m) for m in models))
    if framework == "hermes":
        return [hermes_provider] if hermes_provider else []
    fixed = _FIXED_PROVIDERS.get(framework or "")
    return [fixed] if fixed else []


def check_hermes_config(profile: Path) -> tuple[Check, Optional[str], list[str]]:
    """Validate a Hermes config.yaml; return the check, model.provider, enabled platforms."""
    name = "config.yaml"
    path = profile / "config.yaml"
    try:
        import yaml
    except ImportError:
        return Check(name, WARN, "cannot validate without pyyaml", _reinstall_fix()), None, []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Check(name, FAIL, f"unreadable: {exc.strerror}", f"Fix its permissions: chmod u+r {shlex.quote(str(path))}"), None, []
    except UnicodeDecodeError:
        return Check(name, FAIL, "not valid UTF-8", f"Re-save {path} as UTF-8"), None, []
    try:
        config = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        where = getattr(exc, "problem_mark", None)
        at = f" at line {where.line + 1}" if where else ""
        return Check(name, FAIL, f"cannot parse{at}", f"Fix the YAML in {path}"), None, []
    except Exception as exc:  # noqa: BLE001 - a bad scalar (2001-13-45) raises ValueError and the like
        return Check(name, FAIL, f"cannot parse: {exc}", f"Fix the YAML in {path}"), None, []
    if not isinstance(config, dict):
        config = {}
    model = config.get("model")
    provider = model.get("provider") if isinstance(model, dict) else None
    platforms = config.get("platforms")
    enabled = [
        platform for platform, settings in platforms.items()
        if isinstance(settings, dict) and settings.get("enabled")
    ] if isinstance(platforms, dict) else []
    if not provider:
        return Check(
            name, FAIL, "model.provider is not set",
            f"Set it: add `provider: openrouter` under `model:` in {path}",
        ), None, enabled
    return Check(name, OK, str(path)), str(provider), enabled


def _provider_key_checks(
    providers: Iterable[str], env: dict[str, str], key_fix: Callable[[str], str],
) -> list[Check]:
    """One check per API key the configured providers need."""
    checks: list[Check] = []
    for name in providers:
        provider = _PROVIDERS.get(name)
        key = _provider_key(name)
        if key is None:
            if provider is None:
                checks.append(Check(
                    f"provider {name}", WARN, "no single API key for doctor to check",
                    "Check that provider's docs and confirm its credentials are set",
                ))
            continue
        if provider is None:
            known = ", ".join(_PROVIDERS)
            check = _safe(
                _check_api_key, key, key, env,
                f"A real key is a long random string from {name}'s dashboard. {key_fix(key)}. "
                f"Or route the model through a provider doctor knows ({known})",
            )
            if check.status == FAIL:
                check.detail += f" (the key expected for unknown provider {name})"
            checks.append(check)
            continue
        shape = f"starts with {provider.key_prefix}" if provider.key_prefix else "is a long random string"
        fix = f"A real key {shape} (get one at {provider.keys_page}). {key_fix(key)}"
        checks.append(_safe(_check_api_key, key, key, env, fix, provider.key_prefix))
    return checks


def _probe_url(base: str) -> Optional[str]:
    """Where to probe a configured base URL, or None if it isn't http(s).

    Credentials and query strings are dropped: the probe sends none, and the
    URL ends up in the report.
    """
    try:
        parts = urllib.parse.urlsplit(base)
    except ValueError:
        return None
    host = parts.netloc.rpartition("@")[2]
    if parts.scheme not in ("http", "https") or not host:
        return None
    return f"{parts.scheme}://{host}{parts.path.rstrip('/')}/"


def _local_server_fix(base_env: str) -> str:
    start = "Start it: ollama serve" if shutil.which("ollama") else (
        "Install Ollama (https://ollama.com/download), then start it: ollama serve"
    )
    return f"{start}. If it runs on another machine, set {base_env} to its URL"


def check_provider_reachable(
    name: str, provider: Provider, timeout: float = _NETWORK_TIMEOUT, *, env: Optional[dict[str, str]] = None,
) -> Check:
    check_name = f"{name} reachable"
    url = provider.url
    base = (env or {}).get(provider.base_env, "").strip() if provider.base_env else ""
    if base:
        url = _probe_url(base)
        if url is None:
            return Check(
                check_name, FAIL, f"{provider.base_env} is not an http(s) URL",
                f"Set {provider.base_env} to the server's URL, like http://192.168.1.10:11434",
            )
        where = f"not reachable at {url} (from {provider.base_env})"
        fix = (
            f"Check that the server is up and accepts remote connections, and that "
            f"{provider.base_env} is right, then retry: curl -I {shlex.quote(url)}"
        )
    elif provider.base_env:
        where = f"not running locally at {url}"
        fix = _local_server_fix(provider.base_env)
    else:
        where = url
        fix = f"Check your connection or proxy (HTTPS_PROXY), then retry: curl -I {url}"
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Check(check_name, OK, f"{url} (HTTP {response.status})")
    except urllib.error.HTTPError as exc:
        # No credentials were sent, so 401/403/404/405 still prove the route works.
        if exc.code >= 500:
            return Check(
                check_name, WARN, f"{url} answered HTTP {exc.code}",
                f"Likely a provider outage; retry shortly: curl -I {shlex.quote(url)}",
            )
        return Check(check_name, OK, f"{url} (HTTP {exc.code})")
    except urllib.error.URLError as exc:
        return Check(check_name, FAIL, f"{where}: {exc.reason}", fix)
    except (OSError, http.client.HTTPException) as exc:
        return Check(check_name, FAIL, f"{where}: {exc}", fix)


def run_network_checks(providers: Iterable[str], env: Optional[dict[str, str]] = None) -> list[Check]:
    checks = [
        _safe(check_provider_reachable, f"{name} reachable", name, _PROVIDERS[name], env=env)
        if name in _PROVIDERS else
        Check(
            f"{name} reachable", WARN, "doctor does not know its endpoint, nothing probed",
            f"Find {name}'s API URL in its docs and confirm it responds to curl -I",
        )
        for name in providers
    ]
    return checks or [Check(
        "provider reachable", WARN, "no known provider configured, nothing probed",
        "Find your provider's API URL in its docs and confirm it responds to curl -I",
    )]


# ── state and caches ────────────────────────────────────────────────


def check_writable(label: str, path: Path, fix: str = "") -> Check:
    """Probe the path, or the ancestor it would be created in; doctor creates no dirs."""
    target = path
    while not os.path.exists(target) and target != target.parent:
        target = target.parent
    if not os.path.isdir(target):
        return Check(label, FAIL, f"{target} is not a directory", f"Remove or move {target}")
    try:
        with tempfile.TemporaryFile(dir=target):
            pass
    except OSError as exc:
        return Check(
            label, FAIL, f"{target} is not writable ({exc.strerror})",
            fix or f"Make it writable: chmod u+w {shlex.quote(str(target))}",
        )
    return Check(label, OK, str(path))


def _writable_check(label: str, path: Path, fix: str = "") -> Check:
    return _safe(check_writable, label, label, path, fix)


def _data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def run_state_checks(cwd: Path, info: AgentInfo, env: dict[str, str]) -> list[Check]:
    trace_dir = cwd / env.get("TRACE_DIR", "traces")
    checks = [_writable_check(
        "traces dir", trace_dir,
        f"Run from a writable directory, or set TRACE_DIR to one (now {trace_dir})",
    )]
    if not info.is_agent:
        output = cwd / env.get("AGENTS_OUTPUT_DIR", "generated-agents")
        checks.append(_writable_check(
            "agents output dir", output,
            f"Run from a writable directory, or set AGENTS_OUTPUT_DIR to one (now {output})",
        ))
    home = Path.home()
    for label, path in (
        ("~/.nuvel", home / ".nuvel"),
        ("~/.cache/nuvel", home / ".cache" / "nuvel"),
        ("nuvel data dir", _data_home() / "nuvel"),
    ):
        checks.append(_writable_check(label, path))
    return checks


def check_scored_cache(label: str, path: Path) -> Check:
    if not os.path.isdir(path):
        return Check(label, OK, "empty")
    cutoff = time.time() - _STALE_CACHE_DAYS * 86400
    entries = list(path.rglob("*.json"))

    def is_stale(entry: Path) -> bool:
        try:
            return entry.stat().st_mtime < cutoff
        except OSError:  # A dangling symlink, or a file we can't stat, is no use as a cache.
            return True

    stale = sum(1 for entry in entries if is_stale(entry))
    if stale:
        return Check(
            label, WARN,
            f"{stale} of {len(entries)} entries unreadable or older than {_STALE_CACHE_DAYS} days",
            f"Clear it (examples are re-scored on the next run): rm -rf {shlex.quote(str(path))}",
        )
    return Check(label, OK, f"{len(entries)} entries")


def check_skills_hub_clone(path: Path) -> Check:
    label = "skills hub clone"
    git_dir = path / ".git"
    if not os.path.isdir(git_dir):
        return Check(label, OK, "not cloned")
    marker = git_dir / "FETCH_HEAD" if os.path.isfile(git_dir / "FETCH_HEAD") else git_dir / "HEAD"
    try:
        age_days = int((time.time() - marker.stat().st_mtime) / 86400)
    except OSError:
        return Check(
            label, WARN, "clone looks broken: no readable HEAD",
            f"Remove it (it is re-cloned on the next `nuvel bots` run): rm -rf {shlex.quote(str(path))}",
        )
    if age_days > _STALE_CACHE_DAYS:
        return Check(
            label, WARN, f"last fetched {age_days} days ago",
            f"Update it: git -C {shlex.quote(str(path))} pull --ff-only",
        )
    return Check(label, OK, f"fetched {age_days} days ago")


def run_cache_checks() -> list[Check]:
    data = _data_home() / "nuvel"
    # nuvel.bots.skills.DEFAULT_HUB_HOME, which unlike evalv2 ignores XDG_DATA_HOME.
    # Spelled out because importing it needs pyyaml.
    hub = Path.home() / ".local" / "share" / "nuvel" / "skills"
    return [
        _safe(check_scored_cache, "evalv2 sample cache", "evalv2 sample cache",
              Path.home() / ".cache" / "nuvel" / "evalv2"),
        _safe(check_scored_cache, "evalv2 results cache", "evalv2 results cache",
              data / "evalv2" / "cache"),
        _safe(check_skills_hub_clone, "skills hub clone", hub),
    ]


def _systemd_manages_docker() -> bool:
    if not os.path.isdir("/run/systemd/system"):
        return False
    try:
        return subprocess.run(
            ["systemctl", "cat", "docker.service"], capture_output=True, timeout=5,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _docker_start_fix() -> str:
    if sys.platform == "darwin" and os.path.isdir("/Applications/Docker.app"):
        return "Only needed to build the image; start Docker Desktop: open -a Docker"
    if _systemd_manages_docker():
        return "Only needed to build the image; start Docker: sudo systemctl start docker"
    return "Only needed to build the image; start the Docker daemon the way this host runs it, then confirm: docker info"


def _check_docker_running() -> Check:
    if not shutil.which("docker"):
        return Check(
            "Docker available", WARN, "docker CLI not on PATH",
            "Only needed to build the image; install from https://docs.docker.com/get-docker/",
        )
    try:
        r = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("Docker available", WARN, f"failed: {exc}", _docker_start_fix())
    if r.returncode == 0:
        return Check("Docker available", OK, "daemon reachable")
    return Check("Docker available", WARN, "daemon not reachable", _docker_start_fix())


def _find_pricing_entry(model: str, pricing: dict) -> Optional[str]:
    """Mirror cost_guard_plugin._find_pricing: exact then prefix-stripped match.

    Returns the matched pricing key (so we can show which entry covers the
    model), or None.
    """
    if not model:
        return None
    if model in pricing:
        return model
    parts = model.split("/")
    for i in range(len(parts)):
        candidate = "/".join(parts[i:])
        if candidate in pricing:
            return candidate
    return None


def _find_agent_plugins_dir(cwd: Path) -> Optional[Path]:
    """Locate the agent's plugins/ directory (sits inside the package dir)."""
    for sub in cwd.iterdir() if os.path.isdir(cwd) else []:
        if os.path.isdir(sub) and os.path.isdir(sub / "plugins"):
            return sub / "plugins"
    return None


from nuvel._defaults import DEFAULT_FAST_MODEL, DEFAULT_REASONING_MODEL

_MODEL_DEFAULTS = {
    "FAST_MODEL": DEFAULT_FAST_MODEL,
    "REASONING_MODEL": DEFAULT_REASONING_MODEL,
}


def _pricing_fix(model: str, path: Path) -> str:
    model_id = model.removeprefix("openrouter/")
    add = f"nuvel pricing add --target {shlex.quote(str(path))} {shlex.quote(model_id)}"
    by_hand = f'{json.dumps(model)}: {{"input": <USD per token>, "output": <USD per token>}}'
    return f"If OpenRouter lists it, run: {add}. Otherwise add to {path} by hand (prices of 0 if it's free): {by_hand}"


def check_pricing_coverage(cwd: Path, env: dict[str, str]) -> list[Check]:
    """Verify pricing.json has entries for every active model.

    ADK agents resolve FAST_MODEL and REASONING_MODEL with hard-coded
    fallbacks in config/llm.py — so a missing .env entry isn't a failure
    on its own; the fallback model is what actually runs and is what
    pricing must cover.

    Without coverage, llm_response.cost_usd and run_end.total_cost_usd
    stay null and the traces command center can't show cost.
    """
    plugins = _find_agent_plugins_dir(cwd)
    if plugins is None:
        return [Check(
            "pricing.json coverage", WARN, "no plugins/ dir found",
            "Costs stay null without it; copy plugins/ from a fresh `nuvel agent create`",
        )]

    pricing_path = plugins / "pricing.json"
    if not os.path.isfile(pricing_path):
        return [Check(
            "pricing.json coverage", WARN, f"missing {pricing_path}",
            "Costs stay null without it; copy it from a fresh `nuvel agent create`",
        )]

    try:
        raw = json.loads(pricing_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors.
        return [Check(
            "pricing.json coverage", FAIL, f"parse error: {exc}",
            f"Fix the JSON in {pricing_path}, or copy it from a fresh `nuvel agent create`",
        )]
    if not isinstance(raw, dict):
        return [Check(
            "pricing.json coverage", FAIL, "expected a JSON object of model entries",
            f"Fix the JSON in {pricing_path}, or copy it from a fresh `nuvel agent create`",
        )]

    pricing = {k: v for k, v in raw.items() if not k.startswith("_")}
    checks: list[Check] = []
    for var, default in _MODEL_DEFAULTS.items():
        model = env.get(var) or default
        source = ".env" if env.get(var) else "default"
        matched = _find_pricing_entry(model, pricing)
        name = f"pricing.json: {var}"
        if matched:
            via = "" if matched == model else f" via {matched!r}"
            checks.append(Check(name, OK, f"{model} ({source}){via}"))
        else:
            checks.append(Check(
                name, WARN,
                f"no entry for {model!r} ({source}) — costs will be null",
                _pricing_fix(model, pricing_path),
            ))
    return checks


def _composio_in_use(cwd: Path) -> bool:
    for candidate in cwd.rglob("composio_mcp.py"):
        if ".venv" not in candidate.parts and "site-packages" not in candidate.parts:
            return True
    return False


def _config_file_check(
    env_file: Optional[Path],
    keys: list[str],
    env: dict[str, str],
    missing_fix: str,
) -> Check:
    if env_file is not None:
        return _safe(check_env_file_valid, ".env file", env_file)
    if all(_key_is_set(key, env) for key in keys):
        return Check(".env file", OK, "none needed: required keys are in the environment")
    return Check(".env file", FAIL, "missing", missing_fix)


def _key_prompt(key: str) -> str:
    """Set a key without it landing in the shell history: reads it from a silent prompt."""
    return f"read -rs {key} && export {key}"


def _required_keys(providers: Iterable[str]) -> list[str]:
    return [key for name in providers if (key := _provider_key(name))]


def run_meta_checks(cwd: Path, env_file: Optional[Path], env: dict[str, str]) -> list[Check]:
    """Configuration of `nuvel run`, for when no generated agent is in the cwd.

    The meta-agent's load_dotenv() searches upward from the installed package,
    not from the cwd, so an installed nuvel never reads ./.env.
    """
    providers = configured_providers(AgentInfo(), env)
    keys = _required_keys(providers)
    pkg = Path(__file__).resolve().parent
    root = _source_root()
    example_key = keys[0] if keys else "OPENROUTER_API_KEY"
    if root and os.path.isfile(root / ".env.example"):
        missing_fix = f"Create it: cp {shlex.quote(f'{root}/.env.example')} {shlex.quote(f'{root}/.env')}"
    else:
        prefix = Path(sys.prefix).resolve()
        home = prefix if pkg.is_relative_to(prefix) else None
        missing_fix = (
            f"Create it, typing the key at the silent prompt (bash or zsh): read -rs {example_key} && "
            f"echo \"{example_key}=${example_key}\" >> {shlex.quote(f'{home}/.env')}" if home
            else f"Skip the file and type the key at a silent prompt (bash or zsh): {_key_prompt(example_key)}"
        )
    checks = [_config_file_check(env_file, keys, env, missing_fix)]
    keys_checks = _provider_key_checks(providers, env, lambda key: f"Set it, typing the key at the silent prompt (bash or zsh): {_key_prompt(key)}")
    ignored = parse_env_file(cwd / ".env") if env_file != cwd / ".env" else {}
    for check in keys_checks:
        if check.status == FAIL and check.name in ignored:
            check.detail += "; ./.env sets it, but `nuvel run` does not read that file"
    return checks + keys_checks


@dataclass
class _AgentConfig:
    env_file: Optional[Path]
    env: dict[str, str]
    providers: list[str]
    hermes_check: Optional[Check] = None
    hermes_platforms: list[str] = field(default_factory=list)


def _load_agent_config(cwd: Path, info: AgentInfo) -> _AgentConfig:
    root = info.root or cwd
    if info.framework == "hermes":
        # A profile is its own HERMES_HOME, and Hermes loads $HERMES_HOME/.env only: no upward search.
        env_file = root / ".env" if os.path.isfile(root / ".env") else None
    else:
        env_file = _find_env_file(root)
    env = _effective_env(env_file, file_wins=info.framework == "hermes")
    hermes_check, hermes_provider, platforms = None, None, []
    if info.framework == "hermes":
        hermes_check, hermes_provider, platforms = check_hermes_config(root)
    return _AgentConfig(
        env_file, env, configured_providers(info, env, hermes_provider), hermes_check, platforms,
    )


def run_agent_checks(cwd: Path, info: AgentInfo, config: Optional[_AgentConfig] = None) -> list[Check]:
    checks: list[Check] = []
    root = info.root or cwd
    config = config or _load_agent_config(cwd, info)
    env_path, env, providers = config.env_file, config.env, config.providers

    checks.extend(info.problems)
    if config.hermes_check:
        checks.append(config.hermes_check)

    example = root / ".env.example"
    missing_fix = (
        f"Create it: cp {shlex.quote(str(example))} {shlex.quote(f'{root}/.env')}" if os.path.isfile(example)
        else f"Create {root}/.env with the keys listed below"
    )
    checks.append(_config_file_check(env_path, _required_keys(providers), env, missing_fix))
    checks.extend(_provider_key_checks(providers, env, lambda key: f"Add {key}=<your key> to {root}/.env"))
    if not providers and info.framework != "hermes":
        checks.append(Check(
            "API keys", WARN, f"not checked for framework {info.framework or 'unknown'}",
            "Compare your .env against this agent's .env.example",
        ))

    for platform in config.hermes_platforms:
        keys = _HERMES_PLATFORM_KEYS.get(platform)
        if keys is None:
            checks.append(Check(
                f"platform:{platform}", WARN, "doctor does not know which key it needs",
                "Check that platform's Hermes docs and confirm its token is set",
            ))
        for key in keys or ():
            check = _safe(_check_env_key, key, key, env, f"Add {key}=<value> to {root}/.env")
            check.name = f"platform:{platform} {key}"
            checks.append(check)

    if _composio_in_use(cwd):
        checks.append(_safe(
            _check_api_key, "COMPOSIO_API_KEY", "COMPOSIO_API_KEY", env,
            f"A real key is a long random string (get one at {_COMPOSIO_KEYS_PAGE}, under Settings > Project Settings > API Keys). "
            f"Add COMPOSIO_API_KEY=<your key> to {root}/.env",
        ))

    gateways_dir = cwd / "gateways"
    if not os.path.isdir(gateways_dir):
        # Templates put gateways/ inside the package dir.
        for sub in cwd.iterdir() if os.path.isdir(cwd) else []:
            cand = sub / "gateways"
            if os.path.isdir(cand):
                gateways_dir = cand
                break
    if os.path.isdir(gateways_dir):
        for stem in dict.fromkeys((*_GATEWAY_KEYS, *_OPTIONAL_GATEWAY_KEYS)):
            if not os.path.isfile(gateways_dir / f"{stem}.py"):
                continue
            for key in _GATEWAY_KEYS.get(stem, ()):
                check = _safe(_check_env_key, key, key, env, f"Add {key}=<value> to {root}/.env")
                check.name = f"gateway:{stem} {key}"
                checks.append(check)
            for key, fix in _OPTIONAL_GATEWAY_KEYS.get(stem, {}).items():
                check = _safe(_check_env_key, key, key, env, fix.format(key=key), WARN)
                check.name = f"gateway:{stem} {key}"
                checks.append(check)

    if os.path.isfile(cwd / "Dockerfile"):
        checks.append(_safe(_check_docker_running, "Docker available"))

    if info.framework == "adk":
        try:
            checks.extend(check_pricing_coverage(cwd, env))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check(
                "pricing.json coverage", FAIL,
                f"check raised {type(exc).__name__}: {exc}",
                _REPORT_FIX,
            ))

    return checks


# ── ACP adapter (--with-acp) ────────────────────────────────────────


def detect_acp_package(cwd: Path) -> Optional[str]:
    """Return the package name of a --with-acp agent (has ``<pkg>/acp/__main__.py``)."""
    try:
        subdirs = sorted(cwd.iterdir())
    except OSError:
        return None
    for sub in subdirs:
        if os.path.isdir(sub) and os.path.isfile(sub / "acp" / "__main__.py"):
            return sub.name
    return None


def zed_config_snippet(cwd: Path, pkg: str, *, python: str | None = None) -> str:
    """A ready-to-paste Zed ``agent_servers`` entry for this ACP agent."""
    snippet = {
        "agent_servers": {
            pkg.replace("_", "-"): {
                "command": python or sys.executable,
                "args": ["-m", f"{pkg}.acp"],
                "cwd": str(cwd),
            }
        }
    }
    return json.dumps(snippet, indent=2)


def check_acp_handshake(cwd: Path, pkg: str, timeout: float = 20.0) -> Check:
    """Smoke-test the ACP stdio protocol: send ``initialize``, expect a reply.

    Runs the adapter with ``DEV_MODE=true`` so it uses in-memory services and
    doesn't need a database — this checks the JSON-RPC handshake, not config.
    """
    name = "ACP stdio handshake"
    cmd = [sys.executable, "-m", f"{pkg}.acp"]
    deps_fix = f"Install the agent's deps: {_pip_install('-r', f'{cwd}/requirements.txt')}"
    debug_fix = f"Run it directly to see the error: cd {shlex.quote(str(cwd))} && {shlex.join(cmd)}"
    env = {**os.environ, "DEV_MODE": "true", "LOG_LEVEL": "ERROR"}
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
    except OSError as exc:
        return Check(name, FAIL, f"could not launch: {exc}", debug_fix)

    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 2, "clientCapabilities": {}},
        }
    )
    holder: dict[str, str] = {}

    def _read_line() -> None:
        try:
            holder["line"] = proc.stdout.readline() if proc.stdout else ""
        except Exception as exc:  # noqa: BLE001
            holder["error"] = str(exc)

    try:
        if proc.stdin:
            proc.stdin.write(request + "\n")
            proc.stdin.flush()
    except OSError:
        pass  # process may have died on import; handled via readline/EOF below

    reader = threading.Thread(target=_read_line, daemon=True)
    reader.start()
    reader.join(timeout)

    def _stop() -> None:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass

    if reader.is_alive():
        _stop()
        return Check(
            name, WARN, f"no response within {timeout:.0f}s (are agent deps installed?)", deps_fix,
        )

    line = holder.get("line", "")
    stderr_tail = ""
    if not line and proc.stderr:
        try:
            stderr_tail = (proc.stderr.read() or "")[-300:]
        except Exception:  # noqa: BLE001
            stderr_tail = ""
    _stop()

    if not line:
        low = stderr_tail.lower()
        if "modulenotfound" in low or "importerror" in low or "no module named" in low:
            return Check(name, WARN, "agent deps not installed (pip install -r requirements.txt)", deps_fix)
        lines = stderr_tail.strip().splitlines()
        detail = lines[-1][:120] if lines else "no output"
        return Check(name, FAIL, f"no initialize response — {detail}", debug_fix)

    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return Check(name, FAIL, f"invalid JSON-RPC line: {line.strip()[:80]}", debug_fix)

    result = msg.get("result") or {}
    if "protocolVersion" in result and "agentCapabilities" in result:
        return Check(name, OK, f"initialize → protocolVersion {result['protocolVersion']}")
    return Check(name, FAIL, f"unexpected initialize response: {line.strip()[:80]}", debug_fix)


def run_acp_checks(cwd: Path, pkg: str) -> list[Check]:
    return [_safe(check_acp_handshake, "ACP stdio handshake", cwd, pkg)]


# ── orchestration ───────────────────────────────────────────────────


def check_target(cwd: Optional[Path]) -> Optional[Check]:
    """A FAIL when the directory to inspect can't be used, else None."""
    if cwd is None:
        return Check(
            "directory", FAIL, "the current directory no longer exists",
            'Rerun from an existing directory: cd "$HOME" && nuvel doctor',
        )
    unreadable = Check(
        "directory", FAIL, f"{cwd} cannot be read",
        f"Fix its permissions, or pass another directory to --path: chmod u+rx {shlex.quote(str(cwd))}",
    )
    try:
        with os.scandir(cwd):
            pass
    except PermissionError:
        return unreadable
    except OSError:
        return Check(
            "directory", FAIL, f"{cwd} does not exist or is not a directory",
            'Pass an existing directory: nuvel doctor --path "$HOME"',
        )
    return None if os.access(cwd, os.X_OK) else unreadable


def _collect_target_sections(cwd: Path, acp_pkg: Optional[str]) -> list[tuple[str, list[Check]]]:
    sections: list[tuple[str, list[Check]]] = []
    info = detect_agent(cwd)
    if info.is_agent:
        config = _load_agent_config(cwd, info)
        env, providers = config.env, config.providers
        sections.append((
            f"Agent ({info.framework or 'unknown'})",
            _safe_many(run_agent_checks, "agent config", cwd, info, config),
        ))
    else:
        env_file = _find_env_file(Path(__file__).resolve().parent)
        env = _effective_env(env_file)
        providers = configured_providers(info, env)
        sections.append((
            "Configuration (nuvel run; no generated agent in this directory)",
            _safe_many(run_meta_checks, "nuvel run config", cwd, env_file, env),
        ))

    sections.append(("State", _safe_many(run_state_checks, "state dirs", cwd, info, env)))
    sections.append(("Network", _safe_many(run_network_checks, "provider network", providers, env)))
    sections.append(("Caches", _safe_many(run_cache_checks, "caches")))
    if acp_pkg:
        sections.append(("ACP adapter", run_acp_checks(cwd, acp_pkg)))
    return sections


def collect_sections(
    cwd: Optional[Path], acp_pkg: Optional[str] = None,
) -> list[tuple[str, list[Check]]]:
    """Run every check. Output is captured so a chatty import can't corrupt --json."""
    with contextlib.redirect_stdout(sys.stderr):
        sections = [("Install", run_install_checks())]
        problem = check_target(cwd)
        if problem:
            return [*sections, ("Target", [problem])]
        try:
            return [*sections, *_collect_target_sections(cwd, acp_pkg)]
        except Exception as exc:  # noqa: BLE001 - never crash doctor
            return [*sections, ("Target", [_crashed("agent detection", exc)])]


def _summarize(sections: list[tuple[str, list[Check]]]) -> dict[str, int]:
    statuses = [c.status for _, checks in sections for c in checks]
    return {
        "ok": statuses.count(OK),
        "warn": statuses.count(WARN),
        "fail": statuses.count(FAIL),
    }


def _print_json(
    cwd: Optional[Path], sections: list[tuple[str, list[Check]]], code: int, acp_pkg: Optional[str],
) -> None:
    report: dict = {
        "ok": code == 0,
        "exit_code": code,
        "cwd": str(cwd) if cwd else None,
        "summary": _summarize(sections),
        "checks": [
            {"section": title, "name": c.name, "status": c.status, "detail": c.detail, "fix": c.fix}
            for title, checks in sections for c in checks
        ],
    }
    if acp_pkg:
        report["zed_config"] = json.loads(zed_config_snippet(cwd, acp_pkg))
    print(json.dumps(report, indent=2))


def _print_line(line: str = "") -> None:
    """Print to whatever stdout can encode: a report is no use if a glyph or path kills it."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        line.encode(encoding)
    except UnicodeEncodeError:
        line = line.translate(_ASCII_GLYPHS).encode(encoding, "backslashreplace").decode(encoding)
    print(line)


def _print_text(
    cwd: Optional[Path], sections: list[tuple[str, list[Check]]], acp_pkg: Optional[str],
) -> None:
    _print_line("nuvel doctor")
    _print_line(f"cwd: {cwd or '(no longer exists)'}")
    _print_line()
    for title, checks in sections:
        _print_line(title)
        for c in checks:
            _print_line(c.format())
        _print_line()
    if acp_pkg:
        _print_line("  Zed — add to settings.json (agent_servers):")
        for line in zed_config_snippet(cwd, acp_pkg).splitlines():
            _print_line(f"    {line}")
        _print_line()
    summary = _summarize(sections)
    _print_line("Summary")
    _print_line(f"  {summary['ok']} ok, {summary['warn']} warn, {summary['fail']} fail")


def run_doctor(cwd: Path | None = None, *, as_json: bool = False) -> int:
    try:
        target = (cwd or Path.cwd()).resolve()
    except OSError:  # The shell's cwd was deleted out from under it.
        target = None
    except RuntimeError:  # A symlink loop, before Python 3.13. check_target reports it.
        target = Path(os.path.abspath(cwd or "."))
    acp_pkg = detect_acp_package(target) if target else None
    sections = collect_sections(target, acp_pkg)
    code = 1 if _summarize(sections)["fail"] else 0
    if as_json:
        _print_json(target, sections, code, acp_pkg)
    else:
        _print_text(target, sections, acp_pkg)
    return code
