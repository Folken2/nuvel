"""
LLM configuration for the Meta Agent.
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
        "https://github.com/Folken2/nuvel",
    ),
    "X-Title": os.getenv("OPENROUTER_TITLE", "nuvel"),
}

FAST_MODEL = LiteLlm(
    model=os.getenv("FAST_MODEL", "openrouter/moonshotai/kimi-k2.5"),
    extra_headers=_OPENROUTER_HEADERS,
)

REASONING_MODEL = LiteLlm(
    model=os.getenv("REASONING_MODEL", "openrouter/google/gemini-3-pro-preview"),
    extra_headers=_OPENROUTER_HEADERS,
)
