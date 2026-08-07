"""Guarded first adapter for Makro Marketplace.

The real-platform milestone is DOM discovery and dry-run filling. Final
save/submission stays disabled until selectors and success signals have been
captured from the authenticated site on the user's own computer.

Makro-specific recognition/parsing lives in ``app.makro.listing``; this
adapter only re-exports those helpers and keeps the legacy PlatformAdapter
interface for the mock/runner flow.
"""

from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page

from app.makro.listing import (
    MAKRO_HOST,
    MAKRO_SINGLE_LISTING_ROUTE,
    MakroListingTarget,
    is_makro_listing_page,
    parse_makro_listing_url,
)
from app.models import ProductRecord
from app.platforms.base import PlatformAdapter

# Legacy names kept importable from here (see tests/test_makro.py).
__all__ = [
    "MAKRO_HOST",
    "MAKRO_SINGLE_LISTING_ROUTE",
    "MakroListingTarget",
    "is_makro_listing_page",
    "parse_makro_listing_url",
    "MakroPlatformAdapter",
]


class MakroPlatformAdapter(PlatformAdapter):
    """Guard adapter for Makro Marketplace (legacy interface)."""

    def __init__(self, listing_url: str) -> None:
        self.target = parse_makro_listing_url(listing_url)

    def open_product(self, page: Page, product: ProductRecord) -> None:
        page.goto(self.target.url, wait_until="domcontentloaded")

    def verify_product(self, page: Page, product: ProductRecord) -> bool:
        # On Add Listing there is no existing product identity to compare yet.
        # We therefore verify the platform/page identity only. SKU verification is
        # performed after the SKU field has been populated in the real workflow.
        return is_makro_listing_page(page)

    def save(self, page: Page) -> str:
        raise RuntimeError(
            "Makro real-platform save is intentionally disabled in this stage. "
            "Run in dry-run mode until the authenticated DOM capture has been reviewed."
        )
