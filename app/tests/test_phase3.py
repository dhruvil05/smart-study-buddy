"""Tests for Phase 3 endpoints: streaming (SSE) and metrics dashboard.

These exercise the FastAPI app via Starlette's TestClient.  The LLM/agent
pipeline is mocked by monkeypatching ``_run_study`` so the tests run offline
and deterministically.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api import main as api_main


@pytest.fixture()
def client():
    return TestClient(api_main.app)


def _fake_run_study(topic="Photosynthesis", quiz_type="mcq", language="en"):
    """Build a coroutine that returns a canned ``_run_study`` result."""

    async def _fake(req):
        return {
            "task_id": "00000000-0000-0000-0000-000000000001",
            "topic": req.topic,
            "explanation": "Photosynthesis converts light into chemical energy.",
            "quiz": [{"q": "What powers photosynthesis?", "answer": "light", "type": "mcq"}],
            "flashcards": [{"term": "Chloroplast", "definition": "Site of photosynthesis."}],
            "meta": {
                "provider": "anthropic",
                "language": req.language or "en",
                "quiz_type": req.quiz_type,
                "total_sec": 1.23,
                "orchestration": "ExplainerAgent → (QuizMakerAgent ‖ FlashcardAgent)",
                "agents": ["ExplainerAgent", "QuizMakerAgent", "FlashcardAgent"],
                "package_mgr": "uv",
            },
        }

    return _fake


def test_metrics_dashboard_shape(client):
    """The dashboard endpoint aggregates cache, retry, and flashcard stats."""
    res = client.get("/api/metrics/dashboard")
    assert res.status_code == 200
    body = res.json()
    assert set(body) >= {"cache", "retry", "flashcard_stats", "tasks"}
    assert "hits" in body["cache"]
    assert "attempts" in body["retry"]
    assert "total" in body["flashcard_stats"]


def test_study_stream_emits_sse_events(client):
    """The stream endpoint returns SSE frames for explanation/quiz/flashcards/done."""
    with patch.object(api_main, "_run_study", new=_fake_run_study()):
        res = client.post(
            "/api/study/stream",
            json={"topic": "Photosynthesis", "quiz_type": "mcq", "language": "en"},
        )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    text = res.text
    assert 'event: explanation' in text
    assert 'event: quiz' in text
    assert 'event: flashcards' in text
    assert 'event: done' in text

    # The explanation chunk must carry the canned explanation text.
    assert "Photosynthesis converts light" in text
    # The quiz payload must round‑trip as valid JSON.
    qstart = text.index('event: quiz')
    # Take the block up to the first blank line — that is the quiz event frame.
    qblock = text[qstart:].split("\n\n", 1)[0]
    data_line = [l for l in qblock.splitlines() if l.startswith("data:")][0]
    quiz = json.loads(data_line[len("data: "):])
    assert quiz[0]["answer"] == "light"


def test_study_stream_error_event_on_empty_topic(client):
    """An empty topic is rejected with a single SSE ``error`` event."""
    res = client.post(
        "/api/study/stream",
        json={"topic": "   ", "quiz_type": "mcq", "language": "en"},
    )
    assert res.status_code == 200
    assert 'event: error' in res.text
    assert "cannot be empty" in res.text
