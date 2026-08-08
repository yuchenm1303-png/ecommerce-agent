from __future__ import annotations

"""Stable public surface for deterministic Makro browser primitives.

This package exposes DOM discovery, listing guards, section lifecycle and the
browser domain adapter. Product-semantic resolution intentionally lives outside
this package and is AI-first.
"""

from .domain import MakroDomainAdapter
from .fields import (
    build_semantic_fields,
    capture_controls,
    capture_dropdown_options,
    derive_attribute_key,
    find_scroll_containers,
    merge_scans,
    scroll_and_capture,
    scroll_container,
    scroll_to_end,
    scroll_window,
)
from .listing import (
    MAKRO_HOME_URL,
    MAKRO_HOST,
    MAKRO_SINGLE_LISTING_ROUTE,
    MakroListingTarget,
    assert_expected_vertical,
    is_listing_url,
    is_makro_host,
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
from .snapshot import sanitize_dom_snapshot

__all__ = [
    "MAKRO_HOME_URL",
    "MAKRO_HOST",
    "MAKRO_SINGLE_LISTING_ROUTE",
    "MakroDomainAdapter",
    "MakroListingTarget",
    "assert_expected_vertical",
    "base_section_title",
    "build_semantic_fields",
    "capture_controls",
    "capture_dropdown_options",
    "derive_attribute_key",
    "find_scroll_containers",
    "find_section",
    "find_sections",
    "is_listing_url",
    "is_makro_host",
    "is_makro_listing_page",
    "merge_scans",
    "open_section_for_edit",
    "parse_makro_listing_url",
    "sanitize_dom_snapshot",
    "scan_section_fields",
    "scan_sections",
    "scroll_and_capture",
    "scroll_container",
    "scroll_to_end",
    "scroll_window",
    "selector_for_control",
    "wait_for_authenticated_listing",
]
