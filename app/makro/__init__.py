from __future__ import annotations

"""Small stable public surface for Makro browser primitives.

Specialized diagnostics (coverage, visual hold, photos, persisted inspection)
should import their concrete submodules directly. Keeping ``app.makro`` small
prevents unrelated diagnostic helpers from breaking every Makro import.
"""

from .domain import MakroDomainAdapter
from .fields import build_semantic_fields, derive_attribute_key, parse_completion_counter
from .listing import (
    MAKRO_HOME_URL,
    MakroListingTarget,
    assert_expected_vertical,
    is_listing_url,
    listing_vertical,
    parse_makro_listing_url,
    wait_for_authenticated_listing,
)
from .sections import base_section_title

__all__ = [
    "MAKRO_HOME_URL",
    "MakroDomainAdapter",
    "MakroListingTarget",
    "assert_expected_vertical",
    "base_section_title",
    "build_semantic_fields",
    "derive_attribute_key",
    "is_listing_url",
    "listing_vertical",
    "parse_completion_counter",
    "parse_makro_listing_url",
    "wait_for_authenticated_listing",
]
