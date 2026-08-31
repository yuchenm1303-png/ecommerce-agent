from __future__ import annotations

from types import SimpleNamespace

import app.makro.sections as sections


class _FakeButton:
    def __init__(self, on_click) -> None:
        self._on_click = on_click

    @property
    def first(self):
        return self

    def count(self) -> int:
        return 1

    def is_visible(self) -> bool:
        return True

    def scroll_into_view_if_needed(self) -> None:
        return None

    def click(self) -> None:
        self._on_click()


class _FakeCard:
    def __init__(self, button: _FakeButton) -> None:
        self._button = button

    def locator(self, _selector: str):
        return self

    def filter(self, **_kwargs):
        return self._button


class _FakePage:
    def __init__(self, on_save) -> None:
        self._card = _FakeCard(_FakeButton(on_save))

    def locator(self, _selector: str):
        return self._card

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


def test_collapsed_card_with_validation_badge_is_still_a_persisted_save(monkeypatch) -> None:
    state = {"collapsed": False}
    page = _FakePage(lambda: state.__setitem__("collapsed", True))

    monkeypatch.setattr(
        sections,
        "find_section",
        lambda _page, _title: {
            "title": "Product Description",
            "path": "#product-description",
            "has_edit": state["collapsed"],
        },
    )
    monkeypatch.setattr(
        sections,
        "collapsed_error_badges",
        lambda _page, _title: ["1 Error"],
    )
    monkeypatch.setattr(
        sections,
        "visible_section_errors",
        lambda _page, _path: ["Video Recording Frame Rate is invalid"],
    )
    monkeypatch.setattr(
        sections,
        "trigger_transition",
        lambda action: (action(), SimpleNamespace(timed_out=False, timeout_detail=""))[1],
    )
    monkeypatch.setattr(
        sections,
        "open_section_for_edit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("save_section must not reopen a collapsed saved card to diagnose completeness")
        ),
    )

    sections.save_section(page, "Product Description", timeout_s=0.2)

    assert state["collapsed"] is True
