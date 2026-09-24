from __future__ import annotations

from app.makro.field_engine import fill_control


class _Option:
    def __init__(self, page, text: str) -> None:
        self.page = page
        self.text = text

    def is_visible(self) -> bool:
        return True

    def click(self) -> None:
        self.page.control.value = self.text


class _TextMatches:
    def __init__(self, page, text: str) -> None:
        self.items = [_Option(page, text)] if text == "20" else []

    def count(self) -> int:
        return len(self.items)

    def nth(self, index: int):
        return self.items[index]


class _Control:
    def __init__(self) -> None:
        self.first = self
        self.value = ""
        self.opened = False

    def count(self) -> int:
        return 1

    def is_visible(self) -> bool:
        return True

    def is_disabled(self) -> bool:
        return False

    def wait_for(self, state="visible") -> None:
        assert state == "visible"

    def click(self) -> None:
        self.opened = True

    def get_attribute(self, name, timeout=None):
        if name == "value":
            return self.value
        return None

    def blur(self) -> None:
        return None


class _Page:
    def __init__(self) -> None:
        self.control = _Control()

    def locator(self, _selector):
        return self.control

    def evaluate(self, _script):
        assert self.control.opened is True
        return [{"text": "20", "value": "20", "disabled": False}]

    def get_by_text(self, text, exact=True):
        assert exact is True
        return _TextMatches(self, text)


def test_custom_select_uses_popup_domain_after_react_dependency_change() -> None:
    page = _Page()
    control = {
        "name": "quantity_0_value",
        "field_kind": "dropdown",
        "path": "body > div.quantity",
        # Deliberately stale scan-time options from before the qualifier changed.
        "options": [{"text": "10", "value": "10", "disabled": False}],
        "selector_candidates": [],
    }

    selector = fill_control(page, control, "20")

    assert selector
    assert page.control.opened is True
    assert page.control.value == "20"
