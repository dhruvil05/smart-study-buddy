"""Retry strategy with exponential backoff and a simple circuit breaker.

This module provides:

* ``retry`` – a decorator that retries a failing callable with exponential
  backoff (with optional jitter) up to ``max_attempts`` times.
* ``CircuitBreaker`` – a lightweight per‑provider breaker that short‑circuits
  calls when the failure rate exceeds a threshold, preventing cascading failures
  against an unhealthy upstream (LLM provider or Redis).
"""

import time
import logging
import threading
from typing import Callable, Type, Tuple
from functools import wraps

logger = logging.getLogger(__name__)

# Counters for the metrics dashboard (Phase 3 – Metrics Dashboard).
_ATTEMPTS = 0
_CIRCUIT_TRIPS = 0
_FAILURES = 0


def stats() -> dict:
    """Return retry/circuit‑breaker counters for the metrics dashboard."""
    return {
        "attempts": _ATTEMPTS,
        "failures": _FAILURES,
        "circuit_trips": _CIRCUIT_TRIPS,
    }


def retry(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    backoff_factor: float = 2.0,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
) -> Callable:
    """Return a decorator that retries the wrapped callable.

    The wait time before attempt *n* (1‑indexed) is ``base_delay *
    backoff_factor ** (n‑1)`` seconds.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            global _ATTEMPTS
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                _ATTEMPTS += 1
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    sleep_for = base_delay * (backoff_factor ** (attempt - 1))
                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed: "
                        f"{exc}. Retrying in {sleep_for:.2f}s"
                    )
                    time.sleep(sleep_for)
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator


class CircuitBreaker:
    """A minimal circuit breaker.

    Tracks failures within a rolling window.  Once ``failure_threshold``
    consecutive failures occur, the breaker opens and blocks calls for
    ``cooldown`` seconds, after which it half‑opens to test recovery.
    """

    def __init__(self, failure_threshold: int = 5, cooldown: float = 30.0):
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown
        self._failures = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        """True when the breaker is currently open (calls should be blocked)."""
        if self._failures < self._failure_threshold:
            return False
        with self._lock:
            if time.time() - self._opened_at >= self._cooldown:
                # Cooldown elapsed → half‑open: allow one trial call.
                self._failures = 0
                return False
            return True

    def record_failure(self) -> None:
        global _FAILURES, _CIRCUIT_TRIPS
        with self._lock:
            if self._failures == 0:
                self._opened_at = time.time()
            self._failures += 1
            if self._failures == self._failure_threshold:
                _CIRCUIT_TRIPS += 1

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0

    def __call__(self, func: Callable) -> Callable:
        """Decorator form – wraps a callable with circuit‑breaker protection."""

        @wraps(func)
        def wrapper(*args, **kwargs):
            if self.is_open:
                raise RuntimeError("Circuit breaker open – upstream unavailable")
            try:
                result = func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                self.record_failure()
                raise exc
            self.record_success()
            return result

        return wrapper


# Per‑provider circuit breakers (shared across calls within a process).
_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(name: str) -> CircuitBreaker:
    """Return (creating if needed) a circuit breaker keyed by *name*."""
    if name not in _BREAKERS:
        _BREAKERS[name] = CircuitBreaker()
    return _BREAKERS[name]
