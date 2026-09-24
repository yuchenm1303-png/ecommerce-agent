from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


@dataclass(frozen=True)
class TransitionTrigger:
    """Result of dispatching a UI action whose postcondition is verified elsewhere.

    A Playwright timeout can happen after the browser has already dispatched the
    click while Playwright is waiting for navigation/settling.  Callers must not
    use that timeout as the business result; they must reconcile the target page
    state.  Non-timeout click failures still propagate immediately.
    """

    timed_out: bool = False
    timeout_detail: str = ""


def trigger_transition(click: Callable[[], Any]) -> TransitionTrigger:
    """Dispatch one state-changing click and defer success to its postcondition."""

    try:
        click()
    except PlaywrightTimeoutError as exc:
        return TransitionTrigger(timed_out=True, timeout_detail=str(exc))
    return TransitionTrigger()


class PostconditionAction:
    """Locator facade for actions whose callers already verify the next state."""

    def __init__(self, locator: Any) -> None:
        self._locator = locator
        self.last_trigger = TransitionTrigger()

    def click(self, *args: Any, **kwargs: Any) -> None:
        self.last_trigger = trigger_transition(
            lambda: self._locator.click(*args, **kwargs)
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._locator, name)


__all__ = ["PostconditionAction", "TransitionTrigger", "trigger_transition"]
