"""Makro Step 2 -> Step 3 transition mechanics.

This module owns only portal lifecycle mechanics. Product semantics remain in
``listing_creation`` and Step 3 field execution remains in the domain/executor
layers.

Makro may take longer than the old 15 second post-create wait to settle, and a
portal transition may replace the Playwright Page target. The formal workflows
therefore recover the unique Step 3 page from the originating tab plus pages
created by this exact transition. Pre-existing tabs are never adopted.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Page

from .listing import MAKRO_HOST, parse_makro_listing_url
from .listing_creation import (
    JSONTaskProvider,
    ListingBootstrapHints,
    is_product_info_step,
    select_brand,
)
from .portal_adapter import MakroPortalAdapter


_CREATE_LISTING_TIMEOUT_ERROR = (
    "Makro Step 2 clicked Create New Listing, but Step 3 did not appear"
)
_JOYRIDE_SELECTOR = (
    ".joyride-overlay, .react-joyride__overlay, [class*='joyride-overlay']"
)
_RECOVERY_TIMEOUT_S = 30.0
_RECOVERY_POLL_MS = 250


def _first_visible(locator: Any):
    try:
        count = locator.count()
    except Exception:
        return None
    for index in range(count):
        item = locator.nth(index)
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def _joyride_root(page: Page):
    try:
        return _first_visible(page.locator(_JOYRIDE_SELECTOR))
    except Exception:
        return None


def dismiss_joyride_overlay(page: Page) -> bool:
    """Dismiss Makro's optional onboarding tour without forcing business clicks.

    The tour is presentation-only but its overlay intercepts pointer events. We
    first use Escape, then only exact tutorial close/skip controls inside the
    overlay. We never use ``force=True`` and never remove portal DOM nodes.
    """

    root = _joyride_root(page)
    if root is None:
        return False

    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(220)
    except Exception:
        pass
    if _joyride_root(page) is None:
        return True

    safe_selectors = (
        'button[data-action="skip"]',
        '[role="button"][data-action="skip"]',
        'button[data-action="close"]',
        '[role="button"][data-action="close"]',
        'button[aria-label="Close"]',
        '[role="button"][aria-label="Close"]',
    )
    for selector in safe_selectors:
        root = _joyride_root(page)
        if root is None:
            return True
        try:
            button = _first_visible(root.locator(selector))
        except Exception:
            button = None
        if button is None:
            continue
        try:
            button.click(timeout=2000)
            page.wait_for_timeout(220)
        except Exception:
            continue
        if _joyride_root(page) is None:
            return True

    for label in ("Skip", "Close", "Done", "Got it", "跳过", "关闭", "完成", "知道了"):
        root = _joyride_root(page)
        if root is None:
            return True
        try:
            button = _first_visible(root.get_by_role("button", name=label, exact=True))
        except Exception:
            button = None
        if button is None:
            continue
        try:
            button.click(timeout=2000)
            page.wait_for_timeout(220)
        except Exception:
            continue
        if _joyride_root(page) is None:
            return True

    raise RuntimeError(
        "Makro onboarding tutorial overlay is blocking listing controls. "
        "Please close the visible tutorial once and retry; business buttons were not force-clicked."
    )


def _is_makro_page(page: Any) -> bool:
    try:
        return urlparse(str(page.url or "")).hostname == MAKRO_HOST and not page.is_closed()
    except Exception:
        return False


def _candidate_pages(context: Any, origin: Page, baseline_page_ids: set[int]) -> list[Page]:
    """Return only the origin plus pages born during this exact transition."""

    candidates: list[Page] = []
    seen: set[int] = set()
    for page in [origin, *list(getattr(context, "pages", []) or [])]:
        identity = id(page)
        if identity in seen:
            continue
        seen.add(identity)
        if page is not origin and identity in baseline_page_ids:
            continue
        if _is_makro_page(page):
            candidates.append(page)
    return candidates


def _step3_matches(context: Any, origin: Page, baseline_page_ids: set[int]) -> list[Page]:
    matches: list[Page] = []
    for page in _candidate_pages(context, origin, baseline_page_ids):
        try:
            if is_product_info_step(page):
                matches.append(page)
        except Exception:
            continue
    return matches


def _diagnostics(context: Any, origin: Page, baseline_page_ids: set[int]) -> str:
    payload: list[dict[str, Any]] = []
    for page in _candidate_pages(context, origin, baseline_page_ids):
        item: dict[str, Any] = {"url": str(getattr(page, "url", "") or "")}
        try:
            item.update(MakroPortalAdapter(page).diagnostics())
        except Exception as exc:
            item["diagnostics_error"] = type(exc).__name__
        payload.append(item)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _brand_from_step3(page: Page) -> str:
    try:
        target = parse_makro_listing_url(page.url)
    except (ValueError, AttributeError) as exc:
        raise RuntimeError(
            "Recovered Makro Step 3 page does not expose a verifiable brand in its listing URL"
        ) from exc
    brand = str(target.brand or "").strip()
    if not brand:
        raise RuntimeError(
            "Recovered Makro Step 3 page has no verifiable brand; refusing to guess the Step 2 result"
        )
    return brand


def select_brand_to_product_info(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int = 900,
    recovery_timeout_s: float = _RECOVERY_TIMEOUT_S,
) -> tuple[str, Page]:
    """Select Step 2 brand and return the exact Page that owns Step 3.

    ``select_brand`` keeps its existing strict browser semantics. This wrapper
    only recovers the known post-Create-New-Listing timeout race and page-target
    handoff. It never adopts Step 3 tabs that existed before this transition.
    """

    context = page.context
    baseline_page_ids = {id(item) for item in list(context.pages)}
    dismiss_joyride_overlay(page)

    brand = ""
    recoverable_error: RuntimeError | None = None
    try:
        brand = select_brand(page, provider, hints, wait_ms=wait_ms)
        if is_product_info_step(page):
            return brand, page
    except RuntimeError as exc:
        if _CREATE_LISTING_TIMEOUT_ERROR not in str(exc):
            raise
        recoverable_error = exc

    deadline = time.monotonic() + max(0.1, float(recovery_timeout_s))
    while time.monotonic() < deadline:
        matches = _step3_matches(context, page, baseline_page_ids)
        if len(matches) == 1:
            step3_page = matches[0]
            step3_page.set_default_timeout(15_000)
            resolved_brand = brand or _brand_from_step3(step3_page)
            return resolved_brand, step3_page
        if len(matches) > 1:
            raise RuntimeError(
                "Makro Step 2 transition produced multiple new Step 3 pages; refusing to guess target. "
                f"diagnostics={_diagnostics(context, page, baseline_page_ids)}"
            ) from recoverable_error

        wait_page = page
        try:
            if page.is_closed():
                candidates = _candidate_pages(context, page, baseline_page_ids)
                if candidates:
                    wait_page = candidates[-1]
            wait_page.wait_for_timeout(_RECOVERY_POLL_MS)
        except Exception:
            time.sleep(_RECOVERY_POLL_MS / 1000.0)

    raise RuntimeError(
        "Makro Create New Listing was accepted, but no unique Step 3 page became ready within "
        f"the recovery window. diagnostics={_diagnostics(context, page, baseline_page_ids)}"
    ) from recoverable_error


__all__ = [
    "dismiss_joyride_overlay",
    "select_brand_to_product_info",
]
