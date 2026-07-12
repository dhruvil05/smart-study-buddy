"""Study agents built on top of nexus-a2a.

Each agent lives in its own module so new agents (e.g. TrueFalseQuizAgent)
can be added without touching the API layer.
"""
from app.agents.explainer import ExplainerAgent
from app.agents.quiz import QuizMakerAgent
from app.agents.flashcard import FlashcardAgent

ALL_AGENTS = [ExplainerAgent, QuizMakerAgent, FlashcardAgent]

__all__ = ["ExplainerAgent", "QuizMakerAgent", "FlashcardAgent", "ALL_AGENTS"]
