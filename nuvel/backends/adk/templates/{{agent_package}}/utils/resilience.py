"""
Resilience utilities: circuit breaker and rate limiter.

Lightweight implementations with no external dependencies.
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ── Circuit Breaker ──────────────────────────────────────────────────


class CircuitState(Enum):
    CLOSED = "closed"          # normal operation
    OPEN = "open"              # failing fast
    HALF_OPEN = "half_open"    # testing recovery


class CircuitBreaker:
    """
    Async circuit breaker for external services.

    - CLOSED: requests pass through normally
    - OPEN: requests fail immediately (after `failure_threshold` consecutive failures)
    - HALF_OPEN: after `recovery_timeout`, allows one probe request

    Usage:
        cb = CircuitBreaker("postgres", failure_threshold=5, recovery_timeout=30)
        if not cb.allow_request():
            return {"status": "error", "message": "Service temporarily unavailable"}
        try:
            result = await do_work()
            cb.record_success()
        except Exception:
            cb.record_failure()
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        current = self.state
        if current == CircuitState.CLOSED:
            return True
        if current == CircuitState.HALF_OPEN:
            return True  # allow probe
        # OPEN
        logger.warning("[CircuitBreaker:%s] OPEN — rejecting request", self.name)
        return False

    def record_success(self) -> None:
        """Record a successful request."""
        if self._state != CircuitState.CLOSED:
            logger.info("[CircuitBreaker:%s] recovered -> CLOSED", self.name)
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed request."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            if self._state != CircuitState.OPEN:
                logger.error(
                    "[CircuitBreaker:%s] OPEN after %d failures",
                    self.name, self._failure_count,
                )
            self._state = CircuitState.OPEN

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._failure_count = 0
        self._state = CircuitState.CLOSED


# ── Rate Limiter (Token Bucket) ─────────────────────────────────────


class RateLimiter:
    """
    Simple token bucket rate limiter.

    Refills `rate` tokens per second, up to `burst` capacity.

    Usage:
        limiter = RateLimiter(rate=2.0, burst=5)  # 2 req/s, burst of 5
        if not limiter.allow():
            return {"status": "error", "message": "Rate limit exceeded"}
    """

    def __init__(self, rate: float = 2.0, burst: int = 10):
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
        self._last_refill = now

    def allow(self) -> bool:
        """Check if a request is allowed. Consumes one token if yes."""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    @property
    def available_tokens(self) -> float:
        self._refill()
        return self._tokens


# ── Singleton instances (shared across the app) ─────────────────────

db_circuit = CircuitBreaker(
    name="postgres",
    failure_threshold=5,
    recovery_timeout=30.0,
)

tool_rate_limiter = RateLimiter(
    rate=float(__import__("os").getenv("TOOL_RATE_LIMIT", "5.0")),     # requests/sec
    burst=int(__import__("os").getenv("TOOL_RATE_BURST", "20")),       # burst capacity
)
