"""Makro Step 2 -> Step 3 transition mechanics.

This module owns only portal lifecycle mechanics. Product semantics remain in
``listing_creation`` and Step 3 field execution remains in the domain/executor
layers. Brand selection itself is portal-first and consumes exact live Makro
candidates through ``brand_selection``.

Makro may take longer than the old 15 second post-create wait to settle, and a
portal transition may replace the Playwright Page target. The formal workflows
therefore recover Step 3 from the originating tab or a page owned by this exact
transition. Concurrent Batch jobs may create their own Step 3 pages during the
same recovery window; those pages must never become candidates merely because
they are new to this Playwright context.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Page

from app.browser_page_owner import page_target_id

from .brand_selection import select_brand
from .listing import MAKRO_HOST, parse_makro_listing_url
from .listing_creation import (
    JSONTaskProvider,
    ListingBootstrapHints,
    is_product_info_step,
)
from .portal_adapter import MakroPortalAdapter
from .portal_interruptions import reconcile_portal_interruptions


_CREATE_LISTING_TIMEOUT_ERROR = (
    "Makro Step 2 clicked Create New Listing, but Step 3 did not appear"
)
_RECOVERY_TIMEOUT_S = 30.0
_RECOVERY_POLL_MS = 250


@dataclass(frozen=True, slots=True)
class _TransitionIdentity:
    """Stable listing identity already committed before Step 2 completes.

    ``requestId`` is intentionally excluded because Makro creates it only on the
    Step 3 side of the transition. Vertical + vid are already present on Step 2
    and therefore provide a safe semantic guard without guessing from tab order.
    """

    vertical: str = ""
    vid: str = ""


def dismiss_joyride_overlay(page: Page) -> bool:
    """Backward-compatible entry point for the unified portal interruption gate.

    Existing Single/Batch orchestration calls this function before Step 1/2
    business actions. The implementation now reconciles the whole bounded stack
    of safe presentation-only interruptions rather than one Joyride DOM shape.
    """

    return reconcile_portal_interruptions(page) > 0


def _is_makro_page(page: Any) -> bool:
    try:
        return urlparse(str(page.url or "")).hostname == MAKRO_HOST and not page.is_closed()
    except Exception:
        return False


def _transition_identity(page: Any) -> _TransitionIdentity:
    try:
        target = parse_makro_listing_url(str(page.url or ""))
    except (ValueError, AttributeError):
        return _TransitionIdentity()
    return _TransitionIdentity(
        vertical=str(target.vertical or "").strip().casefold(),
        vid=str(target.vid or "").strip().casefold(),
    )


def _matches_transition_identity(page: Any, expected: _TransitionIdentity) -> bool:
    """Require every identity value known on Step 2 to survive into Step 3."""

    actual = _transition_identity(page)
    if expected.vertical and actual.vertical != expected.vertical:
        return False
    if expected.vid and actual.vid != expected.vid:
        return False
    return True


def _safe_target_id(page: Any) -> str:
    try:
        return page_target_id(page)
    except Exception:
        return ""


def _page_opener(page: Any) -> Any | None:
    """Return Playwright's opener across API/property variants and test fakes."""

    try:
        opener = getattr(page, "opener", None)
        if callable(opener):
            opener = opener()
        return opener
    except Exception:
        return None


def _is_direct_transition_child(
    candidate: Any,
    origin: Any,
    *,
    origin_target_id: str,
) -> bool:
    """Return True only when Chromium/Playwright links candidate to origin."""

    opener = _page_opener(candidate)
    if opener is None:
        return False
    if opener is origin:
        return True
    if origin_target_id:
        opener_target_id = _safe_target_id(opener)
        if opener_target_id and opener_target_id == origin_target_id:
            return True
    return False


def _candidate_pages(context: Any, origin: Page, baseline_page_ids: set[int]) -> list[Page]:
    """Return the origin plus pages born during this exact wait window.

    This is discovery only, not ownership. A concurrent job can also create a
    page after the baseline snapshot, so callers must still apply transition
    identity and opener lineage before adopting a discovered page.
    """

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


def _is_step3(page: Any) -> bool:
    try:
        return bool(is_product_info_step(page))
    except Exception:
        return False


def _step3_matches(
    context: Any,
    origin: Page,
    baseline_page_ids: set[int],
    *,
    expected: _TransitionIdentity,
    origin_target_id: str,
) -> list[Page]:
    """Resolve Step 3 with a strict ownership precedence.

    1. Same-tab Step 3 is authoritative because the owned target survived.
    2. A direct child of the owned origin is authoritative when Makro opens a
       replacement/popup target.
    3. Only when no lineage-bearing Step 3 exists do we retain the historical
       target-replacement fallback, and then only for candidates whose Step 2
       vertical/vid identity matches exactly. More than one match stays fail
       closed at the caller.

    This preserves normal single-job transitions while preventing a concurrently
    created page from another Batch job from competing with the owned child.
    """

    candidates = _candidate_pages(context, origin, baseline_page_ids)

    if _is_step3(origin):
        return [origin] if _matches_transition_identity(origin, expected) else []

    child_step3: list[Page] = []
    fallback_step3: list[Page] = []
    saw_direct_child_step3 = False
    for candidate in candidates:
        if candidate is origin or not _is_step3(candidate):
            continue
        direct_child = _is_direct_transition_child(
            candidate,
            origin,
            origin_target_id=origin_target_id,
        )
        if direct_child:
            saw_direct_child_step3 = True
            if _matches_transition_identity(candidate, expected):
                child_step3.append(candidate)
            continue
        if _matches_transition_identity(candidate, expected):
            fallback_step3.append(candidate)

    # Once Chromium gives us explicit lineage, never fall back to an unrelated
    # concurrently-created page even if it happens to share the same vertical.
    if saw_direct_child_step3:
        return child_step3
    return fallback_step3


def _ownership_relation(page: Any, origin: Any, origin_target_id: str) -> str:
    if page is origin:
        return "origin"
    if _is_direct_transition_child(page, origin, origin_target_id=origin_target_id):
        return "direct_child"
    return "new_unlinked"


def _diagnostics(
    context: Any,
    origin: Page,
    baseline_page_ids: set[int],
    *,
    expected: _TransitionIdentity,
    origin_target_id: str,
) -> str:
    payload: list[dict[str, Any]] = []
    for page in _candidate_pages(context, origin, baseline_page_ids):
        item: dict[str, Any] = {
            "url": str(getattr(page, "url", "") or ""),
            "ownership_relation": _ownership_relation(page, origin, origin_target_id),
            "transition_identity_match": _matches_transition_identity(page, expected),
        }
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

    ``select_brand`` uses the production portal-first live-candidate contract.
    This wrapper only recovers the known post-Create-New-Listing timeout race and
    page-target handoff. Existing tabs are excluded by baseline identity;
    concurrently-created tabs are excluded by origin lineage plus the vertical /
    vid identity already committed on Step 2.
    """

    context = page.context
    baseline_page_ids = {id(item) for item in list(context.pages)}
    expected = _transition_identity(page)
    origin_target_id = _safe_target_id(page)
    dismiss_joyride_overlay(page)

    brand = ""
    recoverable_error: RuntimeError | None = None
    try:
        brand = select_brand(page, provider, hints, wait_ms=wait_ms)
        if is_product_info_step(page):
            if not _matches_transition_identity(page, expected):
                raise RuntimeError(
                    "Makro Step 2 same-tab transition changed the committed listing identity; "
                    "refusing to continue on the wrong Step 3 page"
                )
            return brand, page
    except RuntimeError as exc:
        if _CREATE_LISTING_TIMEOUT_ERROR not in str(exc):
            raise
        recoverable_error = exc

    deadline = time.monotonic() + max(0.1, float(recovery_timeout_s))
    while time.monotonic() < deadline:
        matches = _step3_matches(
            context,
            page,
            baseline_page_ids,
            expected=expected,
            origin_target_id=origin_target_id,
        )
        if len(matches) == 1:
            step3_page = matches[0]
            step3_page.set_default_timeout(15_000)
            resolved_brand = brand or _brand_from_step3(step3_page)
            return resolved_brand, step3_page
        if len(matches) > 1:
            raise RuntimeError(
                "Makro Step 2 transition produced multiple owned Step 3 pages; refusing to guess target. "
                f"diagnostics={_diagnostics(context, page, baseline_page_ids, expected=expected, origin_target_id=origin_target_id)}"
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
        "Makro Create New Listing was accepted, but no unique owned Step 3 page became ready within "
        "the recovery window. "
        f"diagnostics={_diagnostics(context, page, baseline_page_ids, expected=expected, origin_target_id=origin_target_id)}"
    ) from recoverable_error


__all__ = [
    "dismiss_joyride_overlay",
    "select_brand_to_product_info",
]
