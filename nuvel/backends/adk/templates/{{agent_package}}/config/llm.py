"""
LLM configuration for the Data Analysis Agent.
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
    "HTTP-Referer": os.getenv(
        "OPENROUTER_REFERER",
        "https://github.com/your-org/{{agent_name}}",
    ),
    "X-Title": os.getenv("OPENROUTER_TITLE", "{{agent_name}}"),
}

FAST_MODEL = LiteLlm(
    model=os.getenv("FAST_MODEL", "{{default_fast_model}}"),
    extra_headers=_OPENROUTER_HEADERS,
)

REASONING_MODEL = LiteLlm(
    model=os.getenv("REASONING_MODEL", "{{default_reasoning_model}}"),
    extra_headers=_OPENROUTER_HEADERS,
)

# Streaming model — Gemini directly (not via LiteLLM).
# Only used when STREAMING_ENABLED=true.
LIVE_MODEL = os.getenv("LIVE_MODEL", "gemini-2.0-flash-live-001")
