"""Thread-safe rate limiting for outbound LLM API calls, shared across every
agent. Two independent limiters — OpenAI and Anthropic have separate quotas.
"""

import threading
import time
from typing import Callable, TypeVar

from dotenv import load_dotenv

from src.utils.env import required_env_int

load_dotenv()

_T = TypeVar("_T")

OPENAI_MAX_CONCURRENCY = required_env_int("OPENAI_MAX_CONCURRENCY")
OPENAI_RPM = required_env_int("OPENAI_RPM")
ANTHROPIC_MAX_CONCURRENCY = required_env_int("ANTHROPIC_MAX_CONCURRENCY")
ANTHROPIC_RPM = required_env_int("ANTHROPIC_RPM")
RATE_LIMITER_ACQUIRE_TIMEOUT_SECONDS = required_env_int("RATE_LIMITER_ACQUIRE_TIMEOUT_SECONDS")


class _TokenBucket:
    """Allows up to `rate_per_minute` calls per rolling 60-second window."""

    def __init__(self, rate_per_minute: int):
        self._capacity = float(rate_per_minute)
        self._tokens = float(rate_per_minute)
        self._refill_per_second = rate_per_minute / 60.0
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_second)


class ProviderRateLimiter:
    """Caps concurrency and per-minute call rate for one LLM provider."""

    def __init__(
        self, provider: str, max_concurrency: int, rate_per_minute: int, acquire_timeout: float
    ):
        self._provider = provider
        self._acquire_timeout = acquire_timeout
        self._semaphore = threading.Semaphore(max_concurrency)
        self._bucket = _TokenBucket(rate_per_minute)

    def call(self, fn: Callable[..., _T], *args, **kwargs) -> _T:
        if not self._semaphore.acquire(timeout=self._acquire_timeout):
            raise TimeoutError(
                f"{self._provider} rate limiter: no concurrency slot available within "
                f"{self._acquire_timeout}s (max_concurrency reached)"
            )
        try:
            if not self._bucket.acquire(self._acquire_timeout):
                raise TimeoutError(
                    f"{self._provider} rate limiter: no rate-limit token available within "
                    f"{self._acquire_timeout}s (requests-per-minute limit reached)"
                )
            return fn(*args, **kwargs)
        finally:
            self._semaphore.release()


_openai_limiter = ProviderRateLimiter(
    "openai", OPENAI_MAX_CONCURRENCY, OPENAI_RPM, RATE_LIMITER_ACQUIRE_TIMEOUT_SECONDS
)
_anthropic_limiter = ProviderRateLimiter(
    "anthropic", ANTHROPIC_MAX_CONCURRENCY, ANTHROPIC_RPM, RATE_LIMITER_ACQUIRE_TIMEOUT_SECONDS
)


def call_openai(fn: Callable[..., _T], *args, **kwargs) -> _T:
    return _openai_limiter.call(fn, *args, **kwargs)


def call_anthropic(fn: Callable[..., _T], *args, **kwargs) -> _T:
    return _anthropic_limiter.call(fn, *args, **kwargs)
