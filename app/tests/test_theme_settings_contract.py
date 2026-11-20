"""Contract tests for theme controls and the settings popover.

The settings popover only holds provider and language controls. Theme switching
stays in the navbar and mobile menu, and those controls must keep the same
handler.
"""
from html.parser import HTMLParser
from pathlib import Path
import unittest


INDEX_PATH = Path(__file__).resolve().parents[2] / "app" / "static" / "index.html"
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class _Node:
    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children = []
        self.text_parts = []


class _MarkupParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {}, None)
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].text_parts.append(data)


def _descendants(node):
    for child in node.children:
        yield child
        yield from _descendants(child)


def _text(node):
    own = "".join(node.text_parts)
    return own + "".join(_text(child) for child in node.children)


def _find_by_id(parser, element_id):
    return next(
        node
        for node in _descendants(parser.root)
        if node.attrs.get("id") == element_id
    )


class ThemeSettingsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = INDEX_PATH.read_text(encoding="utf-8")
        cls.parser = _MarkupParser()
        cls.parser.feed(cls.source)
        cls.elements = list(_descendants(cls.parser.root))

    def test_settings_popover_has_only_provider_and_language(self):
        popover = _find_by_id(self.parser, "settings-popover")
        self.assertNotIn("theme", _text(popover).lower())
        self.assertFalse(
            any(
                "set-theme" in node.attrs.get("class", "").split()
                for node in _descendants(popover)
            )
        )
        self.assertFalse(
            any(node.attrs.get("id") == "set-theme-ico" for node in _descendants(popover))
        )

    def test_settings_theme_markup_is_fully_removed(self):
        self.assertNotIn("set-theme", self.source)
        self.assertNotIn("syncThemeIcon", self.source)

    def test_navbar_theme_button_is_unique_and_toggles(self):
        buttons = [
            node for node in self.elements if node.attrs.get("id") == "btn-theme"
        ]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].attrs.get("onclick"), "toggleTheme()")

    def test_mobile_menu_keeps_theme_action(self):
        mobile_menu = _find_by_id(self.parser, "mobile-menu")
        theme_actions = [
            node
            for node in _descendants(mobile_menu)
            if "toggleTheme()" in node.attrs.get("onclick", "")
        ]
        self.assertTrue(theme_actions, "mobile menu theme action is missing")


if __name__ == "__main__":
    unittest.main()
