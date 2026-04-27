"""
Query cache management for the Data Analysis Agent.

TTL-based cache with state scope prefixes for appropriate persistence.

Concurrency note: This cache is stored in ADK's tool_context.state which is
scoped per-session. ADK serializes tool calls within a single invocation,
so concurrent writes to the same session state don't occur in practice.
"""

import hashlib
import logging
import os
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# Configuration (env-driven with sensible defaults)
MAX_CACHE_SIZE = int(os.getenv("CACHE_MAX_SIZE", "10"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))
CACHE_STATE_KEY = "query_cache"


@dataclass
class CacheEntry:
    """Lightweight cache entry for optimized tool responses."""

    cache_key: str  # Hash of tool name + normalized args
    tool_name: str
    response: Dict[str, Any]  # Full response (now small ~300-400 tokens)
    timestamp: float
    hit_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CacheEntry":
        return cls(**data)

    def is_expired(self, ttl: int = CACHE_TTL_SECONDS) -> bool:
        return (time.time() - self.timestamp) > ttl


def _generate_cache_key(tool_name: str, args: Dict[str, Any]) -> str:
    """Generate a deterministic cache key from tool name and args."""
    # Sort args for consistent hashing
    sorted_args = sorted(
        ((k, v) for k, v in args.items() if k != "tool_context"),
        key=lambda x: x[0]
    )
    key_string = f"{tool_name}:{sorted_args}"
    return hashlib.md5(key_string.encode()).hexdigest()[:16]


def get_cache(state: Dict[str, Any]) -> Dict[str, CacheEntry]:
    """Get cache dictionary from state."""
    cache_data = state.get(CACHE_STATE_KEY, {})
    if not cache_data:
        return {}

    try:
        return {k: CacheEntry.from_dict(v) for k, v in cache_data.items()}
    except Exception as e:
        logger.warning(f"Failed to deserialize cache: {e}")
        return {}


def save_cache(state: Dict[str, Any], cache: Dict[str, CacheEntry]) -> None:
    """Save cache dictionary to state."""
    state[CACHE_STATE_KEY] = {k: v.to_dict() for k, v in cache.items()}


def cache_get(
    state: Dict[str, Any],
    tool_name: str,
    args: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Get cached response if available and not expired.

    Args:
        state: tool_context.state
        tool_name: Name of the tool
        args: Tool arguments

    Returns:
        Cached response dict if found and valid, None otherwise
    """
    cache_key = _generate_cache_key(tool_name, args)
    cache = get_cache(state)

    entry = cache.get(cache_key)
    if not entry:
        logger.debug(f"Cache MISS: {tool_name} (key not found)")
        return None

    if entry.is_expired():
        # Remove expired entry
        del cache[cache_key]
        save_cache(state, cache)
        logger.debug(f"Cache MISS: {tool_name} (expired)")
        return None

    # Update hit count
    entry.hit_count += 1
    save_cache(state, cache)

    cache_age = int(time.time() - entry.timestamp)
    logger.info(f"Cache HIT: {tool_name} (age: {cache_age}s, hits: {entry.hit_count})")

    # Return response with cache metadata
    response = entry.response.copy()
    response["_cached"] = True
    response["_cache_age_seconds"] = cache_age
    return response


def cache_set(
    state: Dict[str, Any],
    tool_name: str,
    args: Dict[str, Any],
    response: Dict[str, Any]
) -> None:
    """
    Store tool response in cache.

    Args:
        state: tool_context.state
        tool_name: Name of the tool
        args: Tool arguments
        response: Tool response to cache
    """
    # Don't cache errors or already-cached responses
    if response.get("status") == "error" or response.get("_cached"):
        return

    cache_key = _generate_cache_key(tool_name, args)
    cache = get_cache(state)

    # Create entry
    entry = CacheEntry(
        cache_key=cache_key,
        tool_name=tool_name,
        response=response,
        timestamp=time.time(),
        hit_count=0
    )

    # Add to cache
    cache[cache_key] = entry

    # Enforce max size (remove oldest entries)
    if len(cache) > MAX_CACHE_SIZE:
        # Sort by timestamp, remove oldest
        sorted_entries = sorted(cache.items(), key=lambda x: x[1].timestamp)
        for key, _ in sorted_entries[:len(cache) - MAX_CACHE_SIZE]:
            del cache[key]

    save_cache(state, cache)
    logger.debug(f"Cached: {tool_name} (key: {cache_key[:8]}...)")


def cache_clear(state: Dict[str, Any]) -> int:
    """Clear all cache entries. Returns count of cleared entries."""
    cache = get_cache(state)
    count = len(cache)
    state[CACHE_STATE_KEY] = {}
    logger.info(f"Cache cleared: {count} entries removed")
    return count


def cache_stats(state: Dict[str, Any]) -> Dict[str, Any]:
    """Get cache statistics."""
    cache = get_cache(state)

    if not cache:
        return {"entries": 0, "tools": [], "total_hits": 0}

    # Clean expired entries
    valid_cache = {k: v for k, v in cache.items() if not v.is_expired()}
    if len(valid_cache) != len(cache):
        save_cache(state, valid_cache)
        cache = valid_cache

    return {
        "entries": len(cache),
        "tools": list(set(e.tool_name for e in cache.values())),
        "total_hits": sum(e.hit_count for e in cache.values()),
        "oldest_age_seconds": int(time.time() - min(e.timestamp for e in cache.values())) if cache else 0,
    }


# === Temp state helpers (for invocation-only data) ===

def temp_set(state: Dict[str, Any], key: str, value: Any) -> None:
    """Store temporary data that won't persist beyond current invocation."""
    state[f"temp:{key}"] = value


def temp_get(state: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Get temporary data."""
    return state.get(f"temp:{key}", default)


def temp_clear(state: Dict[str, Any], key: str) -> None:
    """Clear temporary data."""
    full_key = f"temp:{key}"
    if full_key in state:
        del state[full_key]
