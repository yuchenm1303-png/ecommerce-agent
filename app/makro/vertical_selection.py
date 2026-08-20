"""Makro Step 1 Vertical resolution.

One production decision boundary owns Step 1: Makro must supply every selectable
Vertical. Product Identity supplies semantics and AI plans a bounded specific-to-
broad retrieval ladder. Each query is then handled as one live transaction: create
a fresh query-owned generation, harvest its current Makro rows, choose only from
those rows, click the chosen row in that same generation, and verify the resulting
canonical Vertical before Step 2 is accepted.

The normal path never replays a query merely to rediscover a row that was already
observed. If one query has no clear semantic candidate, the next broader query is
tried. Browse taxonomy is available only when every search generation yields no
semantic candidate at all. If a row was selected from the current generation but
cannot be bound for the click, the workflow fails closed instead of silently
switching mechanisms.

The workflow never invents a Makro Vertical and never clicks Send to QC.
"""

from __future__ import annotations

import json

from playwright.sync_api import Page

from .listing_creation import (
    JSONTaskProvider,
    ListingBootstrapHints,
    _body_text,
    _current_target_values,
    _vertical_confirmation_content,
    _vertical_search_input,
    _vertical_select_brand_button,
    _wait_for,
    choose_vertical_candidate,
    is_brand_step,
    is_product_info_step,
    is_vertical_step,
    normalize_label,
)
from .requested_vertical import current_requested_vertical, requested_vertical_matches_label
from .search_surface import (
    begin_search_query,
    click_search_row,
    read_search_rows,
    wait_for_search_rows,
)
from .taxonomy_navigation import navigate_live_taxonomy
from .taxonomy_resilient import ResilientMakroTaxonomyBrowser
from .taxonomy_resolution import (
    choose_taxonomy_path_candidate,
    validate_taxonomy_leaf_candidate,
)
from .vertical_resolution import plan_vertical_search_terms


_VERTICAL_INPUT_TOKENS = (
    "vertical", "category", "categories", "垂直", "类别", "分类", "类目", "品类",
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


def _vertical_diag(event: str, payload: dict[str, object]) -> None:
    print(
        "MAKRO_VERTICAL_DIAG "
        + json.dumps(
            {"event": str(event or "unknown"), **payload},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ),
        flush=True,
    )


def _singularize_vertical_token(token: str) -> str:
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
    left = _vertical_identity_tokens(display_label)
    right = _vertical_identity_tokens(canonical)
    return bool(left and right and left == right)


def _selected_label_visible(page: Page, selected: str) -> bool:
    key = normalize_label(selected)
    if not key:
        return False
    try:
        return key in normalize_label(_body_text(page))
    except Exception:
        return False


def _search_result_delta(
    before: list[str],
    after: list[str],
    taxonomy_columns: list[list[str]],
) -> list[str]:
    """Legacy pure helper retained for compatibility tests only."""

    blocked = {normalize_label(value) for value in before if normalize_label(value)}
    for column in taxonomy_columns:
        blocked.update(normalize_label(value) for value in column if normalize_label(value))
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
    parts = [part.strip() for part in str(label or "").split("/") if part.strip()]
    return parts[-1] if parts else str(label or "").strip()


def _scoped_vertical_search_candidates(search) -> list[str]:
    return read_search_rows(search)


def _wait_for_scoped_vertical_search_candidates(
    page: Page,
    search,
    *,
    timeout_ms: int,
    poll_ms: int = 200,
) -> list[str]:
    return wait_for_search_rows(page, search, timeout_ms=timeout_ms, poll_ms=poll_ms)


def _choose_vertical_search_candidate(
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    term: str,
    candidates: list[str],
) -> str:
    """Choose one exact row from the active query generation, or decline it."""

    requested = current_requested_vertical()
    if requested:
        matches = [
            candidate
            for candidate in candidates
            if requested_vertical_matches_label(requested, candidate)
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            available = " | ".join(candidates[:20])
            raise ValueError(
                f"手动指定类目 {requested!r} 未匹配到唯一 Makro live Vertical；"
                f"当前候选={available or '<none>'}"
            )
        raise ValueError(
            f"手动指定类目 {requested!r} 同时匹配到多个 Makro live Vertical：{matches!r}"
        )

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
    blob = normalize_label(" ".join(attributes))
    if any(normalize_label(token) in blob for token in _VERTICAL_INPUT_TOKENS):
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
    """Require independent proof that the resulting canonical is the clicked leaf.

    A changed URL is not evidence of correctness. Direct search -> Step 2
    transitions must have a display/slug equivalent canonical. A visible Step 1
    confirmation may serve as independent proof for portals whose canonical slug
    is not linguistically equivalent to the display label.
    """

    previous = str(previous_canonical or "").strip()
    actual = str(actual_canonical or "").strip()
    equivalent = _display_slug_equivalent(selected, actual)
    verified = bool(equivalent or selected_visible)
    _vertical_diag(
        "canonical_verify",
        {
            "selected_leaf": str(selected or "").strip(),
            "previous_canonical": previous,
            "actual_canonical": actual,
            "display_slug_equivalent": equivalent,
            "selected_visible_confirmation": bool(selected_visible),
            "verified": verified,
        },
    )
    if verified:
        return
    raise RuntimeError(
        "Makro Step 1 exact-live click produced a canonical Vertical that cannot be bound to the "
        f"selected live leaf: selected={selected!r}, canonical={actual!r}, previous={previous!r}"
    )


def _wait_for_canonical_vertical(page: Page, *, timeout_s: float = 10.0) -> str:
    ready = _wait_for(
        lambda current: bool(_current_target_values(current)[0]),
        page,
        timeout_s=timeout_s,
    )
    if not ready:
        return ""
    actual, _ = _current_target_values(page)
    return str(actual or "").strip()


def _vertical_confirmation_ready(page: Page, selected: str = "") -> bool:
    """Recognize only a confirmation that belongs to the selected live Vertical.

    During taxonomy backtracking Makro may leave the previous leaf confirmation on
    screen briefly. When a selected label is supplied, every Step 1 confirmation
    variant therefore has to show that exact label before it can be treated as the
    new branch outcome. Step 2 remains safe because semantic rejection happens
    before Select Brand is clicked.
    """

    selected_value = str(selected or "").strip()
    try:
        if is_brand_step(page):
            return True
    except Exception:
        pass
    try:
        if _vertical_confirmation_content(page):
            return not selected_value or _selected_label_visible(page, selected_value)
    except Exception:
        pass
    try:
        if not is_vertical_step(page):
            return False
        if _vertical_select_brand_button(page) is None:
            return False
    except Exception:
        return False

    return not selected_value or _selected_label_visible(page, selected_value)


def _complete_exact_live_vertical(
    page: Page,
    selected: str,
    *,
    previous_canonical: str = "",
    verification_label: str = "",
) -> str:
    verify_as = str(verification_label or selected).strip()
    transitioned = _wait_for(
        lambda current: _vertical_confirmation_ready(current, verify_as),
        page,
        timeout_s=15.0,
    )
    if not transitioned:
        raise RuntimeError(
            f"Makro Step 1 selected live vertical {selected!r}, but no verified Step 1 confirmation or Step 2 appeared"
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
            selected_visible=False,
        )
        return canonical_after

    canonical_before_brand, _ = _current_target_values(page)
    if canonical_before_brand:
        _verify_retry_canonical(
            page,
            verify_as,
            previous_canonical=previous_canonical,
            actual_canonical=canonical_before_brand,
            selected_visible=selected_visible,
        )
    elif not selected_visible:
        raise RuntimeError(
            "Makro Step 1 vertical confirmation appeared without either the selected live leaf "
            f"or a canonical URL value: selected={selected!r}, verify_as={verify_as!r}"
        )

    button = _vertical_select_brand_button(page)
    if button is None:
        raise RuntimeError("Makro Step 1 vertical confirmation appeared, but the exact Select Brand button was not found")
    button.click(timeout=5000)
    if not _wait_for(is_brand_step, page, timeout_s=15.0):
        raise RuntimeError("Makro Step 1 clicked the vertical confirmation Select Brand button, but Step 2 did not appear")

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


def _close_vertical_search(search, page: Page, *, wait_ms: int) -> None:
    """End one query generation without requiring Makro to destroy old DOM."""

    try:
        search.fill("")
    except Exception as exc:
        raise RuntimeError("Makro Step 1 Vertical Search input could not be cleared between query generations") from exc
    try:
        search.press("Escape")
    except Exception:
        pass
    try:
        search.evaluate("el => el.blur()")
    except Exception:
        pass
    if wait_ms > 0:
        page.wait_for_timeout(min(max(int(wait_ms) // 5, 80), 180))


def _run_vertical_search_query(
    page: Page,
    search,
    term: str,
    *,
    wait_ms: int,
) -> list[str]:
    """Run one isolated discovery generation and return only generation-owned rows."""

    _close_vertical_search(search, page, wait_ms=wait_ms)
    generation = begin_search_query(search)
    if generation <= 0:
        raise RuntimeError(
            "Makro Step 1 could not establish a fresh Vertical Search ownership generation "
            f"before query={term!r}"
        )

    search.fill(term)
    rows = _wait_for_scoped_vertical_search_candidates(
        page,
        search,
        timeout_ms=max(3200, wait_ms * 5),
    )
    if not rows:
        try:
            search.press("Enter")
        except Exception:
            pass
        rows = _wait_for_scoped_vertical_search_candidates(
            page,
            search,
            timeout_ms=max(2200, wait_ms * 3),
        )

    _vertical_diag(
        "query_generation",
        {
            "generation": generation,
            "query": term,
            "fresh_row_count": len(rows),
            "sample": rows[:8],
        },
    )
    return rows


def _try_select_via_search(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int,
) -> tuple[str, list[str], tuple[str, ...]]:
    """Evaluate the search ladder in order and click within the winning generation."""

    search = _vertical_search_input(page)
    planned_terms = plan_vertical_search_terms(provider, hints)
    observed: list[str] = []
    observed_keys: set[str] = set()

    for query_index, term in enumerate(planned_terms, start=1):
        rows = _run_vertical_search_query(page, search, term, wait_ms=wait_ms)
        for row in rows:
            key = normalize_label(row)
            if not key or key in observed_keys:
                continue
            observed_keys.add(key)
            observed.append(row)

        selected = _choose_vertical_search_candidate(provider, hints, term, rows)
        _vertical_diag(
            "query_decision",
            {
                "query_index": query_index,
                "query_count": len(planned_terms),
                "query": term,
                "fresh_row_count": len(rows),
                "selected_vertical": selected,
                "action": "click_current_generation" if selected else "continue_search_ladder",
                "sample": rows[:8],
            },
        )
        if not selected:
            continue

        previous_canonical, _ = _current_target_values(page)
        clicked = click_search_row(search, selected, allow_stable_exact=False)
        _vertical_diag(
            "selected_current_generation",
            {
                "query_index": query_index,
                "query": term,
                "selected_vertical": selected,
                "click_bound": bool(clicked),
                "replay_performed": False,
            },
        )
        if not clicked:
            raise RuntimeError(
                "Makro Step 1 selected a grounded query-owned live Vertical from the current search generation "
                "but could not bind that exact current row for clicking; no replay was attempted because the "
                f"selection must remain generation-local: selected={selected!r}; query={term!r}"
            )

        return (
            _complete_exact_live_vertical(
                page,
                selected,
                previous_canonical=previous_canonical,
                verification_label=_search_result_leaf(selected),
            ),
            observed,
            planned_terms,
        )

    _close_vertical_search(search, page, wait_ms=wait_ms)
    return "", observed, planned_terms


def _select_via_search_with_context(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int,
    reason: str,
) -> str:
    selected, observed, attempted_terms = _try_select_via_search(
        page,
        provider,
        hints,
        wait_ms=wait_ms,
    )
    if selected:
        return selected
    attempted = " | ".join(attempted_terms)
    rows = " | ".join(observed[:20]) if observed else "<none>"
    raise RuntimeError(
        f"Makro Step 1 {reason}; ordered exact-live Vertical Search found no verified result from: "
        f"{attempted}; observed query-owned rows: {rows}"
    )


def _taxonomy_navigation_callbacks(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
):
    selected_paths: dict[str, list[str]] = {}

    def choose(path: list[str], candidates: list[str]) -> str:
        selected = choose_taxonomy_path_candidate(provider, hints, path, candidates)
        if selected:
            selected_paths[normalize_label(selected)] = [*path, selected]
        return selected

    def complete(node: str) -> str:
        breadcrumb = selected_paths.get(normalize_label(node), [node])
        if not validate_taxonomy_leaf_candidate(provider, hints, breadcrumb):
            _vertical_diag(
                "taxonomy_leaf_rejected",
                {
                    "breadcrumb": breadcrumb,
                    "leaf": node,
                    "action": "backtrack_current_branch",
                },
            )
            return ""
        return _complete_exact_live_vertical(page, node)

    return choose, complete


def _resume_partial_taxonomy(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    taxonomy: ResilientMakroTaxonomyBrowser,
    initial_columns: list[list[str]],
    *,
    wait_ms: int,
) -> str:
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

        choose_node, complete_leaf = _taxonomy_navigation_callbacks(page, provider, hints)
        selected = navigate_live_taxonomy(
            page,
            columns_fn=shifted_columns,
            click_fn=shifted_click,
            choose_fn=choose_node,
            leaf_ready_fn=lambda selected_node: _vertical_confirmation_ready(page, selected_node),
            complete_leaf_fn=complete_leaf,
            wait_ms=wait_ms,
            max_depth=max(1, 7 - start_level),
            max_node_attempts=12,
            max_backtracks=5,
            transition_polls=18,
        )
        if selected:
            return selected
    return ""


def _select_via_taxonomy(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    taxonomy: ResilientMakroTaxonomyBrowser,
    *,
    wait_ms: int,
) -> str:
    columns = taxonomy.columns()
    if len(columns) > 1:
        return _resume_partial_taxonomy(page, provider, hints, taxonomy, columns, wait_ms=wait_ms)
    if not columns:
        return ""
    choose_node, complete_leaf = _taxonomy_navigation_callbacks(page, provider, hints)
    return navigate_live_taxonomy(
        page,
        columns_fn=taxonomy.columns,
        click_fn=taxonomy.click_node,
        choose_fn=choose_node,
        leaf_ready_fn=lambda selected_node: _vertical_confirmation_ready(page, selected_node),
        complete_leaf_fn=complete_leaf,
        wait_ms=wait_ms,
        max_depth=7,
        max_node_attempts=16,
        max_backtracks=6,
        transition_polls=18,
    )


def is_vertical_interaction_ready(page: Page) -> bool:
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
    try:
        later = is_product_info_step(page) or is_brand_step(page)
    except Exception:
        later = False
    if not later:
        return ""
    try:
        canonical, _ = _current_target_values(page)
    except Exception as exc:
        raise RuntimeError("Makro page is already Step 2/3 but its committed canonical vertical cannot be read") from exc
    value = str(canonical or "").strip()
    if not value:
        raise RuntimeError("Makro page is already Step 2/3 but its listing URL has no committed canonical vertical")
    return value


def select_vertical(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int = 800,
) -> str:
    """Select one verified live Makro Vertical through the shared resolver."""

    committed = _committed_vertical_from_later_stage(page)
    if committed:
        return committed
    if not is_vertical_interaction_ready(page):
        raise RuntimeError("Makro Step 1 / Select Vertical is not safely operable")

    search_selected, observed, attempted_terms = _try_select_via_search(
        page,
        provider,
        hints,
        wait_ms=wait_ms,
    )
    if search_selected:
        return search_selected

    # Taxonomy is a semantic fallback only. If a current live search generation
    # produced and selected a candidate but its row could not be clicked,
    # _try_select_via_search already failed closed above.
    taxonomy = ResilientMakroTaxonomyBrowser(page)
    taxonomy_selected = _select_via_taxonomy(page, provider, hints, taxonomy, wait_ms=wait_ms)
    if taxonomy_selected:
        return taxonomy_selected

    attempted = " | ".join(attempted_terms)
    rows = " | ".join(observed[:20]) if observed else "<none>"
    raise RuntimeError(
        "Makro Step 1 could not resolve a verified Vertical through ordered query-owned live search "
        f"or bounded live taxonomy; search_terms={attempted}; observed query-owned rows={rows}"
    )


__all__ = ["is_vertical_interaction_ready", "select_vertical"]
