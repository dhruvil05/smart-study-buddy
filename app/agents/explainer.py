"""ExplainerAgent — produces a beginner-friendly explanation of a topic."""
from nexus_a2a import agent, Task

from app.core.llm import llm


@agent(
    name="ExplainerAgent",
    description="Explains any topic in clear, beginner-friendly language with key concepts.",
    skills=[{"id": "explain", "name": "Explain", "description": "Simplify and explain a topic"}],
    url="http://localhost:8001",
)
class ExplainerAgent:
    """Generates a short, student-friendly explanation of the input topic."""

    async def run(self, task: Task) -> str:
        topic = task.history[0].parts[0].content if task.history else ""
        return llm(
            f"Explain this topic for a student in simple language.\n"
            f"List 3-5 key concepts as bullet points. Under 200 words.\n\nTopic:\n{topic}",
            system="You are an expert teacher. Make complex topics simple and engaging.",
        )
