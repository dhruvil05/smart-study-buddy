"""Tests for the retry + circuit‑breaker utilities (Phase 3)."""
import time
import unittest
from app.core import retry as retry_mod


class FakeRedis:
    """Minimal in‑memory stand‑in for a Redis client used by the cache tests."""
    def __init__(self):
        self._store = {}

    def ping(self):
        return True

    def get(self, key):
        return self._store.get(key)

    def setex(self, key, ttl, value):
        self._store[key] = value


class RetryTests(unittest.TestCase):
    def test_retry_succeeds_eventually(self):
        calls = {"n": 0}

        @retry_mod.retry(max_attempts=3, base_delay=0.0)
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("boom")
            return "ok"

        self.assertEqual(flaky(), "ok")
        self.assertEqual(calls["n"], 3)

    def test_retry_reraises_after_max(self):
        calls = {"n": 0}

        @retry_mod.retry(max_attempts=2, base_delay=0.0)
        def always_fail():
            calls["n"] += 1
            raise RuntimeError("nope")

        with self.assertRaises(RuntimeError):
            always_fail()
        self.assertEqual(calls["n"], 2)

    def test_circuit_breaker_opens_after_threshold(self):
        cb = retry_mod.CircuitBreaker(failure_threshold=3, cooldown=0.1)
        self.assertFalse(cb.is_open)
        for _ in range(3):
            cb.record_failure()
        self.assertTrue(cb.is_open)
        # After cooldown it half‑opens and resets.
        time.sleep(0.15)
        self.assertFalse(cb.is_open)

    def test_circuit_breaker_decorator_blocks_when_open(self):
        cb = retry_mod.CircuitBreaker(failure_threshold=1, cooldown=1.0)

        @cb
        def do_work():
            raise ValueError("boom")

        # First call fails → breaker opens (and the original error propagates).
        with self.assertRaises(ValueError):
            do_work()
        # Subsequent call is blocked by the open breaker.
        with self.assertRaises(RuntimeError):
            do_work()


if __name__ == "__main__":
    unittest.main()
