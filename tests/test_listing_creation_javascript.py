from __future__ import annotations

from app.makro.listing_creation import _visible_text_candidates


class FakePage:
    def __init__(self) -> None:
        self.script = ""
        self.limit = None

    def evaluate(self, script, limit):
        self.script = script
        self.limit = limit
        return ["Vehicle Camera System"]


def test_visible_candidate_script_preserves_javascript_newline_escape():
    page = FakePage()

    result = _visible_text_candidates(page, limit=17)

    assert result == ["Vehicle Camera System"]
    assert page.limit == 17
    assert "text.includes('\\n')" in page.script
    assert "text.includes('\n')" not in page.script
