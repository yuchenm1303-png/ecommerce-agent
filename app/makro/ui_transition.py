from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


_PRE_DISPATCH_TIMEOUT_MARKERS = (
    "intercepts pointer events",
    "another element would receive the click",
    "element is not visible",
    "element is not enabled",
    "element is not stable",
    "element is outside of the viewport",
    "waiting for element to be visible",
    "waiting for element to be enabled",
    "waiting for element to be stable",
    "retrying click action",
)


@dataclass(frozen=True)
class TransitionTrigger:
    """Result of dispatching a UI action whose postcondition is verified elsewhere.

    A Playwright timeout can happen after the browser has already dispatched the
    click while Playwright is waiting for navigation/settling. Callers must not
    use that timeout as the business result; they must reconcile the target page
    state. Pre-dispatch actionability timeouts are different: no click is known to
    have been sent, so they are retried once by ``PostconditionAction`` and then
    surfaced instead of being misreported as a triggered transition.
    """

    timed_out: bool = False
    timeout_detail: str = ""


def _timeout_is_pre_dispatch(exc: BaseException) -> bool:
    text = " ".join(str(exc or "").split()).casefold()
    return any(marker in text for marker in _PRE_DISPATCH_TIMEOUT_MARKERS)


def trigger_transition(click: Callable[[], Any]) -> TransitionTrigger:
    """Dispatch one state-changing click and defer success to its postcondition."""

    try:
        click()
    except PlaywrightTimeoutError as exc:
        if _timeout_is_pre_dispatch(exc):
            raise
        return TransitionTrigger(timed_out=True, timeout_detail=str(exc))
    return TransitionTrigger()


class PostconditionAction:
    """Locator facade for actions whose callers already verify the next state.

    The action is first scrolled into the centre of the current page. A timeout
    whose Playwright call log proves that the click never became actionable is
    retried once because no state-changing event was dispatched. Timeouts that may
    have occurred after dispatch remain postcondition-owned and are never blindly
    clicked a second time.
    """

    def __init__(self, locator: Any) -> None:
        self._locator = locator
        self.last_trigger = TransitionTrigger()

    def _prepare_click(self, timeout_ms: float) -> None:
        bounded = max(250.0, min(float(timeout_ms), 3000.0))
        try:
            self._locator.scroll_into_view_if_needed(timeout=bounded)
        except TypeError:
            self._locator.scroll_into_view_if_needed()
        try:
            self._locator.evaluate(
                "el => el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'})"
            )
        except Exception:
            pass

    def click(self, *args: Any, **kwargs: Any) -> None:
        raw_timeout = kwargs.get("timeout", 30_000)
        try:
            timeout_ms = float(raw_timeout)
        except (TypeError, ValueError):
            timeout_ms = 30_000.0

        last_pre_dispatch: PlaywrightTimeoutError | None = None
        for attempt in range(2):
            try:
                self._prepare_click(timeout_ms)
                self.last_trigger = trigger_transition(
                    lambda: self._locator.click(*args, **kwargs)
                )
                return
            except PlaywrightTimeoutError as exc:
                if not _timeout_is_pre_dispatch(exc):
                    raise
                last_pre_dispatch = exc
                if attempt >= 1:
                    raise
                time.sleep(0.20)

        assert last_pre_dispatch is not None
        raise last_pre_dispatch

    def __getattr__(self, name: str) -> Any:
        return getattr(self._locator, name)


__all__ = [
    "PostconditionAction",
    "TransitionTrigger",
    "_timeout_is_pre_dispatch",
    "trigger_transition",
]
