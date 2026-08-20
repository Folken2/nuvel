"""{{agent_name}} — agent definition.

A Buzz agent has no framework object graph to build: it is a model id, an
instruction, and a list of tools, all resolved from the environment. This
module owns that resolution so the ACP adapter, the terminal CLI, and the
relay worker all start from the same configuration.

    BUZZ_AGENT_PROVIDER   openrouter (default) | openai | anthropic | groq | ollama | custom
    BUZZ_AGENT_MODEL      model id, provider prefix optional
    BUZZ_AGENT_BASE_URL   overrides the provider's base URL
    OPENROUTER_API_KEY    (or the key env matching the provider)

Tools are plain Python callables described by a JSON schema; see
:class:`Tool` and ``skills/__init__.py`` for the bundled ones.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from .skills import skill_tools

AGENT_NAME = "{{agent_name}}"
AGENT_DESCRIPTION = "{{agent_description}}"

DEFAULT_MODEL = "{{default_model}}"
DEFAULT_INSTRUCTION = """{{instruction_frame}}"""

# provider -> (base URL, env var carrying the API key). Every entry speaks the
# OpenAI /chat/completions shape; Anthropic serves it from its compatibility
# layer, so one client covers them all.
PROVIDERS: dict[str, tuple[str, str]] = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", "OLLAMA_API_KEY"),
}

DEFAULT_PROVIDER = "openrouter"


@dataclass
class Tool:
    """One callable the model may invoke.

    ``parameters`` is a JSON Schema object; ``handler`` may be sync or async
    and receives the decoded arguments as keyword arguments.
    """

    name: str
    description: str
    parameters: dict
    handler: Callable[..., Any]

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
                or {"type": "object", "properties": {}},
            },
        }


def _strip_provider_prefix(model: str, provider: str) -> str:
    """``openrouter/moonshotai/kimi-k2.5`` → ``moonshotai/kimi-k2.5``.

    The nuvel-wide model ids carry a routing prefix for LiteLLM. The raw HTTP
    APIs don't want it, so drop it when it names the provider we're calling.
    """
    prefix = f"{provider}/"
    return model[len(prefix):] if model.startswith(prefix) else model


@dataclass
class BuzzConfig:
    """Resolved model configuration for one agent process."""

    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    base_url: str = ""
    api_key: str = ""
    instruction: str = DEFAULT_INSTRUCTION
    max_tool_iterations: int = 8
    request_timeout: float = 120.0

    @classmethod
    def from_env(cls) -> "BuzzConfig":
        provider = (os.getenv("BUZZ_AGENT_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
        default_base, key_env = PROVIDERS.get(provider, ("", "BUZZ_AGENT_API_KEY"))

        model = (os.getenv("BUZZ_AGENT_MODEL") or DEFAULT_MODEL).strip()
        base_url = (os.getenv("BUZZ_AGENT_BASE_URL") or default_base).rstrip("/")
        api_key = os.getenv("BUZZ_AGENT_API_KEY") or os.getenv(key_env) or ""

        instruction = os.getenv("BUZZ_AGENT_INSTRUCTION") or DEFAULT_INSTRUCTION

        return cls(
            provider=provider,
            model=_strip_provider_prefix(model, provider),
            base_url=base_url,
            api_key=api_key,
            instruction=instruction,
            max_tool_iterations=int(os.getenv("BUZZ_AGENT_MAX_TOOL_ITERATIONS", "8")),
            request_timeout=float(os.getenv("BUZZ_AGENT_TIMEOUT", "120")),
        )

    def validate(self) -> list[str]:
        """Return human-readable problems; empty means ready to run."""
        problems: list[str] = []
        if not self.base_url:
            problems.append(
                f"No base URL for provider '{self.provider}'. "
                "Set BUZZ_AGENT_BASE_URL, or pick a known BUZZ_AGENT_PROVIDER "
                f"({', '.join(sorted(PROVIDERS))})."
            )
        if not self.api_key and self.provider != "ollama":
            _, key_env = PROVIDERS.get(self.provider, ("", "BUZZ_AGENT_API_KEY"))
            problems.append(f"No API key. Set {key_env} (or BUZZ_AGENT_API_KEY).")
        if not self.model:
            problems.append("No model. Set BUZZ_AGENT_MODEL.")
        return problems


@dataclass
class Agent:
    """The whole agent: identity, model config, and callable tools."""

    name: str
    config: BuzzConfig
    tools: list[Tool] = field(default_factory=list)

    def tool_map(self) -> dict[str, Tool]:
        return {t.name: t for t in self.tools}


def build_agent(extra_tools: list[Tool] | None = None) -> Agent:
    """Build the agent from the environment.

    ``extra_tools`` are appended after the bundled skill tools — the ACP
    server uses this to inject per-session tools an editor brought along.
    """
    tools = list(skill_tools())
    tools.extend(extra_tools or [])
    return Agent(name=AGENT_NAME, config=BuzzConfig.from_env(), tools=tools)
