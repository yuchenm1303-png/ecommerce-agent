from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page

from app.models import ProductRecord
from app.platforms.base import PlatformAdapter


MAKRO_HOST = "seller.makro.co.za"
MAKRO_SINGLE_LISTING_ROUTE = "dashboard/addListings/single"


@dataclass(frozen=True)
class MakroListingTarget:
    url: str
    brand: str | None
    vertical: str | None
    request_id: str | None
    vid: str | None


def parse_makro_listing_url(url: str) -> MakroListingTarget:
    """Validate and parse a Makro Marketplace single-listing hash URL.

    Makro uses a hash-routed SPA. Parameters such as requestId can be short-lived,
    so callers must not treat them as stable product identifiers.
    """

    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Makro listing URL must use http/https")
    if parsed.hostname != MAKRO_HOST:
        raise ValueError(f"Expected host {MAKRO_HOST!r}, got {parsed.hostname!r}")

    route, separator, query_string = parsed.fragment.partition("?")
    if route.rstrip("/") != MAKRO_SINGLE_LISTING_ROUTE:
        raise ValueError(
            "URL is not a Makro Add a Single Listing page: "
            f"expected #{MAKRO_SINGLE_LISTING_ROUTE}"
        )

    params = parse_qs(query_string if separator else "", keep_blank_values=True)

    def first(name: str) -> str | None:
        values = params.get(name)
        return values[0] if values else None

    return MakroListingTarget(
        url=url.strip(),
        brand=first("brand"),
        vertical=first("vertical"),
        request_id=first("requestId"),
        vid=first("vid"),
    )


def is_makro_listing_page(page: Page) -> bool:
    """Return True only when the authenticated single-listing UI is visible."""

    parsed = urlparse(page.url)
    if parsed.hostname != MAKRO_HOST:
        return False

    # Never probe or fill a login form.
    if page.locator('input[type="password"]').count() > 0:
        return False

    markers = (
        "Add a Single Listing",
        "ADD PRODUCT INFO",
        "Please fill all mandatory attributes",
    )
    return any(page.get_by_text(marker, exact=False).count() > 0 for marker in markers)


class MakroPlatformAdapter(PlatformAdapter):
    """Guarded first adapter for Makro Marketplace.

    The first real-platform milestone is DOM discovery and dry-run filling. Final
    save/submission stays disabled until selectors and success signals have been
    captured from the authenticated site on the user's own computer.
    """

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
