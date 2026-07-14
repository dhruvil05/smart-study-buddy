'''Analytics and spaced repetition storage for flashcards.

This module provides a lightweight SQLite-backed analytics layer that tracks
user interactions with flashcards and schedules future reviews using a simple
SM‑2 spaced‑repetition algorithm. It is deliberately minimal – only the
standard library is used so the dependency footprint stays tiny.

Typical workflow:

1. When flashcards are generated (e.g. by ``FlashcardAgent``) the caller can
   store them via :func:`store_flashcards`.  The initial review interval is
   1 day.
2. The frontend fetches due flashcards via :func:`get_due_flashcards` and
   displays them to the user.
3. After the user answers, the frontend POSTs the result to the ``/api/flashcards/review``
   endpoint.  The payload contains ``term`` and a boolean ``correct`` flag.
   The SM‑2 algorithm updates the interval, easiness factor and next due date.
4. Interaction statistics (retry counts, average success rate, etc.) are
   available via the ``/api/analytics/flashcards`` endpoint.

The implementation is deliberately straightforward – a single ``flashcards``
table stores a JSON blob for the flashcard content plus scheduling fields.
All functions are thread‑safe because SQLite is used in ``AUTOCOMMIT`` mode and
connections are created per request.
'''

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple

# ---------------------------------------------------------------------------
# Database helper – creates ``analytics.db`` in the project root if it does not
# exist and ensures the required schema.
# ---------------------------------------------------------------------------

_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "analytics.db"))


def _ensure_db() -> None:
    """Create the SQLite database and ``flashcards`` table if missing.

    The schema stores:
    - ``id``                INTEGER PRIMARY KEY AUTOINCREMENT
    - ``term``              TEXT (the flashcard term)
    - ``definition``        TEXT (the flashcard definition)
    - ``created_at``        TEXT ISO‑8601 timestamp
    - ``next_due``          TEXT ISO‑8601 timestamp of the next review
    - ``interval``          INTEGER days until the next review
    - ``repetitions``       INTEGER count of successful reviews
    - ``efactor``           REAL easiness factor (SM‑2, defaults to 2.5)
    - ``review_count``      INTEGER total number of reviews attempted
    - ``success_count``     INTEGER total number of correct answers
    """
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS flashcards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL,
                definition TEXT NOT NULL,
                created_at TEXT NOT NULL,
                next_due TEXT NOT NULL,
                interval INTEGER NOT NULL,
                repetitions INTEGER NOT NULL,
                efactor REAL NOT NULL,
                review_count INTEGER NOT NULL,
                success_count INTEGER NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


# Ensure the DB is ready at import time.
_ensure_db()


# ---------------------------------------------------------------------------
# Core API – called from FastAPI routes.
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    """Return a pooled SQLite connection (see ``app.core.db_pool``)."""
    from app.core import db_pool
    return db_pool.get_connection()


def _release_conn(conn: sqlite3.Connection) -> None:
    """Return *conn* to the pool (replaces ``conn.close()``)."""
    from app.core import db_pool
    db_pool.release_connection(conn)


def store_flashcards(flashcards: List[Dict[str, str]]) -> int:
    """Insert a list of flashcards into the database.

    Each item should contain ``term`` and ``definition`` keys.  Malformed or
    incomplete entries are skipped (with a warning) rather than aborting the
    whole batch, so a single bad card never prevents the rest from being
    persisted.  Returns the number of cards actually stored.

    The initial scheduling follows SM‑2 defaults: interval = 1 day,
    repetitions = 0, efactor = 2.5.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    stored = 0
    conn = _connect()
    try:
        cur = conn.cursor()
        for fc in flashcards:
            term = fc.get("term") if isinstance(fc, dict) else None
            definition = fc.get("definition") if isinstance(fc, dict) else None
            if not term or not definition:
                print(f"[WARN] Skipping flashcard without term/definition: {fc!r}")
                continue
            cur.execute(
                """
                INSERT INTO flashcards (
                    term, definition, created_at, next_due, interval,
                    repetitions, efactor, review_count, success_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(term),
                    str(definition),
                    now,
                    now,  # first review is immediate
                    1,    # interval = 1 day
                    0,
                    2.5,
                    0,
                    0,
                ),
            )
            stored += 1
        conn.commit()
    finally:
        _release_conn(conn)
    return stored


def get_due_flashcards(limit: int = 20) -> List[Dict[str, Any]]:
    """Return flashcards whose ``next_due`` is now or in the past.

    The result is ordered by ``next_due`` ascending so the most overdue cards
    appear first.  ``limit`` prevents loading the entire table in a single call.
    """
    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, term, definition FROM flashcards
            WHERE next_due <= ?
            ORDER BY next_due ASC
            LIMIT ?
            """,
            (now_iso, limit),
        )
        rows = cur.fetchall()
        return [{"id": r[0], "term": r[1], "definition": r[2]} for r in rows]
    finally:
        _release_conn(conn)


def record_review(flashcard_id: int, correct: bool) -> None:
    """Update scheduling fields for a flashcard based on the SM‑2 algorithm.

    ``correct`` indicates whether the user answered the card correctly.  The
    algorithm:
    1. Increment ``review_count``.
    2. If correct, increment ``success_count`` and update ``repetitions``.
    3. Adjust ``efactor`` (minimum 1.3) and compute the next ``interval`` in days.
    4. Set ``next_due`` to ``now + interval``.
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        # Fetch current scheduling state.
        cur.execute(
            "SELECT interval, repetitions, efactor FROM flashcards WHERE id = ?",
            (flashcard_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Flashcard id={flashcard_id} not found")
        interval, repetitions, efactor = row
        # Update counters.
        review_count_inc = 1
        success_inc = 1 if correct else 0
        if correct:
            repetitions += 1
            # SM‑2 easiness factor update – quality = 5 for correct.
            # Using the classic formula: ef' = ef + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
            # With q=5 this simplifies to ef' = ef + 0.1
            efactor = max(1.3, efactor + 0.1)
            # Interval calculation per SM‑2.
            if repetitions == 1:
                interval = 1
            elif repetitions == 2:
                interval = 6
            else:
                interval = int(interval * efactor)
        else:
            # Failure resets the repetition count.
            repetitions = 0
            interval = 1
            # Slightly decrease efactor but keep above 1.3.
            efactor = max(1.3, efactor - 0.2)
        next_due = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=interval)).isoformat()
        # Apply updates.
        cur.execute(
            """
            UPDATE flashcards
            SET interval = ?, repetitions = ?, efactor = ?, next_due = ?,
                review_count = review_count + ?, success_count = success_count + ?
            WHERE id = ?
            """,
            (interval, repetitions, efactor, next_due, review_count_inc, success_inc, flashcard_id),
        )
        conn.commit()
    finally:
        _release_conn(conn)


def flashcard_statistics() -> Dict[str, Any]:
    """Return aggregated analytics for all flashcards.

    The dictionary contains:
    - ``total``            total number of flashcards stored
    - ``due_today``        count of cards whose ``next_due`` is past due
    - ``average_success``  ratio of correct answers over total reviews (0‑1)
    - ``average_interval`` average interval in days for the next review
    """
    conn = _connect()
    try:
        _ensure_db()  # tolerate a path whose schema hasn't been created yet
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM flashcards")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM flashcards WHERE next_due <= ?", (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),))
        due_today = cur.fetchone()[0]
        cur.execute("SELECT SUM(success_count), SUM(review_count) FROM flashcards")
        succ_sum, rev_sum = cur.fetchone()
        average_success = (succ_sum / rev_sum) if rev_sum else 0.0
        cur.execute("SELECT AVG(interval) FROM flashcards")
        average_interval = cur.fetchone()[0] or 0
        return {
            "total": total,
            "due_today": due_today,
            "average_success": round(average_success, 3),
            "average_interval": round(average_interval, 2),
        }
    finally:
        _release_conn(conn)
