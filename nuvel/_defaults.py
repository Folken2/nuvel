"""Canonical default model ids for ADK agents.

Single source of truth for FAST_MODEL and REASONING_MODEL defaults. When
either value changes here, every downstream surface is updated:

  Python (import directly):
    - nuvel/config/llm.py             (meta-agent's own LiteLlm instances)
    - nuvel/doctor.py                 (pricing.json coverage check)

  Templates (substituted at scaffold time via _build_replacements):
    - nuvel/backends/adk/templates/{{agent_package}}/config/llm.py
    - nuvel/backends/adk/templates/.env.example
    - nuvel/backends/adk/templates/README.md.tmpl

  Narrative docs (manual — please keep in sync, no other reader):
    - README.md
    - docs/reference/env-vars.md
    - docs/reference/cli.md  (if it grows examples)

Both ids are OpenRouter-routed so the pricing-sync tool can refresh
them automatically via `nuvel pricing sync`.
"""

DEFAULT_FAST_MODEL = "openrouter/moonshotai/kimi-k2.5"
DEFAULT_REASONING_MODEL = "openrouter/google/gemini-3.1-pro-preview"
