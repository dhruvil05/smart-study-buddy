"""ExplainerAgent — produces a beginner-friendly explanation of a topic."""
from nexus_a2a import agent, Task

from app.core.llm import llm, get_current_language, get_language_prompt_template


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
        language = get_current_language()
        return llm(
            f"{get_language_prompt_template(language)}\n\n"
            f"Explain this topic for a student in simple language.\n"
            f"List 3-5 key concepts as bullet points. Under 200 words.\n\n"
            f"Topic:\n{topic}",
            system=f"You are an expert teacher who explains concepts in {language}. "
                   f"Make complex topics simple, engaging, and culturally relevant "
                   f"for {language} language learners.",
        )
