"""Simple Redis cache wrapper for LLM responses.

The cache is optional – if the Redis server is unavailable the wrapper falls
back to a no‑op implementation so the application continues to function.
"""

import os
import json
import hashlib
import logging
from typing import Any, Optional

try:
    import redis
except ImportError:  # pragma: no cover – Redis may not be installed in CI
    redis = None
    logging.getLogger(__name__).warning("redis-py not installed – cache disabled")

# Environment variable for Redis connection URL, e.g. redis://localhost:6379/0
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_CLIENT: Optional[redis.Redis] = None

# Simple counters for the metrics dashboard (Phase 3 – Metrics Dashboard).
_HITS = 0
_MISSES = 0
_ERRORS = 0


def stats() -> dict:
    """Return cache counters for the metrics dashboard."""
    total = _HITS + _MISSES
    hit_rate = (_HITS / total) if total else 0.0
    return {
        "enabled": _CLIENT is not None,
        "hits": _HITS,
        "misses": _MISSES,
        "errors": _ERRORS,
        "hit_rate": round(hit_rate, 3),
    }


def _get_client() -> Optional["redis.Redis"]:  # type: ignore[name-defined]
    """Lazy‑initialize a Redis client.

    Returns ``None`` if the ``redis`` package is missing or the connection fails.
    """
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if redis is None:
        return None
    try:
        _CLIENT = redis.from_url(_REDIS_URL)
        # Ping to verify connection; ignore failures silently.
        _CLIENT.ping()
        return _CLIENT
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(f"Redis connection failed: {exc}")
        _CLIENT = None
        return None


def _make_key(provider: str, prompt: str, system: str, language: str) -> str:
    """Create a deterministic cache key.

    The key incorporates the provider name and a SHA‑256 hash of the request
    components to avoid collisions while keeping the key length reasonable.
    """
    raw = f"{provider}|{prompt}|{system}|{language}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return f"llm:{provider}:{digest}"


def get(provider: str, prompt: str, system: str = "", language: str = "en") -> Optional[Any]:
    """Retrieve a cached response if present.

    Returns the original Python object (decoded from JSON) or ``None`` when the
    cache is disabled or the key is missing.
    """
    client = _get_client()
    if client is None:
        return None
    key = _make_key(provider, prompt, system, language)
    raw = client.get(key)
    if raw is None:
        global _MISSES
        _MISSES += 1
        return None
    global _HITS
    _HITS += 1
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def set(provider: str, prompt: str, response: Any, system: str = "", language: str = "en", ttl: int = 300) -> None:
    """Store *response* in the cache.

    ``response`` is JSON‑serialised before storage.  ``ttl`` defaults to five
    minutes – enough to hit the cache for rapid repeated study requests but not
    stale for changing LLM outputs.
    """
    client = _get_client()
    if client is None:
        return
    key = _make_key(provider, prompt, system, language)
    try:
        client.setex(key, ttl, json.dumps(response))
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(f"Failed to write to Redis cache: {exc}")
