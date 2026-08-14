"""Makro Step 2 brand selection.

Supplier evidence establishes brand identity; Makro establishes what is
selectable. Brand discovery and selection therefore use the same query-owned
surface contract as Step 1 Vertical Search. Page-wide text is never treated as
a brand candidate.

The supplier brand may be used as a bounded discovery query, but the selected
value must always be an exact live Makro result and is verified again by the
existing Step 2 transition mechanics.
"""

from __future__ import annotations

from typing import Any, Protocol

from playwright.sync_api import Page

from .listing_creation import (
    _advance_brand_confirmation,
    _brand_input,
    _brand_search_terms,
    _click_check_brand,
    _current_target_values,
    _verify_selected_value,
    is_brand_step,
    is_product_info_step,
    normalize_label,
)
from .portal_interruptions import reconcile_portal_interruptions
from .search_surface import begin_search_query, click_search_row, read_search_rows


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


class BrandHints(Protocol):
    brand: str
    brand_status: str
    product_summary: str
    product_identity: dict[str, Any] | None


def _build_live_brand_choice_request(
    hints: BrandHints,
    discovery_query: str,
    candidates: list[str],
) -> dict[str, Any]:
    allowed = list(dict.fromkeys(candidates))
    return {
        "task": "choose_exact_live_makro_brand",
        "system_instruction": (
            "Choose a brand only from exact live Makro candidates. Supplier evidence "
            "describes product identity; Makro candidates define what is selectable. JSON only."
        ),
        "prompt_instruction": (
            "Compare supplier brand evidence with live_candidates. Return one exact candidate "
            "only when it represents the same brand identity, or the correct no-brand/generic "
            "sentinel for an explicitly unbranded product. discovery_query is only how the "
            "portal exposed the candidates."
        ),
        "context": {
            "supplier_brand_status": str(hints.brand_status or "").strip(),
            "supplier_brand": str(hints.brand or "").strip(),
            "product_summary": str(hints.product_summary or "").strip(),
            "product_identity": dict(hints.product_identity or {}),
            "discovery_query": str(discovery_query or "").strip(),
            "live_candidates": allowed,
        },
        "rules": [
            "selected_brand must be copied exactly from live_candidates or be empty.",
            "For explicit supplier brand, choose only the same brand identity.",
            "For explicitly unbranded status, choose only a clear no-brand/unbranded/generic sentinel.",
            "For unknown supplier brand status, return an empty string.",
            "Never substitute a different commercial brand.",
            "If live candidates do not safely represent supplier evidence, return an empty string.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selected_brand": {"type": "string", "enum": ["", *allowed]},
            },
            "required": ["selected_brand"],
        },
        "strict_json_schema": True,
    }


def choose_live_brand_candidate(
    provider: JSONTaskProvider,
    hints: BrandHints,
    discovery_query: str,
    candidates: list[str],
) -> str:
    if not candidates:
        return ""

    status = str(hints.brand_status or "").strip().casefold()
    if status == "unknown":
        return ""

    if status == "explicit":
        wanted = normalize_label(hints.brand)
        exact = [item for item in candidates if normalize_label(item) == wanted]
        if len(exact) == 1:
            return exact[0]

    raw = provider.extract_json(_build_live_brand_choice_request(hints, discovery_query, candidates))
    if not isinstance(raw, dict):
        raise ValueError("live brand chooser response must be a JSON object")
    selected = str(raw.get("selected_brand") or "").strip()
    if not selected:
        return ""
    matches = [item for item in candidates if normalize_label(item) == normalize_label(selected)]
    if len(matches) != 1:
        raise ValueError(
            f"AI returned a brand that is not one unique live Makro candidate: {selected!r}"
        )
    return matches[0]


def _live_brand_candidates(page: Page, *, limit: int = 160) -> list[str]:
    """Compatibility helper: read only the active input-owned result surface."""

    try:
        rows = read_search_rows(_brand_input(page))
    except Exception:
        return []
    return rows[: max(0, int(limit))]


def _verify_direct_transition_brand(
    provider: JSONTaskProvider,
    hints: BrandHints,
    *,
    discovery_query: str,
    actual_brand: str,
) -> str:
    if not actual_brand:
        raise RuntimeError(
            "Makro Step 2 advanced without exposing a verifiable brand in the listing URL"
        )
    selected = choose_live_brand_candidate(provider, hints, discovery_query, [actual_brand])
    if not selected:
        raise RuntimeError(
            "Makro Step 2 advanced to a brand incompatible with supplier evidence: "
            f"actual_brand={actual_brand!r}, supplier_brand={str(hints.brand or '').strip()!r}, "
            f"brand_status={str(hints.brand_status or '').strip()!r}"
        )
    return actual_brand


def _wait_for_brand_query_outcome(
    page: Page,
    brand_input,
    *,
    timeout_ms: int,
    poll_ms: int = 200,
) -> tuple[str, list[str]]:
    attempts = max(1, max(0, int(timeout_ms)) // max(50, int(poll_ms)))
    for _ in range(attempts):
        if is_product_info_step(page):
            return "product_info", []
        rows = read_search_rows(brand_input)
        if rows:
            return "rows", rows
        page.wait_for_timeout(max(50, int(poll_ms)))
    if is_product_info_step(page):
        return "product_info", []
    rows = read_search_rows(brand_input)
    return ("rows", rows) if rows else ("none", [])


def _select_query_owned_brand(
    page: Page,
    provider: JSONTaskProvider,
    hints: BrandHints,
    brand_input,
    *,
    discovery_query: str,
    candidates: list[str],
) -> str:
    selected = choose_live_brand_candidate(provider, hints, discovery_query, candidates)
    if not selected:
        return ""

    reconcile_portal_interruptions(page)
    if not click_search_row(brand_input, selected):
        raise RuntimeError(
            "Makro Step 2 found an exact query-owned brand but could not click it: "
            f"{selected!r}"
        )
    _advance_brand_confirmation(page, selected)
    _, actual_brand = _current_target_values(page)
    return _verify_selected_value("Step 2 URL", selected, actual_brand)


def _close_brand_search(page: Page, brand_input, *, wait_ms: int) -> None:
    try:
        brand_input.fill("")
    except Exception:
        return
    try:
        brand_input.press("Escape")
    except Exception:
        pass
    if wait_ms > 0:
        page.wait_for_timeout(min(max(wait_ms // 3, 80), 250))


def select_brand(
    page: Page,
    provider: JSONTaskProvider,
    hints: BrandHints,
    *,
    wait_ms: int = 900,
) -> str:
    """Select one verified Makro brand from query-owned live results only."""

    if not is_brand_step(page):
        raise RuntimeError("Makro is not on Step 2 / Select Brand")

    status = str(hints.brand_status or "").strip().casefold()
    terms = _brand_search_terms(hints)
    if status == "unknown" or not terms:
        raise RuntimeError(
            "Supplier evidence did not establish a brand or explicit unbranded status; "
            "refusing to choose a Makro brand"
        )

    reconcile_portal_interruptions(page)
    brand_input = _brand_input(page)

    _close_brand_search(page, brand_input, wait_ms=wait_ms)
    begin_search_query(brand_input)
    current_rows = read_search_rows(brand_input)
    if current_rows:
        selected = _select_query_owned_brand(
            page,
            provider,
            hints,
            brand_input,
            discovery_query="",
            candidates=current_rows,
        )
        if selected:
            return selected

    attempted: list[str] = []
    observed: list[str] = []
    for term in terms:
        attempted.append(term)
        reconcile_portal_interruptions(page)
        _close_brand_search(page, brand_input, wait_ms=wait_ms)

        begin_search_query(brand_input)
        brand_input.fill(term)
        _click_check_brand(page)

        outcome, rows = _wait_for_brand_query_outcome(
            page,
            brand_input,
            timeout_ms=max(3600, wait_ms * 5),
        )
        if outcome == "product_info":
            _, actual_brand = _current_target_values(page)
            return _verify_direct_transition_brand(
                provider,
                hints,
                discovery_query=term,
                actual_brand=actual_brand,
            )

        for row in rows:
            if row not in observed:
                observed.append(row)
        if not rows:
            continue

        selected = _select_query_owned_brand(
            page,
            provider,
            hints,
            brand_input,
            discovery_query=term,
            candidates=rows,
        )
        if selected:
            return selected

    raise RuntimeError(
        "Makro Step 2 could not match supplier brand evidence to a verified query-owned Makro brand. "
        f"brand_status={status!r}, supplier_brand={str(hints.brand or '').strip()!r}, "
        f"queries={attempted!r}, observed_query_rows={observed[:12]!r}"
    )


__all__ = ["choose_live_brand_candidate", "select_brand"]
