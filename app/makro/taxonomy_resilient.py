"""Production wrapper for the owned Makro Step 1 taxonomy surface.

Makro has one trustworthy taxonomy DOM owner: the Select Vertical content surface
anchored to the live Step-1 vertical search control and its verified heading.  The
catalog harvester already enforces that ownership boundary.  Production traversal
must use the same sensor instead of maintaining a second page-global heuristic that
can mistake Seller dashboard navigation for taxonomy columns.

The wrapper intentionally keeps production reads resilient while preserving the
catalog sensor's fail-closed ownership rules: a transient repaint produces no
columns, and a click is allowed only when the exact node can be rebound inside the
owned Select Vertical surface.
"""

from __future__ import annotations

from playwright.sync_api import Page

from .catalog_taxonomy import CatalogTaxonomyBrowser


class ResilientMakroTaxonomyBrowser:
    """Read/click only the verified Step-1 taxonomy owner, tolerating repaint gaps."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self._owned = CatalogTaxonomyBrowser(page)

    @property
    def last_diagnostic(self) -> str:
        return str(getattr(self._owned, "last_diagnostic", "") or "")

    def columns(self, *, max_items_per_level: int = 160) -> list[list[str]]:
        try:
            return self._owned.columns(max_items_per_level=int(max_items_per_level))
        except Exception:
            # Makro repaints the Step-1 surface asynchronously.  A missing owned
            # surface is a temporary/non-operable observation, never permission to
            # fall back to page-global Dashboard/Orders elements.
            return []

    def click_node(self, level: int, text: str, *, max_items_per_level: int = 160) -> bool:
        wanted = " ".join(str(text or "").split()).strip()
        if level < 0 or not wanted:
            return False
        try:
            return bool(
                self._owned.click_node(
                    int(level),
                    wanted,
                    max_items_per_level=int(max_items_per_level),
                )
            )
        except Exception:
            # Mechanical ownership verification failed.  Do not attempt any
            # body-wide substitute click; the caller will fail safely/backtrack.
            return False


__all__ = ["ResilientMakroTaxonomyBrowser"]
