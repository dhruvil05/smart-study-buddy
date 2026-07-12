"""FlashcardAgent — creates flashcards for spaced repetition from an explanation."""
from nexus_a2a import agent, Task

from app.core.llm import llm, get_current_language, get_language_prompt_template


@agent(
    name="FlashcardAgent",
    description="Creates 6 term-definition flashcard pairs for spaced repetition.",
    skills=[{"id": "flashcard", "name": "Flashcard", "description": "Generate flashcards"}],
    url="http://localhost:8003",
)
class FlashcardAgent:
    """Builds 6 term/definition flashcards (valid JSON) from an explanation."""

    async def run(self, task: Task) -> str:
        explanation = task.history[0].parts[0].content if task.history else ""
        language = get_current_language()
        return llm(
            f"{get_language_prompt_template(language)}\n\n"
            f"Create exactly 6 flashcards from this explanation.\n"
            f"Return ONLY valid JSON array, no markdown fences.\n"
            f'Format: [{{"term":"Term","definition":"One-sentence definition"}}]\n\n'
            f"Explanation:\n{explanation}",
            system=f"You are a flashcard creator who writes in {language}. "
                   f"Return valid JSON only. No markdown ever. Make terms and definitions "
                   f"culturally appropriate for {language} language learners.",
        )
