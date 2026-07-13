"""Tests for QuizMakerAgent — Phase 2.2 Advanced Quiz Types.

These validate the expanded output schema (true/false, fill-in-the-blank,
short-answer, plus MCQ) and that every question carries a `justification`.

The real `nexus_a2a` SDK is not required to run these: if it isn't importable
we inject a lightweight stub so the agent module can be imported. The LLM
call is monkeypatched so no network/provider access happens.
"""
import asyncio
import json
import sys
import types
import unittest

# ── Make the agent importable without the real nexus-a2a SDK ──────────────────
if "nexus_a2a" not in sys.modules:
    _fake = types.ModuleType("nexus_a2a")

    def _agent(**kwargs):
        def _deco(cls):
            cls.__agent_meta__ = kwargs
            return cls

        return _deco

    _fake.agent = _agent
    _fake.Task = object
    sys.modules["nexus_a2a"] = _fake

from app.agents import quiz as quiz_mod
from app.agents.quiz import QuizMakerAgent


class _FakePart:
    def __init__(self, content):
        self.content = content


class _FakeMessage:
    def __init__(self, content):
        self.parts = [_FakePart(content)]


class _FakeTask:
    def __init__(self, content):
        self.history = [_FakeMessage(content)]


# Canned, schema-valid model responses keyed by quiz type.
_CANNED = {
    "mcq": json.dumps([
        {"type": "mcq", "q": "What is X?", "options": ["A", "B", "C", "D"],
         "answer": "A", "justification": "A is correct because ..."}
    ]),
    "tf": json.dumps([
        {"type": "tf", "q": "X is true.", "answer": True,
         "justification": "It is true because ..."}
    ]),
    "fi": json.dumps([
        {"type": "fi", "q": "The process is called ___", "answer": "photosynthesis",
         "justification": "Because plants convert light to energy."}
    ]),
    "sa": json.dumps([
        {"type": "sa", "q": "Explain X briefly.", "answer": "X happens when ...",
         "justification": "This captures the core mechanism."}
    ]),
}


class QuizMakerAgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = QuizMakerAgent()
        self.captured_prompts = []
        self.captured_systems = []

        def _fake_llm(prompt, system="", language=None):
            self.captured_prompts.append(prompt)
            self.captured_systems.append(system)
            return _CANNED.get(self.agent.quiz_type, _CANNED["mcq"])

        self._orig_llm = quiz_mod.llm
        quiz_mod.llm = _fake_llm

    def tearDown(self):
        quiz_mod.llm = self._orig_llm

    def _run(self):
        return asyncio.run(self.agent.run(_FakeTask("An explanation of the topic.")))

    def test_mcq_is_default_and_has_justification(self):
        self.agent.quiz_type = "mcq"
        out = self._run()
        data = json.loads(out)
        self.assertEqual(data[0]["type"], "mcq")
        self.assertEqual(len(data[0]["options"]), 4)
        self.assertIn("justification", data[0])
        # Prompt must instruct the model to include a justification + the type field.
        self.assertIn("justification", self.captured_prompts[0].lower())

    def test_true_false_schema(self):
        self.agent.quiz_type = "tf"
        data = json.loads(self._run())
        self.assertEqual(data[0]["type"], "tf")
        self.assertIsInstance(data[0]["answer"], bool)
        self.assertIn("justification", data[0])
        self.assertIn("true/false", self.captured_prompts[0].lower())

    def test_fill_in_schema(self):
        self.agent.quiz_type = "fi"
        data = json.loads(self._run())
        self.assertEqual(data[0]["type"], "fi")
        self.assertIn("justification", data[0])
        self.assertTrue("___" in data[0]["q"] or "blank" in self.captured_prompts[0].lower())

    def test_short_answer_schema(self):
        self.agent.quiz_type = "sa"
        data = json.loads(self._run())
        self.assertEqual(data[0]["type"], "sa")
        self.assertIn("justification", data[0])
        self.assertIn("short-answer", self.captured_prompts[0].lower())

    def test_unknown_type_falls_back_to_mcq(self):
        # The API normalises unknown values to "mcq"; verify the agent honours that.
        self.agent.quiz_type = "bogus"
        data = json.loads(self._run())
        self.assertEqual(data[0]["type"], "mcq")


if __name__ == "__main__":
    unittest.main()
