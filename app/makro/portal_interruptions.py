"""Deterministic cleanup for presentation-only Makro portal interruptions.

This module owns only transient UI chrome that can safely be dismissed without
changing listing business state: onboarding/tours, help/tutorial dialogs and
cookie notices. Unknown dialogs are deliberately left untouched so callers keep
the normal fail-closed browser semantics.
"""

from __future__ import annotations

from typing import Any


_JOYRIDE_OVERLAY_SELECTOR = (
    ".joyride-overlay, .react-joyride__overlay, "
    "[class*='joyride-overlay'], [class*='react-joyride__overlay']"
)
_JOYRIDE_TOOLTIP_SELECTOR = (
    ".react-joyride__tooltip, [class*='joyride-tooltip'], "
    "[class*='react-joyride__tooltip'], [class*='joyride'][role='dialog']"
)
_DIALOG_SELECTOR = '[role="dialog"], [aria-modal="true"], .modal, [class*="modal"]'
_COOKIE_SELECTOR = (
    '[id*="cookie" i], [class*="cookie" i], '
    '[id*="consent" i], [class*="consent" i]'
)

_TOUR_DISMISS_LABELS = (
    "Skip",
    "Done",
    "Got it",
    "Finish",
    "Dismiss",
    "Close",
    "跳过",
    "完成",
    "知道了",
    "关闭",
)
_PRESENTATION_DISMISS_LABELS = (
    "Close",
    "Done",
    "Got it",
    "Dismiss",
    "Not now",
    "Maybe later",
    "关闭",
    "完成",
    "知道了",
    "暂不",
)
_COOKIE_ACCEPT_LABELS = (
    "Accept",
    "Accept All",
    "Allow All",
    "Got it",
    "I Agree",
    "同意",
    "接受",
    "全部接受",
)
_PRESENTATION_MARKERS = (
    "tutorial",
    "onboarding",
    "walkthrough",
    "guide",
    "learn to",
    "how to",
    "help video",
    "create a single listing",
    "教程",
    "引导",
    "新手",
    "帮助视频",
)
_COOKIE_MARKERS = (
    "cookie",
    "privacy policy",
    "consent",
    "cookies",
    "隐私",
    "同意",
)


def _visible(locator: Any) -> list[Any]:
    output: list[Any] = []
    try:
        count = locator.count()
    except Exception:
        return output
    for index in range(count):
        item = locator.nth(index)
        try:
            if item.is_visible():
                output.append(item)
        except Exception:
            continue
    return output


def _first_visible(locator: Any):
    items = _visible(locator)
    return items[0] if items else None


def _root_text(root: Any) -> str:
    try:
        return str(root.inner_text(timeout=500) or "").casefold()
    except Exception:
        return ""


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    normalized = str(text or "").casefold()
    return any(marker.casefold() in normalized for marker in markers)


def _click_exact_button(scope: Any, labels: tuple[str, ...], *, timeout: int = 2_000) -> bool:
    """Click one explicit dismissal action; never guess and never force-click."""

    for label in labels:
        try:
            matches = _visible(scope.get_by_role("button", name=label, exact=True))
        except Exception:
            matches = []
        if len(matches) != 1:
            continue
        try:
            matches[0].click(timeout=timeout)
            return True
        except Exception:
            continue
    return False


def _click_close_affordance(scope: Any, *, timeout: int = 2_000) -> bool:
    selectors = (
        'button[data-action="skip"]',
        '[role="button"][data-action="skip"]',
        'button[data-action="close"]',
        '[role="button"][data-action="close"]',
        'button[aria-label="Close"]',
        '[role="button"][aria-label="Close"]',
        'button[title="Close"]',
        '[role="button"][title="Close"]',
    )
    for selector in selectors:
        try:
            matches = _visible(scope.locator(selector))
        except Exception:
            matches = []
        if len(matches) != 1:
            continue
        try:
            matches[0].click(timeout=timeout)
            return True
        except Exception:
            continue
    return False


def _settle(page: Any) -> None:
    try:
        page.wait_for_timeout(180)
    except Exception:
        pass


def _joyride_visible(page: Any) -> bool:
    try:
        return _first_visible(page.locator(_JOYRIDE_OVERLAY_SELECTOR)) is not None
    except Exception:
        return False


def _dismiss_joyride(page: Any) -> bool:
    """Dismiss one visible onboarding tour, including sibling tooltip layouts."""

    if not _joyride_visible(page):
        return False

    try:
        page.keyboard.press("Escape")
        _settle(page)
    except Exception:
        pass
    if not _joyride_visible(page):
        return True

    # React Joyride commonly renders the overlay and tooltip as siblings. Scope
    # to a tooltip when available; otherwise the confirmed overlay makes global
    # Skip/Done controls safe enough to inspect. Never click Next.
    try:
        tooltip = _first_visible(page.locator(_JOYRIDE_TOOLTIP_SELECTOR))
    except Exception:
        tooltip = None
    scopes = [tooltip] if tooltip is not None else []
    scopes.append(page)
    for scope in scopes:
        if _click_close_affordance(scope) or _click_exact_button(scope, _TOUR_DISMISS_LABELS):
            _settle(page)
            if not _joyride_visible(page):
                return True

    raise RuntimeError(
        "Makro onboarding overlay is visible but has no verified safe dismiss action; "
        "business controls were not force-clicked."
    )


def _presentation_dialog(page: Any):
    try:
        roots = _visible(page.locator(_DIALOG_SELECTOR))
    except Exception:
        return None
    recognized = [root for root in roots if _has_marker(_root_text(root), _PRESENTATION_MARKERS)]
    # Every returned root is already classified as presentation-only. Nested
    # modal wrappers can duplicate the same tutorial, so using the first such
    # root is safe; unknown/business dialogs never enter this set.
    return recognized[0] if recognized else None


def _dismiss_presentation_dialog(page: Any) -> bool:
    root = _presentation_dialog(page)
    if root is None:
        return False

    try:
        page.keyboard.press("Escape")
        _settle(page)
    except Exception:
        pass
    if _presentation_dialog(page) is None:
        return True

    root = _presentation_dialog(page)
    if root is not None and (
        _click_close_affordance(root)
        or _click_exact_button(root, _PRESENTATION_DISMISS_LABELS)
    ):
        _settle(page)
        if _presentation_dialog(page) is None:
            return True

    raise RuntimeError(
        "Makro presentation/help dialog is visible but has no verified safe dismiss action; "
        "business controls were not force-clicked."
    )


def _cookie_notice(page: Any):
    try:
        roots = _visible(page.locator(_COOKIE_SELECTOR))
    except Exception:
        return None
    recognized = [root for root in roots if _has_marker(_root_text(root), _COOKIE_MARKERS)]
    return recognized[0] if recognized else None


def _dismiss_cookie_notice(page: Any) -> bool:
    root = _cookie_notice(page)
    if root is None:
        return False
    if _click_exact_button(root, _COOKIE_ACCEPT_LABELS):
        _settle(page)
        return _cookie_notice(page) is None
    # Cookie banners are supplemental chrome. If there is no exact recognized
    # accept action, leave them untouched rather than clicking arbitrary text.
    return False


def reconcile_portal_interruptions(page: Any, *, max_rounds: int = 8) -> int:
    """Remove a bounded stack of safe presentation-only Makro interruptions.

    The reconciler is category-based rather than popup-name based. It repeatedly
    handles verified onboarding/tour chrome, help/tutorial dialogs and cookie
    notices until no known safe interruption remains. Unknown modal/business
    state is never dismissed here.
    """

    handled = 0
    for _ in range(max(1, int(max_rounds))):
        if _dismiss_joyride(page):
            handled += 1
            continue
        if _dismiss_presentation_dialog(page):
            handled += 1
            continue
        if _dismiss_cookie_notice(page):
            handled += 1
            continue
        return handled
    raise RuntimeError(
        "Makro portal interruption cleanup exceeded its bounded transition limit; "
        "business controls were not force-clicked."
    )


__all__ = ["reconcile_portal_interruptions"]
