"""Makro domain adapter / skill layer facade.

Owns all Makro-specific behavior used by the fill CLI:

- listing-page recognition and vertical guard;
- section title normalization, discovery, safe open/cancel;
- semantic field discovery and field locator strategy;
- page validation/readback after filling.

The CLI keeps only policy (which section to fill, dry-run only). No category
field lists are hard-coded; everything is discovered from the live DOM.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from ..makro_dryrun import FillVerification, fill_resolved_field
from .fields import build_semantic_fields, scroll_and_capture
from .listing import (
    MakroListingTarget,
    assert_expected_vertical,
    is_makro_listing_page,
    parse_makro_listing_url,
    wait_for_authenticated_listing,
)
from .locators import selector_for_control
from .sections import (
    base_section_title,
    find_section,
    find_sections,
    open_section_for_edit,
    scan_section_fields,
    scan_sections,
)


class MakroDomainAdapter:
    """Skill layer for the Makro Add a Single Listing page."""

    def __init__(self, page: Page) -> None:
        self.page = page

    # -- listing recognition / guard --------------------------------------

    def is_listing_page(self) -> bool:
        """True only when the authenticated single-listing UI is visible."""
        return is_makro_listing_page(self.page)

    def current_target(self) -> MakroListingTarget | None:
        """Parse the current URL; None when it is not a valid listing URL."""
        try:
            return parse_makro_listing_url(self.page.url)
        except ValueError:
            return None

    def assert_expected_vertical(self, expected_vertical: str | None) -> None:
        """Fail closed before scanning/filling on a wrong vertical."""
        assert_expected_vertical(self.page, expected_vertical)

    def wait_for_authenticated_listing(
        self,
        initial_url: str | None = None,
        *,
        headless: bool = False,
        timeout_s: int = 30,
        navigate_first: bool = True,
    ) -> None:
        """Let the user log in / navigate in the same Edge window, then wait."""
        wait_for_authenticated_listing(
            self.page,
            initial_url,
            headless=headless,
            timeout_s=timeout_s,
            navigate_first=navigate_first,
        )

    # -- section semantics ------------------------------------------------

    def base_section_title(self, title: str) -> str:
        """Normalize a UI section title to its stable semantic identity."""
        return base_section_title(title)

    def find_sections(self) -> list[dict[str, Any]]:
        return find_sections(self.page)

    def find_section(self, wanted: str) -> dict[str, Any] | None:
        return find_section(self.page, wanted)

    def open_section_for_edit(self, section: dict[str, Any]) -> None:
        """Click only the safe EDIT control of a collapsed section."""
        open_section_for_edit(self.page, section)

    def scan_sections(
        self,
        *,
        include_values: bool = False,
        wait_ms: int = 350,
        max_scroll_steps: int = 200,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        return scan_sections(
            self.page,
            include_values=include_values,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )

    def scan_section_fields(
        self,
        section_path: str,
        *,
        include_values: bool = False,
        wait_ms: int = 350,
        max_scroll_steps: int = 200,
    ) -> list[dict[str, Any]]:
        return scan_section_fields(
            self.page,
            section_path,
            include_values=include_values,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )

    def scroll_and_capture(
        self,
        *,
        include_values: bool = False,
        open_dropdowns: bool = False,
        wait_ms: int = 350,
        max_scroll_steps: int = 200,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return scroll_and_capture(
            self.page,
            include_values=include_values,
            open_dropdowns=open_dropdowns,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )

    # -- semantic field discovery / locator / execution -------------------

    def build_semantic_fields(self, controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return build_semantic_fields(controls)

    def selector_for(self, control: dict[str, Any]) -> str:
        """Deterministic field locator strategy for one control."""
        return selector_for_control(control)

    def fill_resolved_field(
        self, semantic_field: dict[str, Any], answer: Any
    ) -> FillVerification:
        """Fill only a resolved answer and read it back; never Save / Send to QC."""
        return fill_resolved_field(self.page, semantic_field, answer)
