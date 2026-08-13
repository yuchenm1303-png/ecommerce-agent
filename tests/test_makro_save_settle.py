from __future__ import annotations

import pytest

from app.makro import sections


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance_ms(self, value: int) -> None:
        self.now += value / 1000.0


class _SaveControl:
    def __init__(self, page: "_Page") -> None:
        self.page = page
        self.first = self

    def filter(self, **_kwargs):
        return self

    def count(self) -> int:
        return 1

    def is_visible(self) -> bool:
        return True

    def scroll_into_view_if_needed(self) -> None:
        return None

    def click(self) -> None:
        self.page.save_clicked = True


class _Card:
    def __init__(self, page: "_Page") -> None:
        self.page = page

    def locator(self, _selector: str) -> _SaveControl:
        return _SaveControl(self.page)


class _Page:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.save_clicked = False

    def locator(self, _path: str) -> _Card:
        return _Card(self)

    def wait_for_timeout(self, value: int) -> None:
        self.clock.advance_ms(value)


def test_save_accepts_late_async_collapse(monkeypatch) -> None:
    clock = _Clock()
    page = _Page(clock)

    monkeypatch.setattr(sections.time, "monotonic", clock.monotonic)

    def fake_find(_page, _title):
        if not page.save_clicked:
            return {"path": "card", "has_edit": False}
        return {
            "path": "card",
            "has_edit": clock.now >= 20.0,
        }

    monkeypatch.setattr(sections, "find_section", fake_find)
    monkeypatch.setattr(sections, "collapsed_error_badges", lambda *_args: [])

    sections.save_section(page, "Product Description")

    assert page.save_clicked is True
    assert clock.now >= 20.0


def test_save_tolerates_transient_collapsed_error_badge(monkeypatch) -> None:
    clock = _Clock()
    page = _Page(clock)

    monkeypatch.setattr(sections.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(
        sections,
        "find_section",
        lambda *_args: {
            "path": "card",
            "has_edit": bool(page.save_clicked and clock.now >= 0.5),
        },
    )
    monkeypatch.setattr(
        sections,
        "collapsed_error_badges",
        lambda *_args: ["1 Error"] if clock.now < 2.0 else [],
    )
    monkeypatch.setattr(
        sections,
        "open_section_for_edit",
        lambda *_args: pytest.fail("transient badge must not reopen the section"),
    )

    sections.save_section(page, "Price, Stock and Shipping Information", timeout_s=5.0)

    assert page.save_clicked is True
    assert clock.now >= 2.0


def test_save_keeps_persistent_validation_fail_closed(monkeypatch) -> None:
    clock = _Clock()
    page = _Page(clock)

    monkeypatch.setattr(sections.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(
        sections,
        "find_section",
        lambda *_args: {"path": "card", "has_edit": bool(page.save_clicked)},
    )
    monkeypatch.setattr(sections, "collapsed_error_badges", lambda *_args: ["1 Error"])
    monkeypatch.setattr(sections, "open_section_for_edit", lambda *_args: None)
    monkeypatch.setattr(sections, "visible_section_errors", lambda *_args: ["SKU already used"])

    with pytest.raises(RuntimeError, match="validation error"):
        sections.save_section(page, "Price, Stock and Shipping Information", timeout_s=1.0)
