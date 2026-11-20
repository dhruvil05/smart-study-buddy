"""Contract tests for the signed-out study composer markup.

These keep the presentation-only composer redesign from dropping the IDs and
handlers that the rest of the single-page frontend depends on.
"""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


INDEX_PATH = Path(__file__).resolve().parents[2] / "app" / "static" / "index.html"

REQUIRED_IDS = {
    "ui-topic-title",
    "ui-topic-desc",
    "topic-input",
    "study-btn",
    "study-btn-label",
    "quiz-type-select",
    "quiz-segment",
    "stream-toggle",
    "error-box",
}

QUIZ_HANDLERS = {
    "mcq": "setQuizType('mcq')",
    "tf": "setQuizType('tf')",
    "fi": "setQuizType('fi')",
    "sa": "setQuizType('sa')",
}


class _ComposerMarkupParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class ComposerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = INDEX_PATH.read_text(encoding="utf-8")
        parser = _ComposerMarkupParser()
        parser.feed(cls.source)
        cls.elements = parser.elements
        cls.elements_by_id = {
            attrs["id"]: (tag, attrs)
            for tag, attrs in parser.elements
            if "id" in attrs
        }

    def test_required_composer_ids_occur_once(self):
        ids = [
            attrs["id"]
            for _tag, attrs in self.elements
            if "id" in attrs
        ]
        for element_id in sorted(REQUIRED_IDS):
            self.assertEqual(
                ids.count(element_id),
                1,
                f"expected exactly one id={element_id!r}",
            )

    def test_quiz_shortcuts_and_handlers_remain(self):
        actual = {}
        for tag, attrs in self.elements:
            if tag != "button" or "data-qt" not in attrs:
                continue
            quiz_type = attrs["data-qt"]
            self.assertNotIn(quiz_type, actual, f"duplicate quiz value {quiz_type!r}")
            self.assertIn("seg", attrs.get("class", "").split())
            self.assertEqual(attrs.get("role"), "tab")
            actual[quiz_type] = attrs.get("onclick")

        self.assertEqual(actual, QUIZ_HANDLERS)

    def test_stream_and_study_handlers_remain(self):
        stream_tag, stream_attrs = self.elements_by_id["stream-toggle"]
        self.assertEqual(stream_tag, "input")
        self.assertEqual(stream_attrs.get("type"), "checkbox")
        self.assertEqual(stream_attrs.get("onchange"), "syncStreamLabel()")

        study_tag, study_attrs = self.elements_by_id["study-btn"]
        self.assertEqual(study_tag, "button")
        self.assertEqual(study_attrs.get("onclick"), "submitStudy()")

    def test_topic_input_ctrl_or_cmd_enter_listener_remains(self):
        listener = re.search(
            r"document\.getElementById\(['\"]topic-input['\"]\)"
            r"\.addEventListener\(['\"]keydown['\"]([^\n]+)",
            self.source,
        )
        self.assertIsNotNone(listener, "topic-input keydown listener is missing")
        listener_body = listener.group(1)
        self.assertRegex(listener_body, r"e\.key\s*===\s*['\"]Enter['\"]")
        self.assertRegex(listener_body, r"e\.ctrlKey\s*\|\|\s*e\.metaKey")
        self.assertIn("submitStudy()", listener_body)


if __name__ == "__main__":
    unittest.main()
