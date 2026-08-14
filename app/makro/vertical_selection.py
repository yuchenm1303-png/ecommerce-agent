"""Resilient Makro Step 1 vertical selection.

This module owns the production Step 1 selection contract used by the GUI and
Batch flows:
- AI may select only exact live Makro taxonomy/search labels;
- taxonomy branches are boundedly backtracked;
- stale partially-open taxonomy paths resume from the deepest live column and
  backtrack structurally before search is considered;
- a live display label and Makro's canonical URL vertical are intentionally
  different identities. For example ``Air Purifiers`` may become
  ``air_purifier`` in the hash URL. A verified exact-live click plus the portal
  confirmation/Step-2 transition proves the selection; the resulting non-empty
  URL value is then retained as the canonical vertical id.

Makro may delay writing that canonical URL value until the user advances from
the Vertical confirmation card into Step 2, and the URL update itself may lag
slightly behind the Step-2 DOM render. The confirmation state is therefore
verified from the live UI first; canonical URL verification happens only after
the Step-2 transition and uses a bounded wait. If the unique owned workflow page
is already Step 2/3, Step 1 is treated as completed and its committed canonical
vertical is read back rather than attempting to operate a vanished Step-1 input.
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
from .search_surface import click_search_row, read_search_rows
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


def _search_result_delta(
    before: list[str],
    after: list[str],
    taxonomy_columns: list[list[str]],
) -> list[str]:
    """Return only labels newly exposed by one Vertical Search operation.

    ``visible_text_candidates`` intentionally scans the whole page because it is
    also used by older compatibility paths. That is too broad for Step 1 search:
    root departments and already-open taxonomy branches remain visible beside the
    search box and must never masquerade as leaf search results. Treat every label
    that existed before the query, plus every structurally identified taxonomy
    node, as navigation chrome. Only newly appeared live labels may reach the AI
    search-result chooser.
    """

    blocked = {
        normalize_label(value)
        for value in before
        if normalize_label(value)
    }
    for column in taxonomy_columns:
        for value in column:
            key = normalize_label(value)
            if key:
                blocked.add(key)

    output: list[str] = []
    seen: set[str] = set()
    for raw in after:
        value = str(raw or "").strip()
        key = normalize_label(value)
        if not key or key in blocked or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _search_result_leaf(label: str) -> str:
    """Return the leaf identity from one exact live Vertical Search breadcrumb."""

    parts = [part.strip() for part in str(label or "").split("/") if part.strip()]
    return parts[-1] if parts else str(label or "").strip()


def _scoped_vertical_search_candidates(search) -> list[str]:
    """Compatibility wrapper for the canonical hit-tested search surface reader."""

    return read_search_rows(search)


def _wait_for_scoped_vertical_search_candidates(
    page: Page,
    search,
    *,
    timeout_ms: int,
    poll_ms: int = 200,
) -> list[str]:
    """Wait for async Makro autocomplete state instead of assuming a fixed delay."""

    attempts = max(1, int(timeout_ms) // max(1, int(poll_ms)))
    for _ in range(attempts):
        candidates = _scoped_vertical_search_candidates(search)
        if candidates:
            return candidates
        page.wait_for_timeout(poll_ms)
    return _scoped_vertical_search_candidates(search)


def _choose_vertical_search_candidate(
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    term: str,
    candidates: list[str],
) -> str:
    """Choose one exact live search row while treating breadcrumbs as paths.

    A unique row whose *leaf* exactly equals the grounded search term is already
    deterministic and needs no AI call. Ambiguous/non-exact live rows still go to
    the existing constrained chooser, which may only return one supplied label.
    """

    wanted = normalize_label(term)
    exact_leaf = [
        candidate
        for candidate in candidates
        if wanted and normalize_label(_search_result_leaf(candidate)) == wanted
    ]
    if len(exact_leaf) == 1:
        return exact_leaf[0]
    return choose_vertical_candidate(provider, hints, term, candidates)


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


def _verify_retry_canonical(
    page: Page,
    selected: str,
    *,
    previous_canonical: str,
    actual_canonical: str,
    selected_visible: bool,
) -> None:
    """Reject a no-op retry that merely preserves an unrelated stale vertical."""

    previous = str(previous_canonical or "").strip()
    actual = str(actual_canonical or "").strip()
    if not previous or normalize_label(previous) != normalize_label(actual):
        return
    if _display_slug_equivalent(selected, actual) or selected_visible:
        return
    raise RuntimeError(
        "Makro Step 1 exact-live click did not produce independently verifiable vertical state: "
        f"selected={selected!r}, canonical={actual!r}"
    )


def _wait_for_canonical_vertical(page: Page, *, timeout_s: float = 10.0) -> str:
    """Wait for Makro's URL machine vertical after the Step-2 UI appears."""

    ready = _wait_for(
        lambda current: bool(_current_target_values(current)[0]),
        page,
        timeout_s=timeout_s,
    )
    if not ready:
        return ""
    actual, _ = _current_target_values(page)
    return str(actual or "").strip()


def _complete_exact_live_vertical(
    page: Page,
    selected: str,
    *,
    previous_canonical: str = "",
    verification_label: str = "",
) -> str:
    """Finish one exact-live Vertical click and return Makro's canonical id.

    ``selected`` is always the exact live UI text that was clicked. Search rows
    may be full breadcrumbs, while the confirmation card/URL represents only the
    leaf Vertical. ``verification_label`` lets those two portal identities remain
    separate without weakening either exact-live click or canonical verification.
    """

    verify_as = str(verification_label or selected).strip()
    transitioned = _wait_for(
        lambda current: is_brand_step(current) or _vertical_confirmation_content(current),
        page,
        timeout_s=15.0,
    )
    if not transitioned:
        raise RuntimeError(
            f"Makro Step 1 selected live vertical {selected!r}, but neither Step 2 nor the vertical confirmation appeared"
        )

    selected_visible = _selected_label_visible(page, verify_as)

    if is_brand_step(page):
        canonical_after = _wait_for_canonical_vertical(page)
        if not canonical_after:
            raise RuntimeError("Makro Step 1 reached Step 2 but no canonical vertical appeared in the listing URL")
        _verify_retry_canonical(
            page,
            verify_as,
            previous_canonical=previous_canonical,
            actual_canonical=canonical_after,
            selected_visible=selected_visible,
        )
        return canonical_after

    canonical_before_brand, _ = _current_target_values(page)
    if not canonical_before_brand and not selected_visible:
        raise RuntimeError(
            "Makro Step 1 vertical confirmation appeared without either the selected live leaf "
            f"or a canonical URL value: selected={selected!r}, verify_as={verify_as!r}"
        )

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

    canonical_after = _wait_for_canonical_vertical(page)
    if not canonical_after:
        raise RuntimeError("Makro Step 1 reached Step 2 but no canonical vertical appeared in the listing URL")

    _verify_retry_canonical(
        page,
        verify_as,
        previous_canonical=previous_canonical,
        actual_canonical=canonical_after,
        selected_visible=selected_visible,
    )
    return canonical_after


def _resume_partial_taxonomy(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    taxonomy: ResilientMakroTaxonomyBrowser,
    initial_columns: list[list[str]],
    *,
    wait_ms: int,
) -> str:
    """Resume an already-open taxonomy from deepest visible column outward.

    A partially-open Step 1 is valid live state, not a reason to require the user
    to reset the page. The deepest currently rendered column is the most specific
    safe continuation point. ``navigate_live_taxonomy`` already owns bounded
    child settling/backtracking, so expose a shifted view of the live columns and
    translate its relative click levels back to the real Makro column indexes.

    If the deepest branch has no semantically usable route, retry one level
    outward. That lets the chooser replace a stale parent without guessing which
    row was highlighted by the previous attempt. Search remains only a final
    compatibility fallback after all visible structural recovery points fail.
    """

    depth = len(initial_columns)
    if depth < 2:
        return ""

    for start_level in range(depth - 1, -1, -1):
        def shifted_columns(base: int = start_level) -> list[list[str]]:
            current = taxonomy.columns()
            if base >= len(current):
                return []
            return [list(column) for column in current[base:]]

        def shifted_click(relative_level: int, text: str, base: int = start_level) -> bool:
            return taxonomy.click_node(base + int(relative_level), text)

        selected = navigate_live_taxonomy(
            page,
            columns_fn=shifted_columns,
            click_fn=shifted_click,
            choose_fn=lambda path, candidates: choose_taxonomy_candidate(
                provider,
                hints,
                path,
                candidates,
            ),
            leaf_ready_fn=lambda: is_brand_step(page) or _vertical_confirmation_content(page),
            complete_leaf_fn=lambda node: _complete_exact_live_vertical(page, node),
            wait_ms=wait_ms,
            max_depth=max(1, 7 - start_level),
            max_node_attempts=12,
            max_backtracks=5,
            transition_polls=18,
        )
        if selected:
            return selected

    return ""


def _select_via_search_with_context(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int,
    reason: str,
) -> str:
    """Resolve a Vertical through Makro's live search with explicit surface ownership."""

    search = _vertical_search_input(page)
    taxonomy = ResilientMakroTaxonomyBrowser(page)
    attempted: list[str] = []
    observed: list[str] = []
    for term in hints.vertical_search_terms:
        attempted.append(term)

        search.fill("")
        page.wait_for_timeout(min(max(wait_ms // 2, 120), 400))
        before = _visible_text_candidates(page)
        baseline_columns = taxonomy.columns()

        search.fill(term)

        selected = ""
        selected_from_surface = False
        for submit in (False, True):
            if submit:
                try:
                    search.press("Enter")
                except Exception:
                    pass

            scoped = _wait_for_scoped_vertical_search_candidates(
                page,
                search,
                timeout_ms=max(2400, wait_ms * 4),
            )
            for item in scoped:
                if item not in observed:
                    observed.append(item)

            if scoped:
                # Search-surface provenance is stronger than page history. Do not
                # run these rows through the stale-taxonomy delta filter: an exact
                # result may legitimately have the same label as a node exposed by
                # a failed partial taxonomy attempt.
                candidates = scoped
                from_surface = True
            else:
                candidates = _search_result_delta(
                    before,
                    _visible_text_candidates(page),
                    baseline_columns,
                )
                from_surface = False
                for item in candidates:
                    if item not in observed:
                        observed.append(item)

            if not candidates:
                continue
            candidate = _choose_vertical_search_candidate(
                provider,
                hints,
                term,
                candidates,
            )
            if candidate:
                selected = candidate
                selected_from_surface = from_surface
                break

        if not selected:
            continue

        previous_canonical, _ = _current_target_values(page)
        clicked = (
            click_search_row(search, selected)
            if selected_from_surface
            else _click_exact_visible_text(page, selected)
        )
        if not clicked:
            source = "search surface" if selected_from_surface else "page fallback"
            raise RuntimeError(
                f"Makro Step 1 could not click selected live vertical from {source}: {selected!r}"
            )
        return _complete_exact_live_vertical(
            page,
            selected,
            previous_canonical=previous_canonical,
            verification_label=_search_result_leaf(selected),
        )

    observed_text = " | ".join(observed[:12]) if observed else "<none>"
    raise RuntimeError(
        f"Makro Step 1 {reason}; bounded exact-live Vertical Search found no verified result from: "
        + " | ".join(attempted)
        + f"; observed search rows: {observed_text}"
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


def _committed_vertical_from_later_stage(page: Page) -> str:
    """Read Step 1's committed result from a page that already reached Step 2/3."""

    try:
        later = is_product_info_step(page) or is_brand_step(page)
    except Exception:
        later = False
    if not later:
        return ""
    try:
        canonical, _ = _current_target_values(page)
    except Exception as exc:
        raise RuntimeError(
            "Makro page is already Step 2/3 but its committed canonical vertical cannot be read"
        ) from exc
    value = str(canonical or "").strip()
    if not value:
        raise RuntimeError(
            "Makro page is already Step 2/3 but its listing URL has no committed canonical vertical"
        )
    return value


def select_vertical(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int = 800,
) -> str:
    """Select or read one verified Makro Vertical and return its canonical URL id."""

    committed = _committed_vertical_from_later_stage(page)
    if committed:
        return committed

    if not is_vertical_interaction_ready(page):
        raise RuntimeError("Makro Step 1 / Select Vertical is not safely operable")

    taxonomy = ResilientMakroTaxonomyBrowser(page)
    before_clear = taxonomy.columns()

    search = _vertical_search_input(page)
    search.fill("")
    page.wait_for_timeout(wait_ms)

    if len(before_clear) > 1:
        recovered = _resume_partial_taxonomy(
            page,
            provider,
            hints,
            taxonomy,
            before_clear,
            wait_ms=wait_ms,
        )
        if recovered:
            return recovered
        return _select_via_search_with_context(
            page,
            provider,
            hints,
            wait_ms=wait_ms,
            reason="could not resume the stale partial taxonomy structurally",
        )

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
