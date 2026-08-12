"""Resilient Makro Step 1 vertical selection.

This keeps product semantics in ``listing_creation`` while replacing only the
live taxonomy traversal mechanics. Exact live-node AI choices remain mandatory;
wrong branches are boundedly backtracked and exhausted trees fall through to the
existing Makro search fallback.

A retry can begin with a partially opened taxonomy path left by an earlier
failed acceptance. Makro is a SPA, so navigating to the same route is not a
reliable way to reset that React state. Such retries therefore use Makro's own
Vertical Search instead of forcing a page reset. The search path still selects
only exact live Makro results and keeps the existing vertical verification.
"""

from __future__ import annotations

from playwright.sync_api import Page

from .listing_creation import (
    JSONTaskProvider,
    ListingBootstrapHints,
    _complete_vertical_leaf,
    _select_vertical_via_search,
    _vertical_confirmation_content,
    _vertical_search_input,
    choose_taxonomy_candidate,
    is_brand_step,
    is_vertical_step,
)
from .taxonomy_navigation import navigate_live_taxonomy
from .taxonomy_resilient import ResilientMakroTaxonomyBrowser


def _select_via_search_with_context(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int,
    reason: str,
) -> str:
    """Run the existing exact-live search fallback with a useful failure cause."""

    try:
        return _select_vertical_via_search(
            page,
            provider,
            hints,
            wait_ms=wait_ms,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"Makro Step 1 {reason}; the exact-live Vertical Search fallback also failed: {exc}"
        ) from exc


def select_vertical(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int = 800,
) -> str:
    """Select one verified Makro vertical with bounded branch backtracking.

    Fresh Step 1 pages use the live taxonomy tree. If an earlier failed run left
    multiple taxonomy columns open, do not reload or otherwise mutate the SPA in
    an attempt to reset it. Use the portal's own bounded Vertical Search instead;
    that route is specifically designed to resolve a vertical from an arbitrary
    Step 1 browsing state and still verifies the exact live Makro result.
    """

    if not is_vertical_step(page):
        raise RuntimeError("Makro is not on Step 1 / Select Vertical")

    taxonomy = ResilientMakroTaxonomyBrowser(page)
    before_clear = taxonomy.columns()

    search = _vertical_search_input(page)
    search.fill("")
    page.wait_for_timeout(wait_ms)

    if len(before_clear) > 1:
        return _select_via_search_with_context(
            page,
            provider,
            hints,
            wait_ms=wait_ms,
            reason="detected a stale partial taxonomy path from a previous attempt",
        )

    # Clearing the search box may repaint the root taxonomy, so read it again
    # before starting a fresh bounded traversal.
    initial = taxonomy.columns()
    if initial:
        selected = navigate_live_taxonomy(
            page,
            columns_fn=taxonomy.columns,
            click_fn=taxonomy.click_node,
            choose_fn=lambda path, candidates: choose_taxonomy_candidate(
                provider,
                hints,
                path,
                candidates,
            ),
            leaf_ready_fn=lambda: is_brand_step(page) or _vertical_confirmation_content(page),
            complete_leaf_fn=lambda node: _complete_vertical_leaf(page, node),
            wait_ms=wait_ms,
            max_depth=7,
            max_node_attempts=16,
            max_backtracks=6,
            transition_polls=18,
        )
        if selected:
            return selected

    # No readable tree, or all bounded tree branches were semantically rejected.
    # Search remains exact-live and is therefore a safe fallback rather than an
    # invented category shortcut.
    return _select_via_search_with_context(
        page,
        provider,
        hints,
        wait_ms=wait_ms,
        reason="could not resolve a verified vertical through the bounded live taxonomy",
    )


__all__ = ["select_vertical"]
