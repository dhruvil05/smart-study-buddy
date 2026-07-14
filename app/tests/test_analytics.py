"""Tests for the flashcard analytics + spaced-repetition layer (Phase 2.3).

These exercise ``app.core.analytics`` directly against a temporary SQLite file
so they never touch the production ``analytics.db`` and need no network access.
"""
import os
import tempfile
import unittest

from app.core import analytics as analytics_mod


class FlashcardAnalyticsTests(unittest.TestCase):
    def setUp(self):
        # Point the module at a fresh temp DB and (re)create the schema.
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        analytics_mod._DB_PATH = self._tmp.name
        analytics_mod._ensure_db()

    def tearDown(self):
        if os.path.exists(self._tmp.name):
            os.remove(self._tmp.name)

    def test_store_and_due(self):
        cards = [
            {"term": "Photosynthesis", "definition": "Process converting light to energy"},
            {"term": "Mitosis", "definition": "Cell division"},
        ]
        analytics_mod.store_flashcards(cards)
        due = analytics_mod.get_due_flashcards()
        self.assertEqual(len(due), 2)
        terms = {c["term"] for c in due}
        self.assertEqual(terms, {"Photosynthesis", "Mitosis"})

    def test_store_skips_malformed_without_losing_batch(self):
        # Regression: a single malformed card must not abort/roll back the
        # entire batch.  Previously this stored 0 rows (the empty DB bug).
        cards = [
            {"term": "Mitosis", "definition": "Cell division"},
            {"weird_key": "not a real card"},  # missing term/definition
            {"term": "Osmosis", "definition": "Water diffusion"},
        ]
        stored = analytics_mod.store_flashcards(cards)
        self.assertEqual(stored, 2)
        due = analytics_mod.get_due_flashcards()
        self.assertEqual(len(due), 2)
        self.assertEqual({c["term"] for c in due}, {"Mitosis", "Osmosis"})

    def test_record_review_correct_increments_interval(self):
        analytics_mod.store_flashcards([{"term": "T1", "definition": "D1"}])
        fc = analytics_mod.get_due_flashcards()[0]
        analytics_mod.record_review(fc["id"], correct=True)
        stats = analytics_mod.flashcard_statistics()
        # After one correct review the success rate should be 1.0
        self.assertEqual(stats["average_success"], 1.0)
        self.assertGreaterEqual(stats["total"], 1)

    def test_record_review_unknown_id_raises(self):
        with self.assertRaises(ValueError):
            analytics_mod.record_review(999999, correct=True)

    def test_failed_review_lowers_success(self):
        analytics_mod.store_flashcards([{"term": "T2", "definition": "D2"}])
        fc = analytics_mod.get_due_flashcards()[0]
        analytics_mod.record_review(fc["id"], correct=False)
        stats = analytics_mod.flashcard_statistics()
        self.assertEqual(stats["average_success"], 0.0)

    def test_statistics_empty(self):
        stats = analytics_mod.flashcard_statistics()
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["average_success"], 0.0)


if __name__ == "__main__":
    unittest.main()
