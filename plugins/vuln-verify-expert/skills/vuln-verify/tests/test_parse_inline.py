import unittest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from md_to_docx import Run, parse_inline


def runs_of(text):
    """Convenience: return list of (text, bold, italic, code)."""
    return [(r.text, r.bold, r.italic, r.code) for r in parse_inline(text)]


class TestParseInline(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(runs_of("普通文本"), [("普通文本", False, False, False)])

    def test_inline_code(self):
        self.assertEqual(runs_of("设置 `securityLevel` 为 loose"),
                         [("设置 ", False, False, False),
                          ("securityLevel", False, False, True),
                          (" 为 loose", False, False, False)])

    def test_bold(self):
        self.assertEqual(runs_of("**加粗**"), [("加粗", True, False, False)])

    def test_italic_star(self):
        self.assertEqual(runs_of("*斜体*"), [("斜体", False, True, False)])

    def test_italic_underscore(self):
        self.assertEqual(runs_of("_斜体_"), [("斜体", False, True, False)])

    def test_bold_then_code(self):
        self.assertEqual(runs_of("**加粗** 然后 `code`"),
                         [("加粗", True, False, False),
                          (" 然后 ", False, False, False),
                          ("code", False, False, True)])

    def test_code_with_asterisks_not_parsed_as_bold(self):
        # code 段内 ** 不被当作加粗
        self.assertEqual(runs_of("`a **b** c`"),
                         [("a **b** c", False, False, True)])

    def test_unpaired_marker_passthrough(self):
        self.assertEqual(runs_of("单个 ** 不闭合"),
                         [("单个 ** 不闭合", False, False, False)])

    def test_empty_string(self):
        self.assertEqual(runs_of(""), [])

    def test_multiple_code_segments(self):
        self.assertEqual(runs_of("`a` 和 `b`"),
                         [("a", False, False, True),
                          (" 和 ", False, False, False),
                          ("b", False, False, True)])


if __name__ == "__main__":
    unittest.main()
