# Meta-Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the data-analysis-agent skeleton into a meta-agent that creates production-ready Google ADK agents from natural language descriptions.

**Architecture:** The meta-agent is itself an ADK LlmAgent. It uses SkillToolset for ADK knowledge (5 skills with L3 references) and FunctionTools for file operations (scaffold, write, read, list, validate). A `scaffold.py` script stamps out a sanitized copy of the data-analysis-agent infrastructure, and the meta-agent fills in the brain (prompts, tools, skills).

**Tech Stack:** google-adk 1.26.0, LiteLLM (OpenRouter), FastAPI, Python 3.12+

**Source reference:** The data-analysis-agent lives at `data-analysis-agent/data_analysis_agent/`. All "copy from" references point there.

---

## File Structure

### New files to create:

```
meta-agent/
├── meta_agent/
│   ├── __init__.py
│   ├── agent.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── llm.py
│   │   └── logging.py
│   ├── plugins/
│   │   └── __init__.py
│   ├── callbacks/
│   │   └── __init__.py
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── resilience.py
│   │   └── date_utils.py
│   ├── state/
│   │   ├── __init__.py
│   │   └── query_cache.py
│   ├── prompt/
│   │   ├── __init__.py
│   │   └── instructions.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── file_tools.py
│   │   ├── scaffold_tool.py
│   │   └── validate_tool.py
│   ├── skills/
│   │   ├── adk-agent-patterns/
│   │   │   ├── SKILL.md
│   │   │   └── references/
│   │   │       ├── loop-patterns.md
│   │   │       ├── parallel-patterns.md
│   │   │       └── multi-agent-patterns.md
│   │   ├── adk-prompt-engineering/
│   │   │   ├── SKILL.md
│   │   │   └── references/
│   │   │       ├── prompt-templates.md
│   │   │       ├── instruction-provider-pattern.md
│   │   │       └── state-placeholders.md
│   │   ├── adk-tool-creation/
│   │   │   ├── SKILL.md
│   │   │   └── references/
│   │   │       ├── tool-patterns.md
│   │   │       ├── tool-context-api.md
│   │   │       └── async-tool-examples.md
│   │   ├── adk-skill-creation/
│   │   │   ├── SKILL.md
│   │   │   └── references/
│   │   │       ├── skill-spec.md
│   │   │       ├── example-skills.md
│   │   │       └── skilltoolset-wiring.md
│   │   └── adk-callbacks-hitl/
│   │       ├── SKILL.md
│   │       └── references/
│   │           ├── callback-signatures.md
│   │           ├── hitl-patterns.md
│   │           └── state-management.md
│   └── templates/
│       ├── run_adk.py
│       ├── requirements.txt
│       ├── .env.example
│       ├── README.md.tmpl
│       └── {{agent_package}}/
│           ├── __init__.py.tmpl
│           ├── agent.py.tmpl
│           ├── config/
│           │   ├── __init__.py
│           │   ├── llm.py
│           │   └── logging.py
│           ├── plugins/
│           │   ├── __init__.py.tmpl
│           │   ├── console_logger_plugin.py
│           │   ├── resilience_plugin.py
│           │   ├── cache_plugin.py
│           │   ├── tool_events.py
│           │   └── trace_plugin.py
│           ├── callbacks/
│           │   └── __init__.py
│           ├── utils/
│           │   ├── __init__.py
│           │   ├── resilience.py
│           │   └── date_utils.py
│           ├── state/
│           │   ├── __init__.py
│           │   └── query_cache.py
│           ├── prompt/
│           │   ├── __init__.py
│           │   └── instructions.py.tmpl
│           ├── tools/
│           │   └── __init__.py.tmpl
│           ├── skills/
│           │   └── .gitkeep
│           └── contexts/
│               └── .gitkeep
├── scaffold.py
├── run_adk.py
├── requirements.txt
├── .env.example
└── tests/
    ├── test_scaffold.py
    ├── test_file_tools.py
    └── test_validate.py
```

### Key design decisions:

- **Templates use `.tmpl` suffix** for files that need package-name substitution. Non-`.tmpl` files are copied as-is.
- **The `{{agent_package}}` directory** is renamed to the snake_case agent name during scaffolding.
- **Plugins stripped from template:** `security_plugin.py` (Postgres-specific), `query_analysis_plugin.py` (DB-specific). The `ReflectAndRetryToolPlugin` from ADK is kept.
- **Template plugins reference generic tool names** — the resilience plugin uses a configurable `PROTECTED_TOOLS` set instead of hardcoded DB tool names.

---

### Task 1: Project Foundation — Config, Utils, State

**Files:**
- Create: `meta_agent/__init__.py`
- Create: `meta_agent/config/__init__.py`
- Create: `meta_agent/config/llm.py`
- Create: `meta_agent/config/logging.py`
- Create: `meta_agent/utils/__init__.py`
- Create: `meta_agent/utils/resilience.py`
- Create: `meta_agent/utils/date_utils.py`
- Create: `meta_agent/state/__init__.py`
- Create: `meta_agent/state/query_cache.py`
- Create: `meta_agent/callbacks/__init__.py`
- Create: `meta_agent/plugins/__init__.py`
- Create: `requirements.txt`
- Create: `.env.example`

- [ ] **Step 1: Create directory structure**

```bash
cd /Users/albertfolch/Documents/Cursor/meta-agent
mkdir -p meta_agent/{config,utils,state,callbacks,plugins,prompt,tools,skills,templates}
```

- [ ] **Step 2: Create `meta_agent/__init__.py`**

```python
from . import agent
```

- [ ] **Step 3: Create `meta_agent/config/__init__.py`**

```python
```

(Empty file)

- [ ] **Step 4: Create `meta_agent/config/llm.py`**

Copy from `data-analysis-agent/data_analysis_agent/config/llm.py` with these changes:
- Update `HTTP-Referer` to point to meta-agent repo
- Update `X-Title` to `meta-agent`
- Default `FAST_MODEL` stays `openrouter/moonshotai/kimi-k2.5` (good for code generation)

```python
"""
LLM configuration for the Meta-Agent.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

import litellm
from google.adk.models.lite_llm import LiteLlm

logger = logging.getLogger(__name__)

# Retry configuration for transient errors
litellm.num_retries = int(os.getenv("LLM_NUM_RETRIES", "3"))
litellm.request_timeout = int(os.getenv("LLM_REQUEST_TIMEOUT", "120"))
litellm.drop_params = True

_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/albertfolch-renal/meta-agent",
    "X-Title": "meta-agent",
}

FAST_MODEL = LiteLlm(
    model=os.getenv("FAST_MODEL", "openrouter/moonshotai/kimi-k2.5"),
    extra_headers=_OPENROUTER_HEADERS,
)

REASONING_MODEL = LiteLlm(
    model=os.getenv("REASONING_MODEL", "openrouter/google/gemini-3-pro-preview"),
    extra_headers=_OPENROUTER_HEADERS,
)
```

- [ ] **Step 5: Create `meta_agent/config/logging.py`**

Copy verbatim from `data-analysis-agent/data_analysis_agent/config/logging.py`. No changes needed — it's generic.

- [ ] **Step 6: Create `meta_agent/utils/__init__.py`**

```python
```

(Empty file)

- [ ] **Step 7: Create `meta_agent/utils/resilience.py`**

Copy verbatim from `data-analysis-agent/data_analysis_agent/utils/resilience.py`. The singleton names `db_circuit` and `tool_rate_limiter` are fine — the meta-agent's tools will also benefit from circuit breaking and rate limiting.

- [ ] **Step 8: Create `meta_agent/utils/date_utils.py`**

Copy verbatim from `data-analysis-agent/data_analysis_agent/utils/date_utils.py`.

- [ ] **Step 9: Create `meta_agent/state/__init__.py`**

```python
```

(Empty file)

- [ ] **Step 10: Create `meta_agent/state/query_cache.py`**

Copy verbatim from `data-analysis-agent/data_analysis_agent/state/query_cache.py`. The cache works for any tool, not just DB queries.

- [ ] **Step 11: Create `meta_agent/callbacks/__init__.py`**

```python
```

(Empty file — no callbacks needed for the meta-agent itself)

- [ ] **Step 12: Create `meta_agent/plugins/__init__.py`**

Adapted from data-analysis-agent — remove DB-specific plugins (security, query_analysis), keep everything else. Update import paths from `data_analysis_agent` to `meta_agent`.

```python
"""
Plugins for the Meta-Agent.

Uses Google ADK's plugin system (BasePlugin) for cross-cutting concerns:
caching, resilience, tracing, and error recovery.

Inherits the production plugin chain from the data-analysis-agent,
minus DB-specific plugins (security, query_analysis).
"""

import os

from google.adk.plugins.context_filter_plugin import ContextFilterPlugin
from google.adk.plugins.reflect_retry_tool_plugin import (
    ReflectAndRetryToolPlugin,
    TrackingScope,
)

# Import plugins that are generic (not DB-specific)
# These are copied from data-analysis-agent with no changes
from .console_logger_plugin import ConsoleLoggerPlugin
from .resilience_plugin import ResiliencePlugin
from .cache_plugin import CachePlugin
from .tool_events import ToolEventsPlugin
from .trace_plugin import TracePlugin

# ── Pre-configured instances ─────────────────────────────────────────

trace = TracePlugin()
context_filter = ContextFilterPlugin(
    num_invocations_to_keep=int(os.getenv("CONTEXT_FILTER_KEEP", "10")),
)
console_logger = ConsoleLoggerPlugin()
tool_events = ToolEventsPlugin()
resilience = ResiliencePlugin()
cache = CachePlugin()
self_healing = ReflectAndRetryToolPlugin(
    name="self_healing",
    max_retries=3,
    throw_exception_if_retry_exceeded=False,
    tracking_scope=TrackingScope.INVOCATION,
)

PLUGIN_PATHS = [
    "meta_agent.plugins.trace",
    "meta_agent.plugins.context_filter",
    "meta_agent.plugins.console_logger",
    "meta_agent.plugins.tool_events",
    "meta_agent.plugins.resilience",
    "meta_agent.plugins.cache",
    "meta_agent.plugins.self_healing",
]

__all__ = [
    "ConsoleLoggerPlugin",
    "CachePlugin",
    "ResiliencePlugin",
    "ToolEventsPlugin",
    "TracePlugin",
    "PLUGIN_PATHS",
]
```

- [ ] **Step 13: Copy plugin files from data-analysis-agent**

Copy these files verbatim to `meta_agent/plugins/`:
- `console_logger_plugin.py`
- `tool_events.py`
- `trace_plugin.py`
- `cache_plugin.py`

For `resilience_plugin.py`, copy but change `_DB_TOOLS` to a generic set and update the import path:

```python
"""
Resilience plugin: circuit breaker + rate limiting for tool calls.

Runs early in the plugin chain to fail fast when the system is overloaded.
"""

import logging
import os
from typing import Any, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from ..utils.resilience import db_circuit, tool_rate_limiter

logger = logging.getLogger(__name__)

# Tools that hit external services (configurable via env)
_PROTECTED_TOOLS_STR = os.getenv("PROTECTED_TOOLS", "scaffold_agent,validate_agent")
_PROTECTED_TOOLS = set(t.strip() for t in _PROTECTED_TOOLS_STR.split(",") if t.strip())


class ResiliencePlugin(BasePlugin):
    """Applies circuit breaker and rate limiting to tool calls."""

    def __init__(self) -> None:
        super().__init__(name="resilience")

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Optional[dict]:
        if not tool_rate_limiter.allow():
            logger.warning("Rate limit exceeded for tool: %s", tool.name)
            return {
                "status": "error",
                "error": "Rate limit exceeded",
                "message": "Too many requests. Please wait a moment before trying again.",
            }

        if tool.name in _PROTECTED_TOOLS:
            if not db_circuit.allow_request():
                return {
                    "status": "error",
                    "error": "Service temporarily unavailable",
                    "message": "The service is temporarily unavailable due to repeated errors.",
                }

        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict,
    ) -> Optional[dict]:
        if tool.name in _PROTECTED_TOOLS:
            if isinstance(result, dict) and result.get("status") == "error":
                error = result.get("error", "")
                if any(kw in str(error).lower() for kw in [
                    "connection", "timeout", "refused", "unavailable", "permission",
                ]):
                    db_circuit.record_failure()
            else:
                db_circuit.record_success()

        return None
```

For `cache_plugin.py`, copy but change `CACHEABLE_TOOLS`:

```python
"""
Cache plugin for the Meta-Agent.

Caches successful tool responses with TTL expiration.
Uses tool_context.state for session-scoped persistence.
"""

import logging
from typing import Any, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from ..state.query_cache import cache_get, cache_set

logger = logging.getLogger(__name__)

# Tools whose responses can be cached
CACHEABLE_TOOLS = {"read_file", "list_files", "validate_agent"}


class CachePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="cache")

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Optional[dict]:
        if tool.name not in CACHEABLE_TOOLS:
            return None

        cached = cache_get(tool_context.state, tool.name, tool_args)
        if cached:
            logger.info("Cache HIT: %s", tool.name)
            return cached

        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict,
    ) -> Optional[dict]:
        if tool.name not in CACHEABLE_TOOLS:
            return None

        if isinstance(result, dict) and result.get("status") != "error" and not result.get("_cached"):
            try:
                cache_set(tool_context.state, tool.name, tool_args, result)
            except Exception as e:
                logger.warning("Failed to cache: %s", e)

        return None
```

- [ ] **Step 14: Create `requirements.txt`**

```
google-adk==1.26.0
litellm>=1.82.0,<2.0.0
python-dotenv>=1.0.0,<2.0.0
fastapi>=0.135.0,<1.0.0
uvicorn>=0.40.0,<1.0.0
```

- [ ] **Step 15: Create `.env.example`**

```bash
# Required: OpenRouter API key for LLM access
OPENROUTER_API_KEY=your_key_here

# LLM model (default: openrouter/moonshotai/kimi-k2.5)
# FAST_MODEL=openrouter/moonshotai/kimi-k2.5
# REASONING_MODEL=openrouter/google/gemini-3-pro-preview

# Output directory for generated agents (default: ./generated-agents)
# AGENTS_OUTPUT_DIR=./generated-agents

# Server configuration
# PORT=8000
# DEV_MODE=true

# Session persistence (production only)
# SESSION_SERVICE_URI=postgresql://user:pass@host/db

# API authentication
# API_KEY=your_api_key

# Logging
# LOG_FORMAT=text
# LOG_LEVEL=INFO

# Plugin tuning
# CONTEXT_FILTER_KEEP=10
# TOOL_RATE_LIMIT=5.0
# TOOL_RATE_BURST=20
# CACHE_TTL_SECONDS=300
# CACHE_MAX_SIZE=10
```

- [ ] **Step 16: Commit**

```bash
git add meta_agent/ requirements.txt .env.example
git commit -m "feat: add meta-agent project foundation

Config, utils, state, plugins adapted from data-analysis-agent.
Stripped DB-specific plugins (security, query_analysis).
Kept: trace, console_logger, resilience, cache, tool_events, self_healing."
```

---

### Task 2: Template Skeleton

Create the sanitized template directory that scaffold.py will copy for every new agent.

**Files:**
- Create: `meta_agent/templates/run_adk.py`
- Create: `meta_agent/templates/requirements.txt`
- Create: `meta_agent/templates/.env.example`
- Create: `meta_agent/templates/README.md.tmpl`
- Create: `meta_agent/templates/{{agent_package}}/` and all sub-files

- [ ] **Step 1: Create template directory structure**

```bash
cd /Users/albertfolch/Documents/Cursor/meta-agent
mkdir -p "meta_agent/templates/{{agent_package}}"/{config,plugins,callbacks,utils,state,prompt,tools,skills,contexts}
```

- [ ] **Step 2: Create `meta_agent/templates/run_adk.py`**

Adapted from `data-analysis-agent/run_adk.py`. Replace all `data_analysis_agent` references with `{{agent_package}}` placeholders. Remove DB health check, Composio check, Supabase check. Keep the structure identical.

```python
"""
ADK entrypoint for {{agent_name}}.

Usage:
  Development: DEV_MODE=true python run_adk.py
  Production:  python run_adk.py  (requires SESSION_SERVICE_URI)
"""

import os
import secrets
import socket
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from google.adk.cli.fast_api import get_fast_api_app
from {{agent_package}}.plugins import PLUGIN_PATHS
from {{agent_package}}.config.logging import setup_logging, generate_request_id, request_id_var

try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass


class APIKeyMiddleware(BaseHTTPMiddleware):
    PUBLIC_PREFIXES = ("/health", "/favicon.ico")

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/" or any(path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        else:
            token = request.headers.get("X-API-Key", "")
        if not secrets.compare_digest(token, self.api_key):
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "message": "Invalid or missing API key"},
            )
        return await call_next(request)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID", generate_request_id())
        request_id_var.set(rid)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


def add_endpoints(app: FastAPI) -> None:
    @app.get("/health")
    async def health_check():
        return JSONResponse(content={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "{{agent_name}}",
            "status": "healthy",
        })

    @app.get("/")
    async def root():
        return JSONResponse(content={
            "message": "{{agent_name}} API",
            "endpoints": {
                "/health": "Health check",
                "/run/": "Non-streaming agent execution (POST)",
                "/run_sse/": "Streaming agent execution (POST)",
            },
        })

    print("[ADK] Endpoints added: /health, /")


def main() -> None:
    setup_logging()
    agents_dir = os.getenv("AGENTS_DIR", ".")
    dev_mode = os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes")
    port = int(os.getenv("PORT", "8000"))

    print(f"[ADK] Starting server: PORT={port}, DEV_MODE={dev_mode}")

    if dev_mode:
        print("[ADK] DEVELOPMENT mode (in-memory sessions)")
        app = get_fast_api_app(
            agents_dir=agents_dir,
            session_service_uri=None,
            use_local_storage=False,
            web=False,
            a2a=False,
            host="",
            port=port,
            url_prefix=None,
            reload_agents=True,
            extra_plugins=PLUGIN_PATHS,
        )
    else:
        session_uri = os.getenv("SESSION_SERVICE_URI")
        if not session_uri:
            raise RuntimeError("SESSION_SERVICE_URI is required in production mode.")
        session_uri = _normalize_to_asyncpg_uri(session_uri)
        connect_args = {"ssl": "require"}
        print("[ADK] PRODUCTION mode")
        app = get_fast_api_app(
            agents_dir=agents_dir,
            session_service_uri=session_uri,
            session_db_kwargs={"connect_args": connect_args},
            web=False,
            a2a=False,
            host="",
            port=port,
            url_prefix=None,
            reload_agents=True,
            extra_plugins=PLUGIN_PATHS,
        )

    app.router.redirect_slashes = False
    app.add_middleware(RequestIDMiddleware)

    api_key = os.getenv("API_KEY")
    if api_key:
        app.add_middleware(APIKeyMiddleware, api_key=api_key)
        if not os.getenv("DOCS_ENABLED", "").lower() in ("true", "1", "yes"):
            app.openapi_url = None
            app.docs_url = None
            app.redoc_url = None
        print("[ADK] API key authentication enabled")
    else:
        print("[ADK] WARNING: No API_KEY set — endpoints are unauthenticated")

    add_endpoints(app)
    print(f"[ADK] Server ready: http://0.0.0.0:{port}")
    uvicorn.run(app, host="", port=port)


def _normalize_to_asyncpg_uri(uri: str) -> str:
    if uri.startswith("postgresql://"):
        uri = uri.replace("postgresql://", "postgresql+asyncpg://", 1)
    parsed = urlsplit(uri)
    qs = parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for (k, v) in qs if k.lower() not in {"sslmode", "channel_binding", "channelbinding"}]
    new_query = urlencode(filtered)
    return urlunsplit(parsed._replace(query=new_query))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create `meta_agent/templates/requirements.txt`**

```
google-adk==1.26.0
litellm>=1.82.0,<2.0.0
python-dotenv>=1.0.0,<2.0.0
fastapi>=0.135.0,<1.0.0
uvicorn>=0.40.0,<1.0.0
```

- [ ] **Step 4: Create `meta_agent/templates/.env.example`**

```bash
# Required: OpenRouter API key
OPENROUTER_API_KEY=your_key_here

# LLM model (default: openrouter/moonshotai/kimi-k2.5)
# FAST_MODEL=openrouter/moonshotai/kimi-k2.5

# Server
# PORT=8000
# DEV_MODE=true

# Production session persistence
# SESSION_SERVICE_URI=postgresql://user:pass@host/db

# API authentication
# API_KEY=your_api_key
```

- [ ] **Step 5: Create `meta_agent/templates/README.md.tmpl`**

```markdown
# {{agent_name}}

Generated by [meta-agent](https://github.com/albertfolch-renal/meta-agent).

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure env vars
cp .env.example .env

# Run in dev mode
DEV_MODE=true python run_adk.py

# Or use ADK web UI
DEV_MODE=true adk web .
```

## Structure

- `{{agent_package}}/agent.py` — Agent definition (tools + skills + prompt)
- `{{agent_package}}/prompt/instructions.py` — System prompt
- `{{agent_package}}/tools/` — Custom function tools
- `{{agent_package}}/skills/` — Domain skills (SKILL.md files)
- `{{agent_package}}/contexts/` — Domain knowledge files
- `{{agent_package}}/plugins/` — Production plugin chain
- `run_adk.py` — FastAPI server with auth, health checks, SSE
```

- [ ] **Step 6: Create template package files**

Create these files inside `meta_agent/templates/{{agent_package}}/`:

**`__init__.py.tmpl`:**
```python
from . import agent
```

**`agent.py.tmpl`:**
```python
"""
{{agent_name}} — generated by meta-agent.
"""

from __future__ import annotations

import logging
import pathlib

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.skills import load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset

from .config.llm import FAST_MODEL
from .tools import get_tools
from .prompt.instructions import get_agent_instruction

logger = logging.getLogger(__name__)

load_dotenv()

_SKILLS_DIR = pathlib.Path(__file__).parent / "skills"


def _build_skill_toolset() -> SkillToolset | None:
    """Load skills from the skills/ directory."""
    skills = []
    if not _SKILLS_DIR.is_dir():
        return None
    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
            try:
                skill = load_skill_from_dir(skill_dir)
                skills.append(skill)
                logger.info("Loaded skill: %s", skill.name)
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", skill_dir.name, e)
    if not skills:
        return None
    return SkillToolset(skills=skills)


def _build_tools():
    """Build agent tools: custom tools + skills."""
    tools = list(get_tools())
    skill_toolset = _build_skill_toolset()
    if skill_toolset:
        tools.append(skill_toolset)
    return tools


root_agent = LlmAgent(
    model=FAST_MODEL,
    name="{{agent_name_snake}}",
    description="{{agent_description}}",
    instruction=get_agent_instruction,
    tools=_build_tools(),
)
```

**`prompt/__init__.py`:**
```python
```

**`prompt/instructions.py.tmpl`:**
```python
"""
Agent instruction builder for {{agent_name}}.

Constructs the system prompt dynamically.
"""

import logging
from pathlib import Path

from ..utils.date_utils import get_current_date_info, format_current_date

logger = logging.getLogger(__name__)

_CONTEXTS_DIR = Path(__file__).parent.parent / "contexts"


def _load_context() -> str:
    """Load context files from the contexts/ directory."""
    if not _CONTEXTS_DIR.is_dir():
        return ""
    parts = []
    for ctx_file in sorted(_CONTEXTS_DIR.glob("*.md")):
        try:
            content = ctx_file.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
        except Exception as e:
            logger.warning("Failed to read context %s: %s", ctx_file.name, e)
    return "\n\n".join(parts)


async def get_agent_instruction(ctx) -> str:
    """Generate agent instruction. ADK InstructionProvider callback."""
    formatted_date = format_current_date()
    context = _load_context()

    context_block = ""
    if context:
        context_block = f"\n\n# Domain Knowledge\n\n{context}"

    return f"""{{agent_system_prompt}}

Today's date: {formatted_date}
{context_block}"""
```

**`tools/__init__.py.tmpl`:**
```python
"""
Tools for {{agent_name}}.
"""

import logging
from typing import List

logger = logging.getLogger(__name__)


def get_tools() -> List:
    """Get configured tools for the agent."""
    tools = []
    # Tools will be added here by the meta-agent
    return tools
```

- [ ] **Step 7: Copy infrastructure files into template package**

Copy these files verbatim from `data-analysis-agent/data_analysis_agent/` into `meta_agent/templates/{{agent_package}}/`:
- `config/__init__.py`
- `config/llm.py`
- `config/logging.py`
- `utils/__init__.py` (empty)
- `utils/resilience.py`
- `utils/date_utils.py`
- `state/__init__.py` (empty)
- `state/query_cache.py`
- `callbacks/__init__.py` (empty)
- `plugins/console_logger_plugin.py` (verbatim)
- `plugins/tool_events.py` (verbatim)
- `plugins/trace_plugin.py` (verbatim)

For `plugins/__init__.py.tmpl` — same as meta_agent's but with `{{agent_package}}` import paths:

```python
"""
Plugins for {{agent_name}}.
"""

import os

from google.adk.plugins.context_filter_plugin import ContextFilterPlugin
from google.adk.plugins.reflect_retry_tool_plugin import (
    ReflectAndRetryToolPlugin,
    TrackingScope,
)

from .console_logger_plugin import ConsoleLoggerPlugin
from .resilience_plugin import ResiliencePlugin
from .cache_plugin import CachePlugin
from .tool_events import ToolEventsPlugin
from .trace_plugin import TracePlugin

trace = TracePlugin()
context_filter = ContextFilterPlugin(
    num_invocations_to_keep=int(os.getenv("CONTEXT_FILTER_KEEP", "10")),
)
console_logger = ConsoleLoggerPlugin()
tool_events = ToolEventsPlugin()
resilience = ResiliencePlugin()
cache = CachePlugin()
self_healing = ReflectAndRetryToolPlugin(
    name="self_healing",
    max_retries=3,
    throw_exception_if_retry_exceeded=False,
    tracking_scope=TrackingScope.INVOCATION,
)

PLUGIN_PATHS = [
    "{{agent_package}}.plugins.trace",
    "{{agent_package}}.plugins.context_filter",
    "{{agent_package}}.plugins.console_logger",
    "{{agent_package}}.plugins.tool_events",
    "{{agent_package}}.plugins.resilience",
    "{{agent_package}}.plugins.cache",
    "{{agent_package}}.plugins.self_healing",
]
```

For `plugins/resilience_plugin.py` — generic version (same as meta_agent's resilience_plugin):

```python
"""
Resilience plugin: circuit breaker + rate limiting for tool calls.
"""

import logging
import os
from typing import Any, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from ..utils.resilience import db_circuit, tool_rate_limiter

logger = logging.getLogger(__name__)

_PROTECTED_TOOLS_STR = os.getenv("PROTECTED_TOOLS", "")
_PROTECTED_TOOLS = set(t.strip() for t in _PROTECTED_TOOLS_STR.split(",") if t.strip())


class ResiliencePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="resilience")

    async def before_tool_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext,
    ) -> Optional[dict]:
        if not tool_rate_limiter.allow():
            return {"status": "error", "error": "Rate limit exceeded"}
        if tool.name in _PROTECTED_TOOLS and not db_circuit.allow_request():
            return {"status": "error", "error": "Service temporarily unavailable"}
        return None

    async def after_tool_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext, result: dict,
    ) -> Optional[dict]:
        if tool.name in _PROTECTED_TOOLS:
            if isinstance(result, dict) and result.get("status") == "error":
                error = result.get("error", "")
                if any(kw in str(error).lower() for kw in ["connection", "timeout", "refused"]):
                    db_circuit.record_failure()
            else:
                db_circuit.record_success()
        return None
```

For `plugins/cache_plugin.py` — generic version:

```python
"""
Cache plugin — caches successful tool responses with TTL.
"""

import logging
from typing import Any, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from ..state.query_cache import cache_get, cache_set

logger = logging.getLogger(__name__)

CACHEABLE_TOOLS: set[str] = set()  # Configure per agent


class CachePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="cache")

    async def before_tool_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext,
    ) -> Optional[dict]:
        if tool.name not in CACHEABLE_TOOLS:
            return None
        cached = cache_get(tool_context.state, tool.name, tool_args)
        if cached:
            return cached
        return None

    async def after_tool_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext, result: dict,
    ) -> Optional[dict]:
        if tool.name not in CACHEABLE_TOOLS:
            return None
        if isinstance(result, dict) and result.get("status") != "error" and not result.get("_cached"):
            try:
                cache_set(tool_context.state, tool.name, tool_args, result)
            except Exception as e:
                logger.warning("Failed to cache: %s", e)
        return None
```

- [ ] **Step 8: Create .gitkeep files**

```bash
touch meta_agent/templates/{{agent_package}}/skills/.gitkeep
touch meta_agent/templates/{{agent_package}}/contexts/.gitkeep
```

- [ ] **Step 9: Commit**

```bash
git add meta_agent/templates/
git commit -m "feat: add agent template skeleton

Sanitized from data-analysis-agent: keeps plugins, config, utils, state.
Strips DB-specific code. Uses {{placeholders}} for package name substitution."
```

---

### Task 3: scaffold.py — The Stamping Script

**Files:**
- Create: `scaffold.py`
- Create: `tests/test_scaffold.py`

- [ ] **Step 1: Write the test**

Create `tests/__init__.py` (empty) and `tests/test_scaffold.py`:

```python
"""Tests for scaffold.py."""

import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from scaffold import scaffold_agent, validate_agent_name


class TestValidateAgentName:
    def test_valid_names(self):
        assert validate_agent_name("my-agent") == "my-agent"
        assert validate_agent_name("k8s-monitor") == "k8s-monitor"
        assert validate_agent_name("agent123") == "agent123"

    def test_invalid_names(self):
        with pytest.raises(ValueError, match="must start with a letter"):
            validate_agent_name("123-agent")
        with pytest.raises(ValueError, match="cannot end with a hyphen"):
            validate_agent_name("agent-")
        with pytest.raises(ValueError, match="lowercase letters"):
            validate_agent_name("My-Agent")
        with pytest.raises(ValueError, match="max 40 characters"):
            validate_agent_name("a" * 41)

    def test_to_snake_case(self):
        name = validate_agent_name("my-cool-agent")
        assert name.replace("-", "_") == "my_cool_agent"


class TestScaffoldAgent:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_agent_directory(self):
        result = scaffold_agent("test-agent", output_dir=self.tmpdir)
        assert result["status"] == "success"
        agent_dir = os.path.join(self.tmpdir, "test-agent")
        assert os.path.isdir(agent_dir)

    def test_renames_package(self):
        scaffold_agent("test-agent", output_dir=self.tmpdir)
        agent_dir = os.path.join(self.tmpdir, "test-agent")
        assert os.path.isdir(os.path.join(agent_dir, "test_agent"))
        assert not os.path.exists(os.path.join(agent_dir, "{{agent_package}}"))

    def test_replaces_placeholders(self):
        scaffold_agent("test-agent", output_dir=self.tmpdir)
        agent_dir = os.path.join(self.tmpdir, "test-agent")
        init_py = os.path.join(agent_dir, "test_agent", "__init__.py")
        assert os.path.isfile(init_py)
        content = open(init_py).read()
        assert "{{" not in content

    def test_run_adk_has_correct_imports(self):
        scaffold_agent("test-agent", output_dir=self.tmpdir)
        agent_dir = os.path.join(self.tmpdir, "test-agent")
        run_adk = os.path.join(agent_dir, "run_adk.py")
        content = open(run_adk).read()
        assert "test_agent.plugins" in content
        assert "{{agent_package}}" not in content

    def test_creates_required_files(self):
        scaffold_agent("test-agent", output_dir=self.tmpdir)
        agent_dir = os.path.join(self.tmpdir, "test-agent")
        required = [
            "run_adk.py",
            "requirements.txt",
            ".env.example",
            "test_agent/__init__.py",
            "test_agent/agent.py",
            "test_agent/prompt/instructions.py",
            "test_agent/tools/__init__.py",
            "test_agent/config/llm.py",
            "test_agent/plugins/__init__.py",
        ]
        for path in required:
            assert os.path.isfile(os.path.join(agent_dir, path)), f"Missing: {path}"

    def test_duplicate_name_fails(self):
        scaffold_agent("test-agent", output_dir=self.tmpdir)
        result = scaffold_agent("test-agent", output_dir=self.tmpdir)
        assert result["status"] == "error"
        assert "already exists" in result["error"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/albertfolch/Documents/Cursor/meta-agent
python -m pytest tests/test_scaffold.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scaffold'`

- [ ] **Step 3: Implement `scaffold.py`**

```python
#!/usr/bin/env python3
"""
Scaffold script for the Meta-Agent.

Creates a new ADK agent project from the template skeleton.

Usage:
    python scaffold.py <agent-name> [--output-dir ./generated-agents]

The agent name must be kebab-case (e.g., my-cool-agent).
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "meta_agent" / "templates"

# Placeholders used in template files
PLACEHOLDERS = {
    "{{agent_package}}",
    "{{agent_name}}",
    "{{agent_name_snake}}",
    "{{agent_description}}",
    "{{agent_system_prompt}}",
}

# Files that need placeholder substitution (have .tmpl suffix or contain placeholders)
_TMPL_SUFFIX = ".tmpl"
_TEXT_EXTENSIONS = {".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".env", ".example"}


def validate_agent_name(name: str) -> str:
    """Validate and return the agent name."""
    if not re.match(r"^[a-z][a-z0-9-]*$", name):
        if name[0].isdigit():
            raise ValueError(f"Agent name must start with a letter: {name}")
        if name != name.lower():
            raise ValueError(f"Agent name must be lowercase letters, digits, and hyphens: {name}")
        raise ValueError(f"Agent name must be lowercase letters, digits, and hyphens: {name}")
    if name.endswith("-"):
        raise ValueError(f"Agent name cannot end with a hyphen: {name}")
    if len(name) > 40:
        raise ValueError(f"Agent name max 40 characters: {name}")
    return name


def _to_snake(name: str) -> str:
    """Convert kebab-case to snake_case."""
    return name.replace("-", "_")


def _substitute(content: str, replacements: dict[str, str]) -> str:
    """Replace all placeholders in content."""
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    return content


def scaffold_agent(
    name: str,
    output_dir: str | None = None,
    description: str = "",
    system_prompt: str = "",
) -> dict:
    """
    Create a new agent project from the template.

    Args:
        name: Agent name in kebab-case (e.g., "my-agent")
        output_dir: Output directory (default: AGENTS_OUTPUT_DIR or ./generated-agents)
        description: One-line agent description
        system_prompt: Initial system prompt placeholder text

    Returns:
        dict with status, path, and files created
    """
    try:
        name = validate_agent_name(name)
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    if output_dir is None:
        output_dir = os.getenv("AGENTS_OUTPUT_DIR", "./generated-agents")

    snake_name = _to_snake(name)
    agent_dir = Path(output_dir) / name

    if agent_dir.exists():
        return {"status": "error", "error": f"Directory already exists: {agent_dir}"}

    replacements = {
        "{{agent_package}}": snake_name,
        "{{agent_name}}": name,
        "{{agent_name_snake}}": snake_name,
        "{{agent_description}}": description or f"ADK agent: {name}",
        "{{agent_system_prompt}}": system_prompt or "You are a helpful AI assistant.",
    }

    created_files = []

    try:
        # Walk the template directory and copy/transform files
        for root, dirs, files in os.walk(TEMPLATES_DIR):
            # Compute relative path from templates dir
            rel_root = Path(root).relative_to(TEMPLATES_DIR)

            # Replace {{agent_package}} in directory names
            dest_rel = str(rel_root)
            for placeholder, value in replacements.items():
                dest_rel = dest_rel.replace(placeholder, value)

            dest_dir = agent_dir / dest_rel
            dest_dir.mkdir(parents=True, exist_ok=True)

            for filename in files:
                if filename == ".gitkeep":
                    continue

                src_path = Path(root) / filename

                # Handle .tmpl files: strip suffix and substitute
                if filename.endswith(_TMPL_SUFFIX):
                    dest_filename = filename[: -len(_TMPL_SUFFIX)]
                else:
                    dest_filename = filename

                # Replace placeholders in filename
                for placeholder, value in replacements.items():
                    dest_filename = dest_filename.replace(placeholder, value)

                dest_path = dest_dir / dest_filename

                # Read and potentially substitute content
                suffix = Path(dest_filename).suffix
                if filename.endswith(_TMPL_SUFFIX) or suffix in _TEXT_EXTENSIONS:
                    try:
                        content = src_path.read_text(encoding="utf-8")
                        content = _substitute(content, replacements)
                        dest_path.write_text(content, encoding="utf-8")
                    except UnicodeDecodeError:
                        shutil.copy2(src_path, dest_path)
                else:
                    shutil.copy2(src_path, dest_path)

                rel_file = str(dest_path.relative_to(agent_dir))
                created_files.append(rel_file)

        return {
            "status": "success",
            "path": str(agent_dir),
            "agent_name": name,
            "package_name": snake_name,
            "files_created": len(created_files),
            "files": created_files,
        }

    except Exception as e:
        # Clean up on failure
        if agent_dir.exists():
            shutil.rmtree(agent_dir, ignore_errors=True)
        return {"status": "error", "error": f"Scaffold failed: {e}"}


def main():
    parser = argparse.ArgumentParser(description="Scaffold a new ADK agent")
    parser.add_argument("name", help="Agent name (kebab-case, e.g., my-agent)")
    parser.add_argument("--output-dir", default=None, help="Output directory")
    parser.add_argument("--description", default="", help="Agent description")
    args = parser.parse_args()

    result = scaffold_agent(args.name, output_dir=args.output_dir, description=args.description)
    if result["status"] == "error":
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Created {result['agent_name']} at {result['path']}")
        print(f"  Package: {result['package_name']}")
        print(f"  Files: {result['files_created']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_scaffold.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add scaffold.py tests/
git commit -m "feat: add scaffold.py with tests

Stamps out agent projects from the template skeleton.
Handles placeholder substitution, .tmpl files, and package renaming."
```

---

### Task 4: Meta-Agent Tools — File Operations + Scaffold + Validate

**Files:**
- Create: `meta_agent/tools/__init__.py`
- Create: `meta_agent/tools/file_tools.py`
- Create: `meta_agent/tools/scaffold_tool.py`
- Create: `meta_agent/tools/validate_tool.py`
- Create: `tests/test_file_tools.py`
- Create: `tests/test_validate.py`

- [ ] **Step 1: Write tests for file tools**

Create `tests/test_file_tools.py`:

```python
"""Tests for meta_agent.tools.file_tools."""

import os
import shutil
import tempfile

import pytest

# We test the raw functions, not the FunctionTool wrappers
from meta_agent.tools.file_tools import (
    _resolve_safe_path,
    _write_file_impl,
    _read_file_impl,
    _list_files_impl,
)


class TestResolveSafePath:
    def setup_method(self):
        self.base = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_relative_path(self):
        result = _resolve_safe_path("foo/bar.py", self.base)
        assert result == os.path.join(self.base, "foo", "bar.py")

    def test_rejects_absolute_path(self):
        with pytest.raises(ValueError, match="outside"):
            _resolve_safe_path("/etc/passwd", self.base)

    def test_rejects_traversal(self):
        with pytest.raises(ValueError, match="outside"):
            _resolve_safe_path("../../../etc/passwd", self.base)


class TestWriteFile:
    def setup_method(self):
        self.base = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_creates_file_and_directories(self):
        result = _write_file_impl("src/main.py", "print('hi')", self.base)
        assert result["status"] == "success"
        assert os.path.isfile(os.path.join(self.base, "src", "main.py"))

    def test_overwrites_existing(self):
        _write_file_impl("test.py", "v1", self.base)
        _write_file_impl("test.py", "v2", self.base)
        content = open(os.path.join(self.base, "test.py")).read()
        assert content == "v2"


class TestListFiles:
    def setup_method(self):
        self.base = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.base, "src"))
        open(os.path.join(self.base, "README.md"), "w").close()
        open(os.path.join(self.base, "src", "main.py"), "w").close()

    def teardown_method(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_lists_root(self):
        result = _list_files_impl(".", self.base)
        assert "README.md" in result["entries"]
        assert "src/" in result["entries"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_file_tools.py -v
```

Expected: FAIL — module not found

- [ ] **Step 3: Implement `meta_agent/tools/file_tools.py`**

```python
"""
File operation tools for the Meta-Agent.

All paths are relative to the current agent's output directory.
Absolute paths and path traversal are rejected.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

_OUTPUT_DIR = os.getenv("AGENTS_OUTPUT_DIR", "./generated-agents")


def _resolve_safe_path(path: str, base_dir: str) -> str:
    """Resolve a relative path within the base directory. Reject traversal."""
    base = Path(base_dir).resolve()
    resolved = (base / path).resolve()
    if not str(resolved).startswith(str(base)):
        raise ValueError(f"Path {path} resolves outside the output directory")
    return str(resolved)


def _write_file_impl(path: str, content: str, base_dir: str) -> dict:
    """Implementation of write_file (testable without ToolContext)."""
    try:
        resolved = _resolve_safe_path(path, base_dir)
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        return {
            "status": "success",
            "message": f"Written: {path}",
            "path": path,
            "bytes": len(content.encode("utf-8")),
        }
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": f"Write failed: {e}"}


def _read_file_impl(path: str, base_dir: str) -> dict:
    """Implementation of read_file (testable without ToolContext)."""
    try:
        resolved = _resolve_safe_path(path, base_dir)
        if not os.path.isfile(resolved):
            return {"status": "error", "error": f"File not found: {path}"}
        with open(resolved, "r", encoding="utf-8") as f:
            content = f.read()
        return {
            "status": "success",
            "path": path,
            "content": content,
            "bytes": len(content.encode("utf-8")),
        }
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": f"Read failed: {e}"}


def _list_files_impl(path: str, base_dir: str) -> dict:
    """Implementation of list_files (testable without ToolContext)."""
    try:
        resolved = _resolve_safe_path(path, base_dir)
        if not os.path.isdir(resolved):
            return {"status": "error", "error": f"Directory not found: {path}"}
        entries = []
        for entry in sorted(os.listdir(resolved)):
            full = os.path.join(resolved, entry)
            if os.path.isdir(full):
                entries.append(f"{entry}/")
            else:
                entries.append(entry)
        return {
            "status": "success",
            "path": path,
            "entries": entries,
            "count": len(entries),
        }
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": f"List failed: {e}"}


# ── ADK FunctionTool wrappers ────────────────────────────────────────


def write_file(path: str, content: str, tool_context: ToolContext) -> dict:
    """Write a file to the generated agent directory.

    Args:
        path: Relative path within the agent directory (e.g., "my_agent/tools/k8s.py")
        content: File content to write

    Returns:
        dict with status, path, and bytes written
    """
    base = tool_context.state.get("agent_output_dir", _OUTPUT_DIR)
    return _write_file_impl(path, content, base)


def read_file(path: str, tool_context: ToolContext) -> dict:
    """Read a file from the generated agent directory.

    Args:
        path: Relative path within the agent directory

    Returns:
        dict with status, path, and content
    """
    base = tool_context.state.get("agent_output_dir", _OUTPUT_DIR)
    return _read_file_impl(path, base)


def list_files(path: str = ".", tool_context: ToolContext = None) -> dict:
    """List files and directories in the generated agent directory.

    Args:
        path: Relative directory path (default: root of output dir)

    Returns:
        dict with status, entries list, and count
    """
    base = _OUTPUT_DIR
    if tool_context:
        base = tool_context.state.get("agent_output_dir", _OUTPUT_DIR)
    return _list_files_impl(path, base)


write_file_tool = FunctionTool(func=write_file)
read_file_tool = FunctionTool(func=read_file)
list_files_tool = FunctionTool(func=list_files)
```

- [ ] **Step 4: Run file tools tests**

```bash
python -m pytest tests/test_file_tools.py -v
```

Expected: All PASS

- [ ] **Step 5: Implement `meta_agent/tools/scaffold_tool.py`**

```python
"""
Scaffold tool — wraps scaffold.py as an ADK FunctionTool.
"""

import logging
import os
import sys

from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext

# Add project root to path so we can import scaffold
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from scaffold import scaffold_agent as _scaffold_agent

logger = logging.getLogger(__name__)


def scaffold_agent(
    name: str,
    description: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Create a new ADK agent project from the production skeleton.

    This stamps out a complete, runnable agent project with:
    - FastAPI server (run_adk.py) with auth, health checks, SSE
    - Full plugin chain (trace, resilience, cache, console logger)
    - LiteLLM/OpenRouter config
    - Stub prompt, tools, skills, and contexts directories

    Args:
        name: Agent name in kebab-case (e.g., "k8s-monitor-agent").
              Must be lowercase letters, digits, hyphens. Max 40 chars.
        description: One-line description of the agent's purpose.

    Returns:
        dict with status, path, package_name, and files_created count.
        On success, the agent directory is ready for customization.
    """
    output_dir = os.getenv("AGENTS_OUTPUT_DIR", "./generated-agents")
    if tool_context:
        output_dir = tool_context.state.get("agent_output_dir", output_dir)

    result = _scaffold_agent(name, output_dir=output_dir, description=description)

    if result["status"] == "success":
        # Store the agent path in state for subsequent file operations
        if tool_context:
            tool_context.state["current_agent_name"] = name
            tool_context.state["current_agent_path"] = result["path"]
            tool_context.state["current_agent_package"] = result["package_name"]
        logger.info("Scaffolded agent: %s at %s", name, result["path"])

    return result


scaffold_agent_tool = FunctionTool(func=scaffold_agent)
```

- [ ] **Step 6: Write test for validate tool**

Create `tests/test_validate.py`:

```python
"""Tests for meta_agent.tools.validate_tool."""

import os
import shutil
import tempfile

import pytest

from scaffold import scaffold_agent
from meta_agent.tools.validate_tool import _validate_agent_impl


class TestValidateAgent:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        scaffold_agent("test-agent", output_dir=self.tmpdir)
        self.agent_dir = os.path.join(self.tmpdir, "test-agent")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_scaffold_passes(self):
        result = _validate_agent_impl(self.agent_dir)
        assert result["status"] == "success"
        assert result["errors"] == []

    def test_missing_agent_py_fails(self):
        os.remove(os.path.join(self.agent_dir, "test_agent", "agent.py"))
        result = _validate_agent_impl(self.agent_dir)
        assert result["status"] == "error"
        assert any("agent.py" in e for e in result["errors"])

    def test_missing_directory_fails(self):
        result = _validate_agent_impl("/nonexistent/path")
        assert result["status"] == "error"
```

- [ ] **Step 7: Run validate test to verify it fails**

```bash
python -m pytest tests/test_validate.py -v
```

Expected: FAIL — module not found

- [ ] **Step 8: Implement `meta_agent/tools/validate_tool.py`**

```python
"""
Validate tool — checks agent project structure and basic correctness.
"""

import logging
import os
from pathlib import Path

from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

# Required files in every generated agent (relative to agent dir)
# The package name is discovered dynamically
_REQUIRED_PACKAGE_FILES = [
    "__init__.py",
    "agent.py",
    "config/__init__.py",
    "config/llm.py",
    "config/logging.py",
    "plugins/__init__.py",
    "prompt/__init__.py",
    "prompt/instructions.py",
    "tools/__init__.py",
]

_REQUIRED_ROOT_FILES = [
    "run_adk.py",
    "requirements.txt",
]


def _find_package_dir(agent_dir: str) -> str | None:
    """Find the Python package directory (the one with agent.py)."""
    for entry in os.listdir(agent_dir):
        full = os.path.join(agent_dir, entry)
        if os.path.isdir(full) and os.path.isfile(os.path.join(full, "agent.py")):
            return entry
    return None


def _validate_agent_impl(agent_dir: str) -> dict:
    """Validate agent structure. Testable without ToolContext."""
    errors = []
    warnings = []

    if not os.path.isdir(agent_dir):
        return {
            "status": "error",
            "errors": [f"Directory not found: {agent_dir}"],
            "warnings": [],
        }

    # Check root files
    for f in _REQUIRED_ROOT_FILES:
        if not os.path.isfile(os.path.join(agent_dir, f)):
            errors.append(f"Missing root file: {f}")

    # Find and check package directory
    package = _find_package_dir(agent_dir)
    if not package:
        errors.append("No Python package found (directory with agent.py)")
        return {"status": "error", "errors": errors, "warnings": warnings}

    # Check required package files
    for f in _REQUIRED_PACKAGE_FILES:
        if not os.path.isfile(os.path.join(agent_dir, package, f)):
            errors.append(f"Missing: {package}/{f}")

    # Check for placeholder remnants
    for root, dirs, files in os.walk(agent_dir):
        for filename in files:
            if filename.endswith((".py", ".md", ".txt")):
                filepath = os.path.join(root, filename)
                try:
                    content = open(filepath, encoding="utf-8").read()
                    if "{{" in content and "}}" in content:
                        rel = os.path.relpath(filepath, agent_dir)
                        errors.append(f"Unresolved placeholder in: {rel}")
                except Exception:
                    pass

    # Check skills directory
    skills_dir = os.path.join(agent_dir, package, "skills")
    if os.path.isdir(skills_dir):
        for skill_dir in Path(skills_dir).iterdir():
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    warnings.append(f"Skill missing SKILL.md: {skill_dir.name}")

    status = "success" if not errors else "error"
    return {
        "status": status,
        "agent_dir": agent_dir,
        "package": package,
        "errors": errors,
        "warnings": warnings,
    }


def validate_agent(name: str, tool_context: ToolContext = None) -> dict:
    """Validate a generated agent's structure and correctness.

    Checks that all required files exist, no unresolved placeholders remain,
    and skills have valid SKILL.md files.

    Args:
        name: Agent name (kebab-case) to validate in the output directory.

    Returns:
        dict with status, errors list, and warnings list.
    """
    output_dir = os.getenv("AGENTS_OUTPUT_DIR", "./generated-agents")
    if tool_context:
        output_dir = tool_context.state.get("agent_output_dir", output_dir)

    agent_dir = os.path.join(output_dir, name)
    return _validate_agent_impl(agent_dir)


validate_agent_tool = FunctionTool(func=validate_agent)
```

- [ ] **Step 9: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: All PASS

- [ ] **Step 10: Create `meta_agent/tools/__init__.py`**

```python
"""
Tools for the Meta-Agent.
"""

from .file_tools import write_file_tool, read_file_tool, list_files_tool
from .scaffold_tool import scaffold_agent_tool
from .validate_tool import validate_agent_tool


def get_tools():
    """Return all meta-agent function tools."""
    return [
        scaffold_agent_tool,
        write_file_tool,
        read_file_tool,
        list_files_tool,
        validate_agent_tool,
    ]
```

- [ ] **Step 11: Commit**

```bash
git add meta_agent/tools/ tests/
git commit -m "feat: add meta-agent tools (scaffold, file ops, validate)

scaffold_agent: stamps out skeleton from template
write_file/read_file/list_files: scoped file operations
validate_agent: structure and placeholder checking"
```

---

### Task 5: Meta-Agent Skills (Part 1) — Agent Patterns + Prompt Engineering

Create the first two SKILL.md files with L3 references. These teach the meta-agent how to design agent architectures and write effective system prompts.

**Files:**
- Create: `meta_agent/skills/adk-agent-patterns/SKILL.md`
- Create: `meta_agent/skills/adk-agent-patterns/references/loop-patterns.md`
- Create: `meta_agent/skills/adk-agent-patterns/references/parallel-patterns.md`
- Create: `meta_agent/skills/adk-agent-patterns/references/multi-agent-patterns.md`
- Create: `meta_agent/skills/adk-prompt-engineering/SKILL.md`
- Create: `meta_agent/skills/adk-prompt-engineering/references/prompt-templates.md`
- Create: `meta_agent/skills/adk-prompt-engineering/references/instruction-provider-pattern.md`
- Create: `meta_agent/skills/adk-prompt-engineering/references/state-placeholders.md`

- [ ] **Step 1: Create skill directories**

```bash
mkdir -p meta_agent/skills/adk-agent-patterns/references
mkdir -p meta_agent/skills/adk-prompt-engineering/references
```

- [ ] **Step 2: Write `adk-agent-patterns/SKILL.md`**

This skill teaches the meta-agent when and how to use different ADK agent types. The content should be derived from the adk-agents skill knowledge (Section 1: Overall Architecture) and the official ADK docs. Load references for detailed patterns.

The SKILL.md should cover: LlmAgent (single), LoopAgent, SequentialAgent, ParallelAgent, and when to use each. Keep under 500 lines. Put detailed code patterns in references/.

- [ ] **Step 3: Write `adk-agent-patterns/references/loop-patterns.md`**

Detailed LoopAgent patterns: planner→executor→reviewer cycle, max_iterations, exit conditions via `tool_context.actions.escalate = True`, iterative refinement examples. Include complete code examples.

- [ ] **Step 4: Write `adk-agent-patterns/references/parallel-patterns.md`**

ParallelAgent patterns: task decomposition, independent concurrent execution, fan-out/fan-in. Include code examples.

- [ ] **Step 5: Write `adk-agent-patterns/references/multi-agent-patterns.md`**

Multi-agent hierarchy patterns: coordinator with sub-agents, Agent Tool pattern (using agents as tools), delegation. Include code examples.

- [ ] **Step 6: Write `adk-prompt-engineering/SKILL.md`**

Teaches effective ADK system prompts: structure, dynamic instructions, context injection, tone/style. References for detailed patterns.

- [ ] **Step 7: Write `adk-prompt-engineering/references/prompt-templates.md`**

Concrete prompt templates for common agent types: data analysis, monitoring, customer support, code generation. Based on the data-analysis-agent's prompt structure as the gold standard.

- [ ] **Step 8: Write `adk-prompt-engineering/references/instruction-provider-pattern.md`**

The `InstructionProvider` pattern: async function receiving `ReadonlyContext`, loading context files, injecting dynamic state. Code examples based on `data-analysis-agent/prompt/instructions.py`.

- [ ] **Step 9: Write `adk-prompt-engineering/references/state-placeholders.md`**

State placeholders in prompts: `{mode}`, `{plan}`, `{planApproved}`, custom state keys. How to use `output_key` to feed state between agents.

- [ ] **Step 10: Commit**

```bash
git add meta_agent/skills/adk-agent-patterns/ meta_agent/skills/adk-prompt-engineering/
git commit -m "feat: add ADK agent-patterns and prompt-engineering skills

Skills with L3 references for agent architecture selection
and effective system prompt writing."
```

---

### Task 6: Meta-Agent Skills (Part 2) — Tool Creation + Skill Creation + Callbacks

**Files:**
- Create: `meta_agent/skills/adk-tool-creation/SKILL.md` + references/
- Create: `meta_agent/skills/adk-skill-creation/SKILL.md` + references/
- Create: `meta_agent/skills/adk-callbacks-hitl/SKILL.md` + references/

- [ ] **Step 1: Create directories**

```bash
mkdir -p meta_agent/skills/adk-tool-creation/references
mkdir -p meta_agent/skills/adk-skill-creation/references
mkdir -p meta_agent/skills/adk-callbacks-hitl/references
```

- [ ] **Step 2: Write `adk-tool-creation/SKILL.md`**

How to write ADK FunctionTools: function signature conventions, ToolContext usage, error handling, return format (`{status, message, ...}`), type hints. References for patterns and async tools.

- [ ] **Step 3: Write `adk-tool-creation/references/tool-patterns.md`**

Concrete tool patterns: CRUD tools, API wrapper tools, file operation tools, search tools. Show complete implementations with error handling.

- [ ] **Step 4: Write `adk-tool-creation/references/tool-context-api.md`**

ToolContext API reference: `tool_context.state`, `tool_context.actions.escalate`, artifact management. Code examples.

- [ ] **Step 5: Write `adk-tool-creation/references/async-tool-examples.md`**

Async tool patterns: `aiohttp` for HTTP APIs, `asyncpg` for databases, concurrent tool execution.

- [ ] **Step 6: Write `adk-skill-creation/SKILL.md`**

**This is the meta-skill.** Teaches the meta-agent how to create valid SKILL.md files following the agentskills.io spec. Covers: frontmatter (name, description), instruction body, references directory, progressive disclosure (L1/L2/L3), SkillToolset wiring.

- [ ] **Step 7: Write `adk-skill-creation/references/skill-spec.md`**

The agentskills.io specification summary: required fields, directory structure, naming conventions, file size guidelines. Derived from the blog post patterns.

- [ ] **Step 8: Write `adk-skill-creation/references/example-skills.md`**

3-4 complete example SKILL.md files for different domains: security review, API integration, data pipeline validation. Each with references/ examples.

- [ ] **Step 9: Write `adk-skill-creation/references/skilltoolset-wiring.md`**

How to wire SkillToolset in agent.py: `load_skill_from_dir`, `SkillToolset(skills=[...])`, adding to agent tools list. Complete code example matching the template's agent.py.tmpl pattern.

- [ ] **Step 10: Write `adk-callbacks-hitl/SKILL.md`**

Callback signatures (exact parameter names!), HITL gates, before/after hooks. References for patterns and state management.

- [ ] **Step 11: Write `adk-callbacks-hitl/references/callback-signatures.md`**

Exact callback signatures: `before_model_callback(callback_context, llm_request)`, `before_tool_callback(tool, args, tool_context)`, etc. Common pitfall: wrong parameter names.

- [ ] **Step 12: Write `adk-callbacks-hitl/references/hitl-patterns.md`**

HITL implementation: PlanApprovalGate, defensive tool checks with multiple allow conditions, enhanced logging.

- [ ] **Step 13: Write `adk-callbacks-hitl/references/state-management.md`**

State management: `EventActions.state_delta` in BaseAgent, `callback_context.state` in callbacks, state prefixes (`user:`, `app:`, `temp:`), output_key.

- [ ] **Step 14: Commit**

```bash
git add meta_agent/skills/adk-tool-creation/ meta_agent/skills/adk-skill-creation/ meta_agent/skills/adk-callbacks-hitl/
git commit -m "feat: add tool-creation, skill-creation, and callbacks-hitl skills

Completes the 5-skill knowledge base for the meta-agent.
Includes the meta-skill for generating SKILL.md files."
```

---

### Task 7: Meta-Agent System Prompt + Agent Wiring

**Files:**
- Create: `meta_agent/prompt/__init__.py`
- Create: `meta_agent/prompt/instructions.py`
- Modify: `meta_agent/agent.py`
- Modify: `meta_agent/__init__.py`

- [ ] **Step 1: Create `meta_agent/prompt/__init__.py`**

```python
```

(Empty file)

- [ ] **Step 2: Create `meta_agent/prompt/instructions.py`**

```python
"""
Meta-Agent instruction builder.

Constructs the system prompt for the agent-building workflow.
"""

import logging
from pathlib import Path

from ..utils.date_utils import format_current_date

logger = logging.getLogger(__name__)


async def get_agent_instruction(ctx) -> str:
    """Generate the meta-agent system prompt. ADK InstructionProvider."""
    formatted_date = format_current_date()

    return f"""You are an expert ADK (Agent Development Kit) agent builder. Your job is to create production-ready Google ADK agents from natural language descriptions.

Today's date: {formatted_date}

# Your Capabilities

You have two types of capabilities:
1. **Function Tools** for file operations: scaffold_agent, write_file, read_file, list_files, validate_agent
2. **Skills** (via list_skills/load_skill/load_skill_resource) containing deep ADK knowledge about agent patterns, prompt engineering, tool creation, skill creation, and callbacks

# Workflow

Follow this workflow for every agent creation request:

## 1. Discovery
Ask the user about:
- **Goal**: What should the agent do?
- **Tasks**: What specific tasks should it handle?
- **Tools**: What external services, APIs, or data sources does it need?
- **Domain knowledge**: Any specific domain expertise needed?
- **LLM preference**: Model preference (default: OpenRouter via LiteLLM)

Ask only the questions that aren't already answered. If the user gives a comprehensive brief, skip to Design.

## 2. Design
Before writing any code, propose:
- Which tools to create (with names and descriptions)
- Which skills to write (with SKILL.md outlines)
- System prompt strategy (key sections, tone)
- Any special patterns needed (LoopAgent, ParallelAgent, etc.)

Get user approval before proceeding.

## 3. Scaffold
Call `scaffold_agent` with the agent name and description. This creates the complete project skeleton with:
- FastAPI server with auth, health checks, SSE streaming
- Production plugin chain (trace, resilience, cache, console logger)
- LiteLLM/OpenRouter config
- Stub files for prompt, tools, skills, and contexts

## 4. Generate
Load your skills for guidance, then write the custom files:

**Always load relevant skills before generating code.** Use:
- `load_skill("adk-prompt-engineering")` before writing prompt/instructions.py
- `load_skill("adk-tool-creation")` before writing tools
- `load_skill("adk-skill-creation")` before writing SKILL.md files
- `load_skill("adk-agent-patterns")` for architecture decisions
- `load_skill("adk-callbacks-hitl")` for callbacks and HITL gates

Use `load_skill_resource` for detailed patterns and examples.

Write these files using `write_file`:
a. `<package>/prompt/instructions.py` — Full system prompt with InstructionProvider pattern
b. `<package>/tools/<name>.py` — Each custom tool with proper ToolContext signature
c. `<package>/tools/__init__.py` — Tool registry
d. `<package>/skills/<name>/SKILL.md` — Domain skills with references/
e. `<package>/contexts/<name>.md` — Domain knowledge files
f. `<package>/agent.py` — Wire tools + SkillToolset + prompt together
g. `.env.example` — Update with agent-specific env vars

## 5. Validate
Call `validate_agent` to check:
- All required files exist
- No unresolved placeholders
- Skills have valid SKILL.md files

## 6. Iterate
Present the result to the user. Accept feedback and refine any component.

# Code Generation Rules

## Tools
- Every tool function must accept `tool_context: ToolContext` as a parameter
- Return dicts with at minimum `status` and `message` keys
- Handle errors gracefully — return error dicts, don't raise exceptions
- Use type hints for all parameters
- Write clear docstrings (the LLM reads them to decide when to call the tool)
- For async operations, use `async def` and `await`

## System Prompts
- Lead with identity and purpose
- Structure with clear markdown sections
- Include tone/style guidance
- Add tool usage instructions specific to the agent's tools
- Include domain knowledge via context files
- Use the InstructionProvider pattern (async function with ReadonlyContext)
- Inject current date dynamically

## Skills (SKILL.md)
- Follow the agentskills.io specification strictly
- Frontmatter: name (kebab-case), description (under 1024 chars)
- Instructions: step-by-step, clear, actionable
- Put detailed reference material in references/ directory
- Keep SKILL.md under 500 lines
- Wire via SkillToolset in agent.py

## Agent Wiring (agent.py)
- Use the template's pattern: _build_skill_toolset() + _build_tools() + LlmAgent
- Always auto-discover skills from the skills/ directory
- Use FAST_MODEL from config (never hardcode model names)
- Use get_agent_instruction as the InstructionProvider

# Important Rules
- NEVER hardcode API keys, secrets, or credentials in generated code
- ALWAYS document required env vars in .env.example
- NEVER hallucinate ADK APIs — load your skills for correct patterns
- Generated agents must be immediately runnable with `DEV_MODE=true python run_adk.py`
- Use the exact callback parameter names ADK expects (callback_context, llm_request, etc.)
- Prefer FunctionTool over raw function registration
"""
```

- [ ] **Step 3: Create `meta_agent/agent.py`**

```python
"""
Meta-Agent — creates production-ready Google ADK agents.

Given a goal, tasks, and tools description, this agent:
1. Scaffolds a project from the production skeleton
2. Generates custom prompts, tools, and skills
3. Validates the result
4. Iterates based on feedback
"""

from __future__ import annotations

import logging
import pathlib

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.skills import load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset

from .config.llm import FAST_MODEL
from .tools import get_tools
from .prompt.instructions import get_agent_instruction

logger = logging.getLogger(__name__)

load_dotenv()

_SKILLS_DIR = pathlib.Path(__file__).parent / "skills"


def _build_skill_toolset() -> SkillToolset | None:
    """Load all skills from the skills/ directory."""
    skills = []
    if not _SKILLS_DIR.is_dir():
        logger.warning("Skills directory not found: %s", _SKILLS_DIR)
        return None

    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
            try:
                skill = load_skill_from_dir(skill_dir)
                skills.append(skill)
                logger.info("Loaded skill: %s", skill.name)
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", skill_dir.name, e)

    if not skills:
        logger.warning("No skills loaded")
        return None

    logger.info("Loaded %d skills", len(skills))
    return SkillToolset(skills=skills)


def _build_tools():
    """Build agent tools: file operation tools + SkillToolset."""
    tools = list(get_tools())
    skill_toolset = _build_skill_toolset()
    if skill_toolset:
        tools.append(skill_toolset)
    return tools


root_agent = LlmAgent(
    model=FAST_MODEL,
    name="meta_agent",
    description="Creates production-ready Google ADK agents from natural language descriptions",
    instruction=get_agent_instruction,
    tools=_build_tools(),
)
```

- [ ] **Step 4: Update `meta_agent/__init__.py`**

Already correct:
```python
from . import agent
```

- [ ] **Step 5: Commit**

```bash
git add meta_agent/prompt/ meta_agent/agent.py meta_agent/__init__.py
git commit -m "feat: add meta-agent system prompt and agent wiring

System prompt enforces Discovery→Design→Scaffold→Generate→Validate→Iterate.
Agent wires function tools + SkillToolset with auto-discovery."
```

---

### Task 8: Meta-Agent Runner (run_adk.py)

**Files:**
- Create: `run_adk.py` (at project root)

- [ ] **Step 1: Create `run_adk.py`**

Adapted from data-analysis-agent's `run_adk.py` — replace all `data_analysis_agent` references with `meta_agent`, remove DB health check and shutdown hook.

```python
"""
Meta-Agent entrypoint.

Usage:
  Development: DEV_MODE=true python run_adk.py
  Production:  python run_adk.py (requires SESSION_SERVICE_URI)
"""

import os
import secrets
import socket
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from google.adk.cli.fast_api import get_fast_api_app
from meta_agent.plugins import PLUGIN_PATHS
from meta_agent.config.logging import setup_logging, generate_request_id, request_id_var

try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass


class APIKeyMiddleware(BaseHTTPMiddleware):
    PUBLIC_PREFIXES = ("/health", "/favicon.ico")

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/" or any(path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        else:
            token = request.headers.get("X-API-Key", "")
        if not secrets.compare_digest(token, self.api_key):
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "message": "Invalid or missing API key"},
            )
        return await call_next(request)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID", generate_request_id())
        request_id_var.set(rid)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


def add_endpoints(app: FastAPI) -> None:
    @app.get("/health")
    async def health_check():
        return JSONResponse(content={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "meta-agent",
            "status": "healthy",
        })

    @app.get("/")
    async def root():
        return JSONResponse(content={
            "message": "Meta-Agent API — Creates production-ready ADK agents",
            "endpoints": {
                "/health": "Health check",
                "/run/": "Non-streaming agent execution (POST)",
                "/run_sse/": "Streaming agent execution (POST)",
            },
        })

    print("[ADK] Endpoints added: /health, /")


def main() -> None:
    setup_logging()
    agents_dir = os.getenv("AGENTS_DIR", ".")
    dev_mode = os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes")
    port = int(os.getenv("PORT", "8000"))

    print(f"[ADK] Meta-Agent starting: PORT={port}, DEV_MODE={dev_mode}")

    if dev_mode:
        print("[ADK] DEVELOPMENT mode (in-memory sessions)")
        app = get_fast_api_app(
            agents_dir=agents_dir,
            session_service_uri=None,
            use_local_storage=False,
            web=False,
            a2a=False,
            host="",
            port=port,
            url_prefix=None,
            reload_agents=True,
            extra_plugins=PLUGIN_PATHS,
        )
    else:
        session_uri = os.getenv("SESSION_SERVICE_URI")
        if not session_uri:
            raise RuntimeError("SESSION_SERVICE_URI is required in production mode.")
        session_uri = _normalize_to_asyncpg_uri(session_uri)
        connect_args = {"ssl": "require"}
        print("[ADK] PRODUCTION mode")
        app = get_fast_api_app(
            agents_dir=agents_dir,
            session_service_uri=session_uri,
            session_db_kwargs={"connect_args": connect_args},
            web=False,
            a2a=False,
            host="",
            port=port,
            url_prefix=None,
            reload_agents=True,
            extra_plugins=PLUGIN_PATHS,
        )

    app.router.redirect_slashes = False
    app.add_middleware(RequestIDMiddleware)

    api_key = os.getenv("API_KEY")
    if api_key:
        app.add_middleware(APIKeyMiddleware, api_key=api_key)
        if not os.getenv("DOCS_ENABLED", "").lower() in ("true", "1", "yes"):
            app.openapi_url = None
            app.docs_url = None
            app.redoc_url = None
        print("[ADK] API key authentication enabled")
    else:
        print("[ADK] WARNING: No API_KEY set — endpoints are unauthenticated")

    add_endpoints(app)
    print(f"[ADK] Meta-Agent ready: http://0.0.0.0:{port}")
    uvicorn.run(app, host="", port=port)


def _normalize_to_asyncpg_uri(uri: str) -> str:
    if uri.startswith("postgresql://"):
        uri = uri.replace("postgresql://", "postgresql+asyncpg://", 1)
    parsed = urlsplit(uri)
    qs = parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for (k, v) in qs if k.lower() not in {"sslmode", "channel_binding", "channelbinding"}]
    new_query = urlencode(filtered)
    return urlunsplit(parsed._replace(query=new_query))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create `generated-agents/.gitkeep`**

```bash
mkdir -p generated-agents
touch generated-agents/.gitkeep
```

- [ ] **Step 3: Commit**

```bash
git add run_adk.py generated-agents/.gitkeep
git commit -m "feat: add meta-agent FastAPI runner

Adapted from data-analysis-agent runner. Removes DB-specific
health checks. Keeps auth, request tracing, plugin chain."
```

---

### Task 9: Smoke Test — Generate a Test Agent End-to-End

**Files:**
- Create: `tests/test_end_to_end.py`

- [ ] **Step 1: Write end-to-end scaffold + validate test**

```python
"""End-to-end test: scaffold + validate an agent."""

import os
import shutil
import tempfile

import pytest

from scaffold import scaffold_agent
from meta_agent.tools.validate_tool import _validate_agent_impl


class TestEndToEnd:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_scaffold_and_validate(self):
        """Scaffold an agent and verify it passes validation."""
        result = scaffold_agent(
            "hello-world-agent",
            output_dir=self.tmpdir,
            description="A simple greeting agent",
        )
        assert result["status"] == "success"
        assert result["files_created"] > 10

        # Validate
        agent_dir = os.path.join(self.tmpdir, "hello-world-agent")
        validation = _validate_agent_impl(agent_dir)
        assert validation["status"] == "success", f"Validation errors: {validation['errors']}"
        assert validation["package"] == "hello_world_agent"
        assert validation["errors"] == []

    def test_generated_files_have_correct_imports(self):
        """Check that generated Python files have correct package imports."""
        scaffold_agent("my-api-agent", output_dir=self.tmpdir)
        agent_dir = os.path.join(self.tmpdir, "my-api-agent")

        # Check agent.py has correct imports
        agent_py = open(os.path.join(agent_dir, "my_api_agent", "agent.py")).read()
        assert "from .config.llm import FAST_MODEL" in agent_py
        assert "from .tools import get_tools" in agent_py
        assert "from .prompt.instructions import get_agent_instruction" in agent_py

        # Check plugins/__init__.py has correct paths
        plugins_init = open(os.path.join(agent_dir, "my_api_agent", "plugins", "__init__.py")).read()
        assert "my_api_agent.plugins.trace" in plugins_init
        assert "{{agent_package}}" not in plugins_init

        # Check run_adk.py has correct imports
        run_adk = open(os.path.join(agent_dir, "run_adk.py")).read()
        assert "my_api_agent.plugins" in run_adk
        assert "my_api_agent.config.logging" in run_adk
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/ -v
```

Expected: All PASS

- [ ] **Step 3: Run the meta-agent locally (manual verification)**

```bash
# Install dependencies
pip install -r requirements.txt

# Start in dev mode
DEV_MODE=true python run_adk.py
```

Verify: Server starts, health check returns 200, agent loads with 5 skills + 5 tools.

- [ ] **Step 4: Final commit**

```bash
git add tests/test_end_to_end.py
git commit -m "feat: add end-to-end tests for scaffold + validate pipeline

Verifies generated agents have correct imports, no placeholders,
and pass structural validation."
```

---

## Implementation Notes

### Parallelizable Tasks
- **Tasks 1-2** can run in parallel (foundation + templates are independent)
- **Tasks 5-6** can run in parallel (skill groups are independent)
- **Tasks 3-4** depend on Tasks 1-2 (need the templates and config to exist)
- **Tasks 7-8** depend on Tasks 4-6 (need tools and skills to wire up)
- **Task 9** depends on all previous tasks

### Skill Content Guidance
Tasks 5 and 6 specify SKILL.md files but don't include their full content (they'd be 100+ lines each). The implementer should:
1. Load the adk-agents skill content (already available in this session) for agent patterns, callbacks, HITL
2. Reference the data-analysis-agent's actual code for prompt engineering templates
3. Reference the agentskills.io spec and blog post for skill creation patterns
4. Use ADK docs via the MCP context7 tools for current API details

### Testing Strategy
- Unit tests for scaffold, file tools, and validate (Tasks 3-4)
- Integration test for scaffold→validate pipeline (Task 9)
- Manual smoke test: start the meta-agent and create an agent via conversation
