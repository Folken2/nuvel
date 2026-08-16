"""
Long-horizon memory self-improvement for the agent.

This package layers relevance-conditioned recall and a fire-and-forget
"judge" self-improvement loop on top of the markdown long-term memory in
``state/memory.py``. Everything here is opt-in via ``NUVEL_MEMORY_*`` env
vars so existing generated agents keep working unchanged.

Modules:
    throttle           per-session cooldown + cap for after-turn forks
    fork_utils         pure helpers (snapshot formatting, tool whitelist)
    sibling_runner     fire-and-forget runner lifecycle + shutdown drain
    review_fork        after_agent_callback judge fork (durable-fact capture)
    preload            relevance-conditioned memory surfacing for the prompt
    org_backend        optional OrgMemoryService retrieval backend
    profile            structured per-user profile (## User Profile block)
    consolidation      periodic dedupe/reconcile "dream" pass
"""

from .throttle import try_claim
from .sibling_runner import SiblingRunner, SIBLING_RUNNER
from .review_fork import review_fork_callback, REVIEW_FORK_ENABLED_ENV

__all__ = [
    "try_claim",
    "SiblingRunner",
    "SIBLING_RUNNER",
    "review_fork_callback",
    "REVIEW_FORK_ENABLED_ENV",
]
