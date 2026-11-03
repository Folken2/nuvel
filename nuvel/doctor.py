"""nuvel doctor — diagnose nuvel install and the agent in cwd.

Inspired by `hermes doctor`. Prints a categorised checklist with
[OK] / [WARN] / [FAIL] markers and exits non-zero on any [FAIL]; every
[WARN] and [FAIL] carries one next step. `--json` emits the same findings
as structured output. Every check is wrapped so a single missing dependency
cannot abort the whole report.

A key can come from the process environment or a .env file, and the
environment wins: that is how load_dotenv() behaves at runtime. Secret values
are never printed, only the names of the variables that hold them.
"""

from __future__ import annotations

import contextlib
import http.client
import importlib
import importlib.metadata
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

_PLACEHOLDER_HINTS = ("your_", "_here", "changeme", "xxx", "todo")
_NETWORK_TIMEOUT = 5.0
_STALE_CACHE_DAYS = 30
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


# ── helpers ─────────────────────────────────────────────────────────


def _safe(fn, name: str, *args, **kwargs) -> Check:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - never crash doctor
        return Check(name, FAIL, f"check raised {type(exc).__name__}: {exc}", _REPORT_FIX)


def _safe_many(fn, name: str, *args, **kwargs) -> list[Check]:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - never crash doctor
        return [Check(name, FAIL, f"check raised {type(exc).__name__}: {exc}", _REPORT_FIX)]


def parse_env_file(path: Path) -> dict[str, str]:
    """Best-effort .env parser, using python-dotenv's own grammar when installed."""
    env: dict[str, str] = {}
    if not path.is_file():
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
    if not value:
        return True
    low = value.lower()
    return any(hint in low for hint in _PLACEHOLDER_HINTS)


# ── install checks ──────────────────────────────────────────────────


def _source_root() -> Optional[Path]:
    """The repo root when nuvel runs from a source checkout, else None."""
    root = Path(__file__).resolve().parent.parent
    return root if (root / "pyproject.toml").is_file() else None


def _reinstall_fix() -> str:
    root = _source_root()
    if root:
        return f"Reinstall from source: {sys.executable} -m pip install -e {root}"
    if "pipx" in Path(sys.prefix).parts:
        return "Reinstall: pipx reinstall nuvel-cli"
    return f"Reinstall: {sys.executable} -m pip install --force-reinstall nuvel-cli"


def check_python_version() -> Check:
    major, minor = sys.version_info[:2]
    detail = f"{major}.{minor}.{sys.version_info.micro}"
    if (major, minor) >= (3, 11):
        return Check("Python version", OK, detail)
    return Check(
        "Python version", FAIL, f"{detail} (need >= 3.11)",
        "Install Python 3.11 or newer, then reinstall: pipx install --python python3.11 nuvel-cli",
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
    if root:
        declared = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
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
    if not cwd.is_dir():
        return None
    for candidate in (cwd, *sorted(p for p in cwd.iterdir() if p.is_dir())):
        if (candidate / "config.yaml").is_file() and (candidate / "SOUL.md").is_file():
            return candidate
    return None


def detect_agent(cwd: Path) -> AgentInfo:
    info = AgentInfo()
    # A nuvel checkout looks like an ADK agent, but what runs from it is the
    # meta-agent, which has its own checks.
    if (cwd / "nuvel" / "doctor.py").is_file():
        return info
    profile = _find_hermes_profile(cwd)
    if profile:
        return AgentInfo(is_agent=True, framework="hermes", root=profile)
    req = cwd / "requirements.txt"
    has_entry = (
        (cwd / "agent.py").is_file()
        or (cwd / "server.py").is_file()
        or (cwd / "run_adk.py").is_file()
    )
    if not has_entry and cwd.is_dir():
        # ADK agents put agent.py inside a package subdir.
        for sub in cwd.iterdir():
            if sub.is_dir() and (sub / "agent.py").is_file():
                has_entry = True
                break
    if not req.is_file() or not has_entry:
        return info
    try:
        text = req.read_text(encoding="utf-8").lower()
    except OSError:
        return info
    info.is_agent = True
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


_PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider(
        "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/models",
        "https://openrouter.ai/keys",
    ),
    "anthropic": Provider(
        "ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/models",
        "https://console.anthropic.com/settings/keys",
    ),
    "openai": Provider(
        "OPENAI_API_KEY", "https://api.openai.com/v1/models",
        "https://platform.openai.com/api-keys",
    ),
    "groq": Provider(
        "GROQ_API_KEY", "https://api.groq.com/openai/v1/models",
        "https://console.groq.com/keys",
    ),
    "gemini": Provider(
        "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/",
        "https://aistudio.google.com/apikey",
    ),
    "ollama": Provider(None, "http://localhost:11434/"),
}

# Frameworks pinned to one provider; ADK and Hermes read theirs from config.
_FIXED_PROVIDERS: dict[str, str] = {
    "claude-agent-sdk": "anthropic",
    "anthropic-managed-agents": "anthropic",
}

_GATEWAY_KEYS: dict[str, tuple[str, ...]] = {
    "slack": ("SLACK_BOT_TOKEN", "COMPOSIO_WEBHOOK_SECRET"),
    "telegram": ("TELEGRAM_BOT_TOKEN",),
    "teams_bridge": (
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET",
    ),
}


def _check_env_key(key: str, env: dict[str, str], fix: str = "") -> Check:
    fix = fix or f"Add {key}=<value> to .env"
    if key not in env:
        return Check(key, FAIL, "not set in .env or the environment", fix)
    if _is_placeholder(env[key]):
        return Check(key, FAIL, "empty or still a placeholder", fix)
    return Check(key, OK, "set")


def _find_env_file(start: Path) -> Optional[Path]:
    """Find the .env load_dotenv() would load for code living in ``start``."""
    for directory in (start, *start.parents):
        if (directory / ".env").is_file():
            return directory / ".env"
    return None


def _effective_env(env_file: Optional[Path]) -> dict[str, str]:
    env = parse_env_file(env_file) if env_file else {}
    return {**env, **os.environ}


def check_env_file_valid(path: Path) -> Check:
    """Line numbers only: a malformed line may well hold a secret."""
    name = ".env file"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Check(name, FAIL, f"unreadable: {exc.strerror}", f"Fix its permissions: chmod u+r {path}")
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
    return model.split("/", 1)[0] if "/" in model else model


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


def check_hermes_config(profile: Path) -> tuple[Check, Optional[str]]:
    """Validate a Hermes config.yaml; return the check and its model.provider."""
    name = "config.yaml"
    path = profile / "config.yaml"
    try:
        import yaml
    except ImportError:
        return Check(name, WARN, "cannot validate without pyyaml", _reinstall_fix()), None
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        where = getattr(exc, "problem_mark", None)
        at = f" at line {where.line + 1}" if where else ""
        return Check(name, FAIL, f"cannot parse{at}", f"Fix the YAML in {path}"), None
    model = config.get("model") if isinstance(config, dict) else None
    provider = model.get("provider") if isinstance(model, dict) else None
    if not provider:
        return Check(
            name, FAIL, "model.provider is not set",
            f"Set it: add `provider: openrouter` under `model:` in {path}",
        ), None
    return Check(name, OK, str(path)), str(provider)


def _provider_key_checks(providers: Iterable[str], env: dict[str, str], target: str) -> list[Check]:
    """One check per API key the configured providers need."""
    checks: list[Check] = []
    for name in providers:
        provider = _PROVIDERS.get(name)
        if provider is None:
            checks.append(Check(
                f"provider {name}", WARN, "doctor does not know which key it needs",
                "Check that provider's docs and confirm its key is set",
            ))
        elif provider.key_env:
            where = f" (get one at {provider.keys_page})" if provider.keys_page else ""
            fix = f"{target.format(key=provider.key_env)}{where}"
            checks.append(_safe(_check_env_key, provider.key_env, provider.key_env, env, fix))
    return checks


def check_provider_reachable(name: str, provider: Provider, timeout: float = _NETWORK_TIMEOUT) -> Check:
    check_name = f"{name} reachable"
    request = urllib.request.Request(provider.url, method="HEAD")
    fix = (
        "Start it: ollama serve" if name == "ollama" else
        f"Check your connection or proxy (HTTPS_PROXY), then retry: curl -I {provider.url}"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Check(check_name, OK, f"{provider.url} (HTTP {response.status})")
    except urllib.error.HTTPError as exc:
        # No credentials were sent, so 401/403/404/405 still prove the route works.
        if exc.code >= 500:
            return Check(
                check_name, WARN, f"{provider.url} answered HTTP {exc.code}",
                f"Likely a provider outage; retry shortly: curl -I {provider.url}",
            )
        return Check(check_name, OK, f"{provider.url} (HTTP {exc.code})")
    except urllib.error.URLError as exc:
        return Check(check_name, FAIL, f"{provider.url}: {exc.reason}", fix)
    except (OSError, http.client.HTTPException) as exc:
        return Check(check_name, FAIL, f"{provider.url}: {exc}", fix)


def run_network_checks(providers: Iterable[str]) -> list[Check]:
    checks = [
        _safe(check_provider_reachable, f"{name} reachable", name, _PROVIDERS[name])
        for name in providers if name in _PROVIDERS
    ]
    return checks or [Check(
        "provider reachable", WARN, "no known provider configured, nothing probed",
        "Confirm your provider's endpoint responds: curl -I <its base URL>",
    )]


# ── state and caches ────────────────────────────────────────────────


def check_writable(label: str, path: Path, fix: str = "") -> Check:
    """Probe the path, or the ancestor it would be created in; doctor creates no dirs."""
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    if not target.is_dir():
        return Check(label, FAIL, f"{target} is not a directory", f"Remove or move {target}")
    try:
        with tempfile.TemporaryFile(dir=target):
            pass
    except OSError as exc:
        return Check(
            label, FAIL, f"{target} is not writable ({exc.strerror})",
            fix or f"Make it writable: chmod u+w {target}",
        )
    return Check(label, OK, str(path))


def _data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def run_state_checks(cwd: Path, info: AgentInfo, env: dict[str, str]) -> list[Check]:
    trace_dir = cwd / env.get("TRACE_DIR", "traces")
    checks = [_safe(
        check_writable, "traces dir", "traces dir", trace_dir,
        f"Run from a writable directory, or set TRACE_DIR to one (now {trace_dir})",
    )]
    if not info.is_agent:
        output = cwd / env.get("AGENTS_OUTPUT_DIR", "generated-agents")
        checks.append(_safe(
            check_writable, "agents output dir", "agents output dir", output,
            f"Run from a writable directory, or set AGENTS_OUTPUT_DIR to one (now {output})",
        ))
    home = Path.home()
    for label, path in (
        ("~/.nuvel", home / ".nuvel"),
        ("~/.cache/nuvel", home / ".cache" / "nuvel"),
        ("nuvel data dir", _data_home() / "nuvel"),
    ):
        checks.append(_safe(check_writable, label, label, path))
    return checks


def check_scored_cache(label: str, path: Path) -> Check:
    if not path.is_dir():
        return Check(label, OK, "empty")
    cutoff = time.time() - _STALE_CACHE_DAYS * 86400
    entries = list(path.rglob("*.json"))
    stale = sum(1 for entry in entries if entry.stat().st_mtime < cutoff)
    if stale:
        return Check(
            label, WARN, f"{stale} of {len(entries)} entries older than {_STALE_CACHE_DAYS} days",
            f"Clear it (examples are re-scored on the next run): rm -rf {path}",
        )
    return Check(label, OK, f"{len(entries)} entries")


def check_skills_hub_clone(path: Path) -> Check:
    label = "skills hub clone"
    git_dir = path / ".git"
    if not git_dir.is_dir():
        return Check(label, OK, "not cloned")
    marker = git_dir / "FETCH_HEAD" if (git_dir / "FETCH_HEAD").is_file() else git_dir / "HEAD"
    age_days = int((time.time() - marker.stat().st_mtime) / 86400)
    if age_days > _STALE_CACHE_DAYS:
        return Check(
            label, WARN, f"last fetched {age_days} days ago",
            f"Update it: git -C {path} pull --ff-only",
        )
    return Check(label, OK, f"fetched {age_days} days ago")


def run_cache_checks() -> list[Check]:
    data = _data_home() / "nuvel"
    return [
        _safe(check_scored_cache, "evalv2 sample cache", "evalv2 sample cache",
              Path.home() / ".cache" / "nuvel" / "evalv2"),
        _safe(check_scored_cache, "evalv2 results cache", "evalv2 results cache",
              data / "evalv2" / "cache"),
        _safe(check_skills_hub_clone, "skills hub clone", data / "skills"),
    ]


def _check_docker_running() -> Check:
    start_fix = "Only needed to build the image; start Docker: sudo systemctl start docker"
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
        return Check("Docker available", WARN, f"failed: {exc}", start_fix)
    if r.returncode == 0:
        return Check("Docker available", OK, "daemon reachable")
    return Check("Docker available", WARN, "daemon not reachable", start_fix)


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
    for sub in cwd.iterdir() if cwd.is_dir() else []:
        if sub.is_dir() and (sub / "plugins").is_dir():
            return sub / "plugins"
    return None


from nuvel._defaults import DEFAULT_FAST_MODEL, DEFAULT_REASONING_MODEL

_MODEL_DEFAULTS = {
    "FAST_MODEL": DEFAULT_FAST_MODEL,
    "REASONING_MODEL": DEFAULT_REASONING_MODEL,
}


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
    if not pricing_path.is_file():
        return [Check(
            "pricing.json coverage", WARN, f"missing {pricing_path}",
            "Costs stay null without it; copy it from a fresh `nuvel agent create`",
        )]

    try:
        raw = json.loads(pricing_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Check(
            "pricing.json coverage", FAIL, f"parse error: {exc}",
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
                f"Add it: nuvel pricing add {model.removeprefix('openrouter/')}",
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


def _required_keys(providers: Iterable[str]) -> list[str]:
    known = (_PROVIDERS[name] for name in providers if name in _PROVIDERS)
    return [p.key_env for p in known if p.key_env]


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
    if root and (root / ".env.example").is_file():
        missing_fix = f"Create it: cp {root}/.env.example {root}/.env"
    else:
        prefix = Path(sys.prefix).resolve()
        home = prefix if pkg.is_relative_to(prefix) else None
        missing_fix = (
            f"Create it: echo '{example_key}=<your key>' >> {home}/.env" if home
            else f"Skip the file and export the key instead: export {example_key}=<your key>"
        )
    checks = [_config_file_check(env_file, keys, env, missing_fix)]
    keys_checks = _provider_key_checks(providers, env, "Set it: export {key}=<your key>")
    ignored = parse_env_file(cwd / ".env") if env_file != cwd / ".env" else {}
    for check in keys_checks:
        if check.status == FAIL and check.name in ignored:
            check.detail += "; ./.env sets it, but `nuvel run` does not read that file"
    return checks + keys_checks


def run_agent_checks(cwd: Path, info: AgentInfo) -> list[Check]:
    checks: list[Check] = []
    root = info.root or cwd
    env_path = _find_env_file(root)
    env = _effective_env(env_path)

    hermes_provider = None
    if info.framework == "hermes":
        config_check, hermes_provider = check_hermes_config(root)
        checks.append(config_check)
    providers = configured_providers(info, env, hermes_provider)

    example = root / ".env.example"
    missing_fix = (
        f"Create it: cp {example} {root}/.env" if example.is_file()
        else f"Create {root}/.env with the keys listed below"
    )
    checks.append(_config_file_check(env_path, _required_keys(providers), env, missing_fix))
    target = f"Add {{key}}=<your key> to {root}/.env"
    checks.extend(_provider_key_checks(providers, env, target))
    if not providers and info.framework != "hermes":
        checks.append(Check(
            "API keys", WARN, f"not checked for framework {info.framework or 'unknown'}",
            "Compare your .env against this agent's .env.example",
        ))

    if _composio_in_use(cwd):
        checks.append(_safe(
            _check_env_key, "COMPOSIO_API_KEY", "COMPOSIO_API_KEY", env,
            f"Add COMPOSIO_API_KEY=<value> to {root}/.env",
        ))

    gateways_dir = cwd / "gateways"
    if not gateways_dir.is_dir():
        # Templates put gateways/ inside the package dir.
        for sub in cwd.iterdir() if cwd.is_dir() else []:
            cand = sub / "gateways"
            if cand.is_dir():
                gateways_dir = cand
                break
    if gateways_dir.is_dir():
        for stem, keys in _GATEWAY_KEYS.items():
            if (gateways_dir / f"{stem}.py").is_file():
                for key in keys:
                    check = _safe(_check_env_key, key, key, env, f"Add {key}=<value> to {root}/.env")
                    check.name = f"gateway:{stem} {key}"
                    checks.append(check)

    if (cwd / "Dockerfile").is_file():
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
    if not cwd.is_dir():
        return None
    for sub in sorted(cwd.iterdir()):
        if sub.is_dir() and (sub / "acp" / "__main__.py").is_file():
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
    deps_fix = f"Install the agent's deps: {sys.executable} -m pip install -r {cwd}/requirements.txt"
    debug_fix = f"Run it directly to see the error: cd {cwd} && {' '.join(cmd)}"
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


def collect_sections(cwd: Path) -> list[tuple[str, list[Check]]]:
    """Run every check. Output is captured so a chatty import can't corrupt --json."""
    with contextlib.redirect_stdout(sys.stderr):
        sections = [("Install", run_install_checks())]
        if not cwd.is_dir():
            sections.append(("Target", [Check(
                "directory", FAIL, f"{cwd} does not exist",
                "Pass an existing directory: nuvel doctor --path <dir>",
            )]))
            return sections

        info = detect_agent(cwd)
        hermes_provider = None
        if info.is_agent:
            root = info.root or cwd
            env = _effective_env(_find_env_file(root))
            sections.append((
                f"Agent ({info.framework or 'unknown'})",
                _safe_many(run_agent_checks, "agent config", cwd, info),
            ))
            if info.framework == "hermes":
                _, hermes_provider = check_hermes_config(root)
        else:
            env_file = _find_env_file(Path(__file__).resolve().parent)
            env = _effective_env(env_file)
            sections.append((
                "Configuration (nuvel run; no generated agent in this directory)",
                _safe_many(run_meta_checks, "nuvel run config", cwd, env_file, env),
            ))

        providers = configured_providers(info, env, hermes_provider)
        sections.append(("State", _safe_many(run_state_checks, "state dirs", cwd, info, env)))
        sections.append(("Network", _safe_many(run_network_checks, "provider network", providers)))
        sections.append(("Caches", _safe_many(run_cache_checks, "caches")))

        acp_pkg = detect_acp_package(cwd)
        if acp_pkg:
            sections.append(("ACP adapter", run_acp_checks(cwd, acp_pkg)))
    return sections


def _summarize(sections: list[tuple[str, list[Check]]]) -> dict[str, int]:
    statuses = [c.status for _, checks in sections for c in checks]
    return {
        "ok": statuses.count(OK),
        "warn": statuses.count(WARN),
        "fail": statuses.count(FAIL),
    }


def _print_json(cwd: Path, sections: list[tuple[str, list[Check]]], code: int) -> None:
    report: dict = {
        "ok": code == 0,
        "exit_code": code,
        "cwd": str(cwd),
        "summary": _summarize(sections),
        "checks": [
            {"section": title, "name": c.name, "status": c.status, "detail": c.detail, "fix": c.fix}
            for title, checks in sections for c in checks
        ],
    }
    acp_pkg = detect_acp_package(cwd)
    if acp_pkg:
        report["zed_config"] = json.loads(zed_config_snippet(cwd, acp_pkg))
    print(json.dumps(report, indent=2))


def _print_text(cwd: Path, sections: list[tuple[str, list[Check]]]) -> None:
    print("nuvel doctor")
    print(f"cwd: {cwd}")
    print()
    for title, checks in sections:
        print(title)
        for c in checks:
            print(c.format())
        print()
    acp_pkg = detect_acp_package(cwd)
    if acp_pkg:
        print("  Zed — add to settings.json (agent_servers):")
        for line in zed_config_snippet(cwd, acp_pkg).splitlines():
            print(f"    {line}")
        print()
    summary = _summarize(sections)
    print("Summary")
    print(f"  {summary['ok']} ok, {summary['warn']} warn, {summary['fail']} fail")


def run_doctor(cwd: Path | None = None, *, as_json: bool = False) -> int:
    cwd = (cwd or Path.cwd()).resolve()
    sections = collect_sections(cwd)
    code = 1 if _summarize(sections)["fail"] else 0
    if as_json:
        _print_json(cwd, sections, code)
    else:
        _print_text(cwd, sections)
    return code
