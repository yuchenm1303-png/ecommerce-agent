"""Resilient Makro Step 1 vertical selection.

This module owns the production Step 1 selection contract used by the GUI and
Batch flows:
- AI may select only exact live Makro taxonomy/search labels;
- taxonomy branches are boundedly backtracked;
- stale partially-open taxonomy paths use Makro's own Vertical Search rather
  than attempting to reset the SPA;
- a live display label and Makro's canonical URL vertical are intentionally
  different identities. For example ``Air Purifiers`` may become
  ``air_purifier`` in the hash URL. A verified exact-live click plus the portal
  confirmation/Step-2 transition proves the selection; the resulting non-empty
  URL value is then retained as the canonical vertical id.

Brand verification remains strict in ``listing_creation``. This module does not
weaken Step 2 or any Step 3 safety gate.
"""

from __future__ import annotations

from playwright.sync_api import Page

from .listing_creation import (
    JSONTaskProvider,
    ListingBootstrapHints,
    _body_text,
    _click_exact_visible_text,
    _current_target_values,
    _vertical_confirmation_content,
    _vertical_search_input,
    _vertical_select_brand_button,
    _visible_text_candidates,
    _wait_for,
    choose_taxonomy_candidate,
    choose_vertical_candidate,
    is_brand_step,
    is_product_info_step,
    is_vertical_step,
    normalize_label,
)
from .taxonomy_navigation import navigate_live_taxonomy
from .taxonomy_resilient import ResilientMakroTaxonomyBrowser


_VERTICAL_INPUT_TOKENS = (
    "vertical",
    "category",
    "categories",
    "垂直",
    "类别",
    "分类",
    "类目",
    "品类",
)
_VERTICAL_BODY_MARKERS = (
    "select the vertical for your product",
    "browse verticals",
    "select vertical",
    "选择待产品的垂直领域",
    "浏览垂直栏目",
    "选择垂直领域",
    "进入垂直类别",
)


def _singularize_vertical_token(token: str) -> str:
    """Normalize ordinary English display pluralisation for URL slug comparison."""

    value = str(token or "").strip().casefold()
    if len(value) > 4 and value.endswith("ies"):
        return value[:-3] + "y"
    if len(value) > 4 and value.endswith(("ches", "shes", "xes", "zes")):
        return value[:-2]
    if len(value) > 4 and value.endswith("sses"):
        return value[:-2]
    if len(value) > 3 and value.endswith("s") and not value.endswith("ss"):
        return value[:-1]
    return value


def _vertical_identity_tokens(value: str) -> tuple[str, ...]:
    normalized = normalize_label(value)
    return tuple(
        _singularize_vertical_token(token)
        for token in normalized.split()
        if _singularize_vertical_token(token)
    )


def _display_slug_equivalent(display_label: str, canonical: str) -> bool:
    """Compare a human Vertical label with Makro's machine URL slug.

    This is deliberately narrow: punctuation/space/underscore differences and
    ordinary English pluralisation are tolerated. It is not used for Brand.
    """

    left = _vertical_identity_tokens(display_label)
    right = _vertical_identity_tokens(canonical)
    return bool(left and right and left == right)


def _selected_label_visible(page: Page, selected: str) -> bool:
    selected_key = normalize_label(selected)
    if not selected_key:
        return False
    try:
        return selected_key in normalize_label(_body_text(page))
    except Exception:
        return False


def _vertical_search_semantics_visible(page: Page) -> bool:
    """Require Step-1-specific evidence around the fallback search control.

    ``MakroPortalAdapter.find_search_input`` intentionally has a one-visible-input
    fallback. That is useful once the stage is already known, but it must not be
    used as independent evidence that an UNKNOWN SPA state is Step 1, otherwise a
    lone Step 2 Brand input could be misclassified as Vertical.
    """

    try:
        search = _vertical_search_input(page)
    except Exception:
        return False

    attributes: list[str] = []
    for name in ("placeholder", "name", "id", "aria-label", "title", "data-testid", "class"):
        try:
            value = search.get_attribute(name)
        except Exception:
            value = None
        if value:
            attributes.append(str(value))
    attribute_blob = normalize_label(" ".join(attributes))
    if any(normalize_label(token) in attribute_blob for token in _VERTICAL_INPUT_TOKENS):
        return True

    try:
        body = normalize_label(_body_text(page))
    except Exception:
        body = ""
    return any(normalize_label(marker) in body for marker in _VERTICAL_BODY_MARKERS)


def _complete_exact_live_vertical(
    page: Page,
    selected: str,
    *,
    previous_canonical: str = "",
) -> str:
    """Finish one exact-live Vertical click and return Makro's canonical id.

    The display label is not compared literally with the URL value. Makro is
    free to encode a singular snake_case machine id for a plural human label.
    Safety comes from the evidence chain instead:

    1. ``selected`` was copied from the live Makro UI and exact-clicked;
    2. Makro reaches its vertical confirmation or Step 2;
    3. the resulting listing URL exposes a non-empty canonical vertical;
    4. on a retry where the same canonical id already existed, the selected live
       label must either be label/slug-equivalent or remain visible in the
       confirmed UI.
    """

    transitioned = _wait_for(
        lambda current: is_brand_step(current) or _vertical_confirmation_content(current),
        page,
        timeout_s=15.0,
    )
    if not transitioned:
        raise RuntimeError(
            f"Makro Step 1 selected live vertical {selected!r}, but neither Step 2 nor the vertical confirmation appeared"
        )

    actual_vertical, _ = _current_target_values(page)
    if not actual_vertical:
        raise RuntimeError(
            f"Makro Step 1 selected live vertical {selected!r}, but the listing URL exposed no canonical vertical id"
        )

    previous = str(previous_canonical or "").strip()
    if previous and normalize_label(previous) == normalize_label(actual_vertical):
        equivalent = _display_slug_equivalent(selected, actual_vertical)
        if not equivalent and not _selected_label_visible(page, selected):
            raise RuntimeError(
                "Makro Step 1 exact-live click did not produce independently verifiable vertical state: "
                f"selected={selected!r}, canonical={actual_vertical!r}"
            )

    if is_brand_step(page):
        return actual_vertical

    button = _vertical_select_brand_button(page)
    if button is None:
        raise RuntimeError(
            "Makro Step 1 vertical confirmation appeared, but the exact Select Brand button was not found"
        )
    button.click(timeout=5000)
    if not _wait_for(is_brand_step, page, timeout_s=15.0):
        raise RuntimeError(
            "Makro Step 1 clicked the vertical confirmation Select Brand button, but Step 2 did not appear"
        )

    canonical_after, _ = _current_target_values(page)
    if not canonical_after:
        raise RuntimeError("Makro Step 1 reached Step 2 without a canonical vertical in the listing URL")
    return canonical_after


def _select_via_search_with_context(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int,
    reason: str,
) -> str:
    """Resolve a Vertical through Makro's live search without label/slug conflation."""

    search = _vertical_search_input(page)
    attempted: list[str] = []
    for term in hints.vertical_search_terms:
        attempted.append(term)
        search.fill("")
        search.fill(term)
        page.wait_for_timeout(wait_ms)

        candidates = _visible_text_candidates(page)
        selected = choose_vertical_candidate(provider, hints, term, candidates)
        if not selected:
            continue

        previous_canonical, _ = _current_target_values(page)
        if not _click_exact_visible_text(page, selected):
            raise RuntimeError(f"Makro Step 1 could not click selected live vertical: {selected!r}")
        return _complete_exact_live_vertical(
            page,
            selected,
            previous_canonical=previous_canonical,
        )

    raise RuntimeError(
        f"Makro Step 1 {reason}; bounded exact-live Vertical Search found no verified result from: "
        + " | ".join(attempted)
    )


def is_vertical_interaction_ready(page: Page) -> bool:
    """Return True when Step 1 can be safely operated, not merely stage-labelled.

    A live taxonomy is decisive structural evidence. If only the search control
    is available while the coarse stage detector lags, its own attributes or the
    surrounding page copy must still identify it specifically as Vertical.
    """

    try:
        if is_product_info_step(page) or is_brand_step(page):
            return False
    except Exception:
        return False

    try:
        if is_vertical_step(page):
            return True
    except Exception:
        pass

    try:
        if ResilientMakroTaxonomyBrowser(page).columns():
            return True
    except Exception:
        pass

    return _vertical_search_semantics_visible(page)


def select_vertical(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int = 800,
) -> str:
    """Select one verified Makro Vertical and return its canonical URL id."""

    if not is_vertical_interaction_ready(page):
        raise RuntimeError("Makro Step 1 / Select Vertical is not safely operable")

    taxonomy = ResilientMakroTaxonomyBrowser(page)
    before_clear = taxonomy.columns()

    search = _vertical_search_input(page)
    search.fill("")
    page.wait_for_timeout(wait_ms)

    # A failed acceptance intentionally leaves the browser现场 intact. If
    # multiple taxonomy columns are already open, re-clicking an already selected
    # parent cannot be distinguished reliably from a stale React child column.
    # Use Makro's own exact-live search from that state instead of resetting the
    # SPA or guessing which row is selected.
    if len(before_clear) > 1:
        return _select_via_search_with_context(
            page,
            provider,
            hints,
            wait_ms=wait_ms,
            reason="detected a stale partial taxonomy path from a previous attempt",
        )

    # Clearing the search can repaint the root taxonomy; read it again before
    # starting the bounded traversal.
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
            complete_leaf_fn=lambda node: _complete_exact_live_vertical(page, node),
            wait_ms=wait_ms,
            max_depth=7,
            max_node_attempts=16,
            max_backtracks=6,
            transition_polls=18,
        )
        if selected:
            return selected

    return _select_via_search_with_context(
        page,
        provider,
        hints,
        wait_ms=wait_ms,
        reason="could not resolve a verified vertical through the bounded live taxonomy",
    )


__all__ = ["is_vertical_interaction_ready", "select_vertical"]
