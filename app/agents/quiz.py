"""QuizMakerAgent — generates 5 multiple-choice questions from an explanation."""
from nexus_a2a import agent, Task

from app.core.llm import llm


@agent(
    name="QuizMakerAgent",
    description="Generates 5 MCQ questions with 4 options and correct answer.",
    skills=[{"id": "quiz", "name": "Quiz", "description": "Generate MCQ quiz questions"}],
    url="http://localhost:8002",
)
class QuizMakerAgent:
    """Builds a 5-question MCQ set (valid JSON) from an explanation."""

    async def run(self, task: Task) -> str:
        explanation = task.history[0].parts[0].content if task.history else ""
        return llm(
            f"Generate exactly 5 multiple choice questions from this explanation.\n"
            f"Return ONLY valid JSON array, no markdown fences.\n"
            f'Format: [{{"q":"Question?","options":["A","B","C","D"],"answer":"A"}}]\n\n'
            f"Explanation:\n{explanation}",
            system="You are a quiz generator. Return valid JSON only. No markdown ever.",
        )
