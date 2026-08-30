"""Makro Step 1 Vertical resolution.

One production decision boundary owns Step 1: Makro supplies the selectable
Verticals, AI plans a bounded retrieval ladder, and AI decides directly from the
rows that are live in each current search generation. When AI chooses one current
live row, that exact row is clicked immediately; the workflow never re-runs an old
query to re-judge or re-bind an already chosen category. Query-local DOM failures
stay inside that retrieval attempt, and the existing taxonomy fallback remains for
runs where AI selects none from every live search generation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

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
    is_brand_step,
    is_product_info_step,
    is_vertical_step,
    normalize_label,
)
from .portal_interruptions import reconcile_portal_interruptions
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
from .vertical_resolution import (
    choose_vertical_candidate_pool,
    merge_vertical_search_observations,
    plan_vertical_search_terms,
)


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


@dataclass(frozen=True, slots=True)
class _VerticalBrandTransitionObservation:
    brand_step: bool = False
    confirmation_visible: bool = False
    selected_visible: bool = False
    canonical: str = ""
    select_brand_action: object | None = None

    @property
    def actionable(self) -> bool:
        return bool(self.brand_step or self.select_brand_action is not None)


def _observe_vertical_brand_transition(
    page: Page,
    selected: str = "",
) -> _VerticalBrandTransitionObservation:
    selected_value = str(selected or "").strip()

    try:
        brand_step = bool(is_brand_step(page))
    except Exception:
        brand_step = False
    try:
        canonical, _ = _current_target_values(page)
    except Exception:
        canonical = ""
    canonical_value = str(canonical or "").strip()

    if brand_step:
        return _VerticalBrandTransitionObservation(
            brand_step=True,
            canonical=canonical_value,
        )

    try:
        selected_visible = not selected_value or _selected_label_visible(page, selected_value)
    except Exception:
        selected_visible = False
    try:
        action = _vertical_select_brand_button(page)
    except Exception:
        action = None
    try:
        semantic_confirmation = bool(_vertical_confirmation_content(page))
    except Exception:
        semantic_confirmation = False
    try:
        vertical_step = bool(is_vertical_step(page))
    except Exception:
        vertical_step = False

    structural_confirmation = bool(
        vertical_step
        and selected_visible
        and action is not None
    )
    confirmation_visible = bool(semantic_confirmation or structural_confirmation)

    if not confirmation_visible:
        return _VerticalBrandTransitionObservation(
            selected_visible=selected_visible,
            canonical=canonical_value,
        )
    if not selected_visible:
        return _VerticalBrandTransitionObservation(
            confirmation_visible=True,
            canonical=canonical_value,
        )

    return _VerticalBrandTransitionObservation(
        confirmation_visible=True,
        selected_visible=True,
        canonical=canonical_value,
        select_brand_action=action,
    )


def _vertical_confirmation_ready(page: Page, selected: str = "") -> bool:
    return _observe_vertical_brand_transition(page, selected).actionable


def _wait_for_brand_step_after_select(page: Page) -> bool:
    """Reconcile only safe presentation interruptions while Step 2 settles."""

    def ready(current: Page) -> bool:
        if is_brand_step(current):
            return True
        handled = reconcile_portal_interruptions(current)
        if handled:
            _vertical_diag(
                "select_brand_interruption_reconciled",
                {"handled": handled, "page_url": str(current.url or "")},
            )
        return bool(is_brand_step(current))

    return _wait_for(ready, page, timeout_s=20.0)


def _complete_exact_live_vertical(
    page: Page,
    selected: str,
    *,
    previous_canonical: str = "",
    verification_label: str = "",
) -> str:
    verify_as = str(verification_label or selected).strip()
    observation = _VerticalBrandTransitionObservation()

    def observe(current: Page) -> bool:
        nonlocal observation
        observation = _observe_vertical_brand_transition(current, verify_as)
        return observation.actionable

    transitioned = _wait_for(observe, page, timeout_s=15.0)
    if not transitioned:
        if observation.confirmation_visible:
            raise RuntimeError(
                "Makro Step 1 selected the expected live Vertical and rendered its confirmation, "
                "but that confirmation never became atomically actionable with the exact Select Brand action"
            )
        raise RuntimeError(
            f"Makro Step 1 selected live vertical {selected!r}, but no verified Step 1 confirmation or Step 2 appeared"
        )

    selected_visible = bool(observation.selected_visible)
    if observation.brand_step:
        canonical_after = observation.canonical or _wait_for_canonical_vertical(page)
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

    canonical_before_brand = observation.canonical
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

    button = observation.select_brand_action
    if button is None:
        raise RuntimeError("Makro Step 1 transition observation became actionable without a Select Brand action")

    _vertical_diag(
        "select_brand_transition",
        {
            "selected_leaf": verify_as,
            "canonical_before_brand": canonical_before_brand,
            "selected_visible_confirmation": selected_visible,
            "action": "click_observed_action_then_reconcile_step2",
        },
    )
    button.click(timeout=5000)
    if not _wait_for_brand_step_after_select(page):
        diagnostics = {
            "url": str(page.url or ""),
            "brand_step": bool(is_brand_step(page)),
            "vertical_step": bool(is_vertical_step(page)),
            "body_prefix": str(_body_text(page) or "")[:700],
        }
        raise RuntimeError(
            "Makro Step 1 triggered the verified Select Brand action, but no verified Step 2 state "
            f"appeared after safe interruption reconciliation; diagnostics={json.dumps(diagnostics, ensure_ascii=False)}"
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


def _close_vertical_search(search, page: Page, *, wait_ms: int) -> None:
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
    term: str,
    *,
    wait_ms: int,
):
    """Run one query with a freshly reacquired search owner."""

    search = _vertical_search_input(page)
    _close_vertical_search(search, page, wait_ms=wait_ms)
    search = _vertical_search_input(page)
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
    return rows, search


def _search_attempt_is_locally_recoverable(page: Page) -> bool:
    try:
        if page.is_closed():
            return False
    except Exception:
        return False
    try:
        if is_product_info_step(page) or is_brand_step(page):
            return False
    except Exception:
        return False
    return True


def _try_select_via_search(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int,
) -> tuple[str, list[str], tuple[str, ...]]:
    """Let AI decide from each current live generation and click immediately."""

    planned_terms = plan_vertical_search_terms(provider, hints)
    observed: list[str] = []
    observed_keys: set[str] = set()
    last_search = None

    for query_index, term in enumerate(planned_terms, start=1):
        try:
            rows, search = _run_vertical_search_query(page, term, wait_ms=wait_ms)
        except Exception as exc:
            if not _search_attempt_is_locally_recoverable(page):
                raise
            _vertical_diag(
                "query_failed",
                {
                    "query_index": query_index,
                    "query_count": len(planned_terms),
                    "query": term,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "action": "continue_next_query",
                },
            )
            continue

        last_search = search
        for row in rows:
            key = normalize_label(row)
            if not key or key in observed_keys:
                continue
            observed_keys.add(key)
            observed.append(row)

        current_candidates = merge_vertical_search_observations(((term, rows),))
        selected = choose_vertical_candidate_pool(
            provider,
            hints,
            (term,),
            current_candidates,
        )
        _vertical_diag(
            "query_decision",
            {
                "query_index": query_index,
                "query_count": len(planned_terms),
                "query": term,
                "fresh_row_count": len(rows),
                "candidate_count": len(current_candidates),
                "selected_vertical": selected,
                "action": "click_current_generation" if selected else "continue_next_query",
                "sample": rows[:8],
            },
        )
        if not selected:
            continue

        selected_key = normalize_label(selected)
        exact = [row for row in rows if normalize_label(row) == selected_key]
        if len(exact) != 1:
            raise RuntimeError(
                "Makro Step 1 AI selected a Vertical that is not one unique row in the current live "
                f"search generation; selected={selected!r}; query={term!r}; exact_match_count={len(exact)}"
            )

        current_row = exact[0]
        previous_canonical, _ = _current_target_values(page)
        if not click_search_row(search, current_row, allow_stable_exact=False):
            raise RuntimeError(
                "Makro Step 1 AI selected one current live Vertical, but that exact current row could "
                f"not be clicked; selected={current_row!r}; query={term!r}"
            )
        return (
            _complete_exact_live_vertical(
                page,
                current_row,
                previous_canonical=previous_canonical,
                verification_label=_search_result_leaf(current_row),
            ),
            observed,
            planned_terms,
        )

    if last_search is not None:
        try:
            _close_vertical_search(last_search, page, wait_ms=wait_ms)
        except Exception:
            pass
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
        f"Makro Step 1 {reason}; AI selected no Vertical from the current live search generations: "
        f"{attempted}; observed live rows: {rows}"
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

    taxonomy = ResilientMakroTaxonomyBrowser(page)
    taxonomy_selected = _select_via_taxonomy(page, provider, hints, taxonomy, wait_ms=wait_ms)
    if taxonomy_selected:
        return taxonomy_selected

    attempted = " | ".join(attempted_terms)
    rows = " | ".join(observed[:20]) if observed else "<none>"
    raise RuntimeError(
        "Makro Step 1 could not resolve a verified Vertical through direct current-generation AI selection "
        f"or bounded live taxonomy; search_terms={attempted}; observed live rows={rows}"
    )


__all__ = ["is_vertical_interaction_ready", "select_vertical"]
