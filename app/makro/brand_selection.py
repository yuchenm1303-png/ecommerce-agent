"""Makro Step 2 brand availability confirmation.

Makro Step 2 is not an autocomplete picker. The portal accepts one brand query,
validates it with ``Check Brand`` and then exposes a confirmation/create-listing
state for the validated brand. Supplier evidence establishes the brand identity;
Makro confirms whether that exact identity is sellable.

Production therefore never scans page-wide text or waits for synthetic brand
candidate rows. It only advances after the portal exposes a confirmation state
that is mechanically bound to the queried brand, then re-verifies the committed
brand from the listing URL/Step 3 transition.
"""

from __future__ import annotations

from typing import Any, Protocol

from playwright.sync_api import Page

from .listing_creation import (
    _advance_brand_confirmation,
    _brand_input,
    _brand_search_terms,
    _click_check_brand,
    _current_target_values,
    _verify_selected_value,
    is_brand_ready_to_create_listing,
    is_brand_selected_confirmation,
    is_brand_step,
    is_product_info_step,
)
from .portal_interruptions import reconcile_portal_interruptions


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


class BrandHints(Protocol):
    brand: str
    brand_status: str


def _wait_for_brand_check_outcome(
    page: Page,
    selected_brand: str,
    *,
    timeout_ms: int,
    poll_ms: int = 200,
) -> str:
    """Wait for one structurally verified result of ``Check Brand``.

    ``ready`` means Makro exposed the exact Create New Listing state for the
    queried brand. ``confirmation`` covers the alternate confirm/use-brand
    state. Some portal versions may transition directly to Step 3.
    """

    polls = max(1, max(0, int(timeout_ms)) // max(50, int(poll_ms)))
    for _ in range(polls):
        if is_product_info_step(page):
            return "product_info"
        if is_brand_ready_to_create_listing(page, selected_brand):
            return "ready"
        if is_brand_selected_confirmation(page, selected_brand):
            return "confirmation"
        page.wait_for_timeout(max(50, int(poll_ms)))

    if is_product_info_step(page):
        return "product_info"
    if is_brand_ready_to_create_listing(page, selected_brand):
        return "ready"
    if is_brand_selected_confirmation(page, selected_brand):
        return "confirmation"
    return "none"


def select_brand(
    page: Page,
    provider: JSONTaskProvider,
    hints: BrandHints,
    *,
    wait_ms: int = 900,
) -> str:
    """Validate one supplier-grounded brand through Makro's native Step 2 flow."""

    del provider  # Brand semantics were already established upstream.

    if not is_brand_step(page):
        raise RuntimeError("Makro is not on Step 2 / Select Brand")

    terms = _brand_search_terms(hints)
    if not terms:
        raise RuntimeError(
            "Supplier evidence did not establish a brand or explicit unbranded status; "
            "refusing to invent Step 2 brand"
        )

    brand_input = _brand_input(page)
    attempted: list[str] = []
    for term in terms:
        attempted.append(term)
        reconcile_portal_interruptions(page)
        brand_input.fill("")
        brand_input.fill(term)
        _click_check_brand(page)

        outcome = _wait_for_brand_check_outcome(
            page,
            term,
            timeout_ms=max(5_000, int(wait_ms) * 6),
        )
        if outcome == "none":
            continue

        if outcome != "product_info":
            # Existing transition mechanics perform the exact confirmation-card
            # / Create New Listing click and retain the known Step 3 timeout
            # error string consumed by step3_transition recovery.
            _advance_brand_confirmation(page, term)

        _, actual_brand = _current_target_values(page)
        return _verify_selected_value("Step 2 URL", term, actual_brand)

    raise RuntimeError(
        "Makro Step 2 did not confirm the supplier-grounded brand through Check Brand. "
        f"brand_status={str(hints.brand_status or '').strip()!r}, "
        f"supplier_brand={str(hints.brand or '').strip()!r}, queries={attempted!r}"
    )


__all__ = ["select_brand"]
