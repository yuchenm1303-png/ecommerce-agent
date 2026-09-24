from __future__ import annotations

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.makro import sections
from app.makro.ui_transition import PostconditionAction, trigger_transition


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance_ms(self, value: int) -> None:
        self.now += value / 1000.0


class _TimeoutAfterDispatchSave:
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
        raise PlaywrightTimeoutError("navigation wait timed out after click")


class _Card:
    def __init__(self, page: "_Page") -> None:
        self.page = page

    def locator(self, _selector: str) -> _TimeoutAfterDispatchSave:
        return _TimeoutAfterDispatchSave(self.page)


class _Page:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.save_clicked = False

    def locator(self, _path: str) -> _Card:
        return _Card(self)

    def wait_for_timeout(self, value: int) -> None:
        self.clock.advance_ms(value)


def test_save_uses_collapsed_postcondition_after_click_timeout(monkeypatch) -> None:
    clock = _Clock()
    page = _Page(clock)
    monkeypatch.setattr(sections.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(
        sections,
        "find_section",
        lambda *_args: {"path": "card", "has_edit": bool(page.save_clicked)},
    )
    monkeypatch.setattr(sections, "collapsed_error_badges", lambda *_args: [])

    sections.save_section(page, "Product Description", timeout_s=2.0)

    assert page.save_clicked is True


def test_transition_timeout_is_indeterminate_but_non_timeout_error_propagates() -> None:
    dispatched = False

    def timeout_click() -> None:
        nonlocal dispatched
        dispatched = True
        raise PlaywrightTimeoutError("post-click wait")

    result = trigger_transition(timeout_click)
    assert dispatched is True
    assert result.timed_out is True

    with pytest.raises(RuntimeError, match="detached before dispatch"):
        trigger_transition(lambda: (_ for _ in ()).throw(RuntimeError("detached before dispatch")))


def test_postcondition_action_preserves_locator_surface_and_timeout_state() -> None:
    class Locator:
        marker = "locator"

        def click(self, **_kwargs) -> None:
            raise PlaywrightTimeoutError("navigation wait")

    action = PostconditionAction(Locator())
    action.click(timeout=5_000)

    assert action.marker == "locator"
    assert action.last_trigger.timed_out is True
