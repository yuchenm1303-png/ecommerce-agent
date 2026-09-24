from __future__ import annotations

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.makro.ui_transition import PostconditionAction, trigger_transition


def _raise(exc: BaseException):
    raise exc


def test_pre_dispatch_actionability_timeout_is_not_misreported_as_triggered() -> None:
    with pytest.raises(PlaywrightTimeoutError, match="intercepts pointer events"):
        trigger_transition(
            lambda: _raise(
                PlaywrightTimeoutError(
                    "Locator.click: Timeout 5000ms exceeded. retrying click action because another element intercepts pointer events"
                )
            )
        )


def test_post_dispatch_navigation_timeout_remains_postcondition_owned() -> None:
    result = trigger_transition(
        lambda: _raise(
            PlaywrightTimeoutError(
                "Timeout 5000ms exceeded while waiting for scheduled navigations to finish"
            )
        )
    )

    assert result.timed_out is True
    assert "scheduled navigations" in result.timeout_detail


class _FlakyLocator:
    def __init__(self) -> None:
        self.click_calls = 0
        self.scroll_calls = 0
        self.evaluate_calls = 0

    def scroll_into_view_if_needed(self, **kwargs) -> None:
        self.scroll_calls += 1

    def evaluate(self, expression: str) -> None:
        self.evaluate_calls += 1

    def click(self, *args, **kwargs) -> None:
        self.click_calls += 1
        if self.click_calls == 1:
            raise PlaywrightTimeoutError(
                "Locator.click: Timeout 5000ms exceeded. element is outside of the viewport; retrying click action"
            )


def test_postcondition_action_retries_one_known_pre_dispatch_timeout() -> None:
    locator = _FlakyLocator()
    action = PostconditionAction(locator)

    action.click(timeout=5000)

    assert locator.click_calls == 2
    assert locator.scroll_calls == 2
    assert locator.evaluate_calls == 2
    assert action.last_trigger.timed_out is False


class _AlwaysBlockedLocator(_FlakyLocator):
    def click(self, *args, **kwargs) -> None:
        self.click_calls += 1
        raise PlaywrightTimeoutError(
            "Locator.click: Timeout 5000ms exceeded. element is not stable; retrying click action"
        )


def test_postcondition_action_surfaces_second_pre_dispatch_failure() -> None:
    locator = _AlwaysBlockedLocator()
    action = PostconditionAction(locator)

    with pytest.raises(PlaywrightTimeoutError, match="element is not stable"):
        action.click(timeout=5000)

    assert locator.click_calls == 2
