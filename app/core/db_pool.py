"""Lightweight SQLite connection pool (Phase 3 – Connection Pooling).

The analytics store used to open a fresh ``sqlite3`` connection on every
request.  Under load (many concurrent review POSTs + dashboard polls) that
thrashes the filesystem and can trigger ``database is locked`` errors.  This
module hands out reusable connections from a small per‑database pool.

Key design points:

* **Path‑aware** – the pool is keyed by the database file path.  This matters
  because the test‑suite overrides ``analytics._DB_PATH`` per test; a naive
  module‑level capture of ``_DB_PATH`` would keep pointing at the production
  database and break test isolation.  We read the live value via the
  ``analytics`` module object instead.
* **Stdlib only** – no external dependency, keeping the UV‑only footprint.
* **Graceful fallback** – if the pool is exhausted a one‑off connection is
  returned (and simply closed on release).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Dict, List, Optional

import app.core.analytics as analytics_mod

_POOL_SIZE = int(os.environ.get("SQLITE_POOL_SIZE", "5"))
_lock = threading.Lock()
_pool: Dict[str, List[sqlite3.Connection]] = {}
_active: Dict[str, int] = {}


def _db_path() -> str:
    """Return the *current* analytics database path (live lookup)."""
    return analytics_mod._DB_PATH


def _new_connection(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    # Tag the connection with its path so release knows where it belongs.
    try:
        conn._pool_path = path  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 – sqlite3 connections allow attributes
        pass
    return conn


def get_connection(path: Optional[str] = None) -> sqlite3.Connection:
    """Return a pooled connection for *path* (defaults to the analytics DB)."""
    if path is None:
        path = _db_path()
    with _lock:
        bucket = _pool.setdefault(path, [])
        if bucket:
            return bucket.pop()
        active = _active.get(path, 0)
        if active < _POOL_SIZE:
            _active[path] = active + 1
            return _new_connection(path)
    # Pool exhausted for this path – hand out a one‑off connection.
    return _new_connection(path)


def release_connection(conn: sqlite3.Connection) -> None:
    """Return *conn* to its pool bucket (or close it if the pool is full)."""
    path = getattr(conn, "_pool_path", None)
    if path is None:
        conn.close()
        return
    with _lock:
        bucket = _pool.setdefault(path, [])
        if len(bucket) < _POOL_SIZE:
            bucket.append(conn)
            return
    conn.close()
    with _lock:
        _active[path] = max(0, _active.get(path, 1) - 1)


class connection:  # noqa: N801 – context‑manager style name
    """Context manager that yields a pooled connection and releases it after.

    Usage::

        with db_pool.connection() as conn:
            conn.execute(...)
    """

    def __enter__(self) -> sqlite3.Connection:
        self._conn = get_connection()
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> Optional[bool]:
        release_connection(self._conn)
        return None
