"""Makro domain adapter / skill layer facade.

Owns all Makro-specific behavior used by the fill CLI:

- listing-page recognition and vertical guard;
- section title normalization, discovery, open/cancel/save lifecycle;
- semantic field discovery and field locator strategy;
- product photo staging and post-Save persistence verification;
- pre-save and post-save field readback verification.

The CLI keeps policy/orchestration only. No category field lists are hard-coded;
everything is discovered from the live DOM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from playwright.sync_api import Page

from ..makro_dryrun import FillVerification, fill_resolved_field, verify_resolved_field
from .fields import build_semantic_fields, scroll_and_capture
from .listing import (
    MakroListingTarget,
    assert_expected_vertical,
    is_makro_listing_page,
    parse_makro_listing_url,
    wait_for_authenticated_listing,
)
from .locators import selector_for_control
from .photos import (
    PhotoUploadResult,
    inspect_product_photos,
    upload_product_photos,
    verify_persisted_photo_count,
)
from .sections import (
    base_section_title,
    cancel_section,
    find_section,
    find_sections,
    open_section_for_edit,
    save_section,
    scan_section_fields,
    scan_sections,
    visible_section_errors,
)


class MakroDomainAdapter:
    """Skill layer for the Makro Add a Single Listing page."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def is_listing_page(self) -> bool:
        return is_makro_listing_page(self.page)

    def current_target(self) -> MakroListingTarget | None:
        try:
            return parse_makro_listing_url(self.page.url)
        except ValueError:
            return None

    def assert_expected_vertical(self, expected_vertical: str | None) -> None:
        assert_expected_vertical(self.page, expected_vertical)

    def wait_for_authenticated_listing(
        self,
        initial_url: str | None = None,
        *,
        headless: bool = False,
        timeout_s: int = 30,
        navigate_first: bool = True,
    ) -> None:
        wait_for_authenticated_listing(
            self.page,
            initial_url,
            headless=headless,
            timeout_s=timeout_s,
            navigate_first=navigate_first,
        )

    def base_section_title(self, title: str) -> str:
        return base_section_title(title)

    def find_sections(self) -> list[dict[str, Any]]:
        return find_sections(self.page)

    def find_section(self, wanted: str) -> dict[str, Any] | None:
        return find_section(self.page, wanted)

    def open_section_for_edit(self, section: dict[str, Any]) -> None:
        open_section_for_edit(self.page, section)

    def cancel_section(self, section_title: str, *, wait_ms: int = 450) -> None:
        cancel_section(self.page, section_title, wait_ms=wait_ms)

    def save_section(self, section_title: str, *, timeout_s: float = 15.0) -> None:
        save_section(self.page, section_title, timeout_s=timeout_s)

    def visible_section_errors(self, section_path: str) -> list[str]:
        return visible_section_errors(self.page, section_path)

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

    def inspect_product_photos(self) -> dict[str, Any]:
        return inspect_product_photos(self.page)

    def upload_product_photos(
        self,
        image_paths: Iterable[str | Path],
        *,
        timeout_ms: int = 30_000,
    ) -> PhotoUploadResult:
        return upload_product_photos(
            self.page,
            image_paths,
            timeout_ms=timeout_ms,
        )

    def verify_persisted_photo_count(
        self,
        *,
        initial_count: int | None,
        expected_added: int,
    ) -> dict[str, Any]:
        return verify_persisted_photo_count(
            self.page,
            initial_count=initial_count,
            expected_added=expected_added,
        )

    def build_semantic_fields(self, controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return build_semantic_fields(controls)

    def selector_for(self, control: dict[str, Any]) -> str:
        return selector_for_control(control)

    def fill_resolved_field(
        self,
        semantic_field: dict[str, Any],
        answer: Any,
        *,
        section_path: str | None = None,
        recheck_wait_ms: int = 800,
    ) -> FillVerification:
        return fill_resolved_field(
            self.page,
            semantic_field,
            answer,
            section_path=section_path,
            recheck_wait_ms=recheck_wait_ms,
        )

    def verify_resolved_field(
        self,
        semantic_field: dict[str, Any],
        answer: Any,
        *,
        section_path: str | None = None,
    ) -> FillVerification:
        return verify_resolved_field(
            self.page,
            semantic_field,
            answer,
            section_path=section_path,
        )