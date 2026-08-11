"""Resilient Makro Step 1 vertical selection.

This keeps product semantics in ``listing_creation`` while replacing only the
live taxonomy traversal mechanics. Exact live-node AI choices remain mandatory;
wrong branches are boundedly backtracked and exhausted trees fall through to the
existing Makro search fallback.
"""

from __future__ import annotations

import time

from playwright.sync_api import Page

from .listing_creation import (
    JSONTaskProvider,
    ListingBootstrapHints,
    MAKRO_NEW_LISTING_URL,
    _complete_vertical_leaf,
    _select_vertical_via_search,
    _vertical_confirmation_content,
    _vertical_search_input,
    choose_taxonomy_candidate,
    is_brand_step,
    is_vertical_step,
)
from .step3_transition import dismiss_joyride_overlay
from .taxonomy_navigation import navigate_live_taxonomy
from .taxonomy_resilient import ResilientMakroTaxonomyBrowser


def _reset_partial_taxonomy_if_needed(
    page: Page,
    taxonomy: ResilientMakroTaxonomyBrowser,
    *,
    wait_ms: int,
) -> list[list[str]]:
    """Discard a stale partially selected Step 1 path before a fresh run.

    A failed acceptance intentionally leaves the browser现场 intact. A retry can
    therefore start with multiple taxonomy columns already open. Re-clicking the
    already-selected parent would leave the same child signature and look like a
    failed transition. Reloading the same Step 1 route is safe because no
    vertical has been confirmed yet; Step 2/3 pages never enter this function.
    """

    initial = taxonomy.columns()
    if len(initial) <= 1:
        return initial

    page.goto(MAKRO_NEW_LISTING_URL, wait_until="domcontentloaded", timeout=45_000)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if is_vertical_step(page):
            break
        page.wait_for_timeout(250)
    if not is_vertical_step(page):
        raise RuntimeError("Makro Step 1 could not reset a stale partial taxonomy path")

    dismiss_joyride_overlay(page)
    search = _vertical_search_input(page)
    search.fill("")
    page.wait_for_timeout(wait_ms)
    return taxonomy.columns()


def select_vertical(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int = 800,
) -> str:
    """Select one verified Makro vertical with bounded branch backtracking."""

    if not is_vertical_step(page):
        raise RuntimeError("Makro is not on Step 1 / Select Vertical")

    search = _vertical_search_input(page)
    search.fill("")
    page.wait_for_timeout(wait_ms)

    taxonomy = ResilientMakroTaxonomyBrowser(page)
    initial = _reset_partial_taxonomy_if_needed(
        page,
        taxonomy,
        wait_ms=wait_ms,
    )
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

    # A readable taxonomy that contains no semantically valid bounded path is
    # not a fatal portal error. Reset through the existing exact-live search
    # fallback using the canonical Product Identity search term.
    try:
        return _select_vertical_via_search(
            page,
            provider,
            hints,
            wait_ms=wait_ms,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            "Makro Step 1 exhausted the bounded live taxonomy and the live search fallback also failed: "
            f"{exc}"
        ) from exc


__all__ = ["select_vertical"]
