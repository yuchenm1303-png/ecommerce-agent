"""Makro domain adapter / skill layer.

The Makro-specific behavior that used to live inside the probe/fill CLIs now
lives here:

- fields.py    : deterministic DOM control capture + semantic field grouping
- sections.py  : listing section discovery, safe expand/cancel, title normalization
- listing.py   : listing-page recognition, vertical guard, authenticated wait
- snapshot.py  : sanitized DOM snapshot writer (read-only)
- locators.py  : field locator strategy
- fallback.py  : deterministic-first / future AI-fallback interfaces

``MakroDomainAdapter`` (domain.py) is imported explicitly by callers; it is not
re-exported here to avoid a circular import with ``app.makro_dryrun``.
"""

from __future__ import annotations

from .fallback import DeterministicOnlyFallback, SemanticFallback
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
    "MakroListingTarget",
    "SemanticFallback",
    "DeterministicOnlyFallback",
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
