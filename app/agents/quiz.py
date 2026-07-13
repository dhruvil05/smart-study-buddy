"""QuizMakerAgent — generates quiz questions in multiple formats from an explanation.

Supported quiz types (set via `quiz_type`):
  - "mcq": Multiple-choice (4 options, one correct) + justification
  - "tf" : True/False statements + justification
  - "fi" : Fill-in-the-blank + justification
  - "sa" : Short-answer + justification
"""
from nexus_a2a import agent, Task

from app.core.llm import llm, get_current_language, get_language_prompt_template


@agent(
    name="QuizMakerAgent",
    description="Generates 5 questions in the chosen format (MCQ, TF, Fill-in, Short) with justifications.",
    skills=[{"id": "quiz", "name": "Quiz", "description": "Generate quiz questions in multiple formats"}],
    url="http://localhost:8002",
)
class QuizMakerAgent:
    """Generates a 5-question set (valid JSON) from an explanation based on `quiz_type`."""

    def __init__(self):
        self.quiz_type = "mcq"  # default; overridden per-request from StudyRequest

    async def run(self, task: Task) -> str:
        explanation = task.history[0].parts[0].content if task.history else ""
        language = get_current_language()
        quiz_type = getattr(self, "quiz_type", "mcq")

        if quiz_type == "tf":
            return await self._generate_true_false(explanation, language)
        elif quiz_type == "fi":
            return await self._generate_fill_in(explanation, language)
        elif quiz_type == "sa":
            return await self._generate_short_answer(explanation, language)
        return await self._generate_multiple_choice(explanation, language)

    async def _generate_multiple_choice(self, explanation: str, language: str) -> str:
        """Multiple-choice — the default behavior, now with justifications."""
        return llm(
            f"{get_language_prompt_template(language)}\n\n"
            f"Generate exactly 5 multiple choice questions from this explanation.\n"
            f'Every item MUST be a JSON object with these exact keys: '
            f'"type":"mcq", "q", "options" (array of exactly 4 strings), '
            f'"answer" (one of the option strings), "justification".\n'
            f"Return ONLY a valid JSON array. No markdown fences, no prose.\n\n"
            f"Explanation:\n{explanation}",
            system=f"You are a quiz generator who writes in {language}. "
                   f"Return valid JSON only. No markdown ever. Make questions culturally "
                   f"appropriate for {language} learners. Always include a 'justification' "
                   f"field explaining why the answer is correct.",
        )

    async def _generate_true_false(self, explanation: str, language: str) -> str:
        """True/False statements with justifications."""
        return llm(
            f"{get_language_prompt_template(language)}\n\n"
            f"Generate exactly 5 true/false statements from this explanation.\n"
            f'Every item MUST be a JSON object with these exact keys: '
            f'"type":"tf", "q" (a statement), "answer" (boolean true or false), '
            f'"justification".\n'
            f"Return ONLY a valid JSON array. No markdown fences, no prose.\n\n"
            f"Explanation:\n{explanation}",
            system=f"You are a quiz generator who writes in {language}. "
                   f"Return valid JSON only. No markdown ever. Make statements factually "
                   f"based on the explanation and culturally appropriate for {language} learners. "
                   f"Always include a 'justification' field explaining the true/false answer.",
        )

    async def _generate_fill_in(self, explanation: str, language: str) -> str:
        """Fill-in-the-blank questions with justifications."""
        return llm(
            f"{get_language_prompt_template(language)}\n\n"
            f"Generate exactly 5 fill-in-the-blank questions from this explanation.\n"
            f'Every item MUST be a JSON object with these exact keys: '
            f'"type":"fi", "q" (use ___ for the blank), "answer" (the missing word/phrase), '
            f'"justification".\n'
            f"Return ONLY a valid JSON array. No markdown fences, no prose.\n\n"
            f"Explanation:\n{explanation}",
            system=f"You are a quiz generator who writes in {language}. "
                   f"Return valid JSON only. No markdown ever. Use '___' as the blank. "
                   f"Make questions culturally appropriate for {language} learners. "
                   f"Always include a 'justification' field explaining the missing answer.",
        )

    async def _generate_short_answer(self, explanation: str, language: str) -> str:
        """Short-answer questions with justifications."""
        return llm(
            f"{get_language_prompt_template(language)}\n\n"
            f"Generate exactly 5 short-answer questions from this explanation.\n"
            f'Every item MUST be a JSON object with these exact keys: '
            f'"type":"sa", "q", "answer" (1-3 sentence answer drawn from the explanation), '
            f'"justification".\n'
            f"Return ONLY a valid JSON array. No markdown fences, no prose.\n\n"
            f"Explanation:\n{explanation}",
            system=f"You are a quiz generator who writes in {language}. "
                   f"Return valid JSON only. No markdown ever. Answers must be answerable "
                   f"from the explanation and culturally appropriate for {language} learners. "
                   f"Always include a 'justification' field explaining the answer.",
        )
