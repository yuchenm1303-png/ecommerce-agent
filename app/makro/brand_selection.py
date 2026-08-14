"""Portal-first Makro Step 2 brand selection.

Supplier evidence describes the product's brand identity; Makro decides which
brands are actually selectable.  The production workflow therefore consumes
live Makro candidates first and only uses bounded search queries when the Step 2
UI does not materialize a suitable candidate set on entry.

AI is never allowed to invent a marketplace brand: every selected value must be
copied from the currently rendered Makro candidate set and is verified again by
the existing Step 2 transition mechanics.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from playwright.sync_api import Page

from .listing_creation import (
    _advance_brand_confirmation,
    _brand_input,
    _brand_search_terms,
    _click_check_brand,
    _click_exact_visible_text,
    _current_target_values,
    _verify_selected_value,
    _visible_text_candidates,
    is_brand_step,
    is_product_info_step,
    normalize_label,
)
from .portal_interruptions import reconcile_portal_interruptions


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


class BrandHints(Protocol):
    brand: str
    brand_status: str
    product_summary: str
    product_identity: dict[str, Any] | None


_BRAND_CANDIDATE_EXCLUDED = {
    normalize_label(value)
    for value in (
        "brand",
        "select brand",
        "check brand",
        "brand check",
        "enter brand name",
        "selected brand",
        "brand details",
        "confirm brand",
        "use brand",
        "create new listing",
        "create listing",
        "search",
        "no results",
        "no result",
        "选择品牌",
        "检查品牌",
        "输入品牌名称",
        "已选择品牌",
        "品牌详情",
        "确认品牌",
        "使用品牌",
        "创建新商品",
        "搜索",
        "暂无结果",
        "无结果",
        "or",
        "或",
    )
}


def _structured_brand_candidate_texts(page: Page, *, limit: int = 160) -> list[str]:
    """Read visible result/option/card text without interpreting brand semantics.

    Makro has used several result shapes over time (rows, list items, option-like
    divs and brand/result cards).  This extractor is deliberately mechanical: it
    only returns visible rendered labels/attributes from those structures.  The
    semantic decision remains in ``choose_live_brand_candidate``.
    """

    try:
        raw = page.evaluate(
            r"""(limit) => {
              const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
              const visible = (el) => {
                const s = getComputedStyle(el), r = el.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden'
                  && Number(s.opacity || 1) !== 0 && r.width > 2 && r.height > 2;
              };
              const out = [];
              const seen = new Set();
              const push = (value) => {
                const text = clean(value);
                if (!text || text.length < 2 || text.length > 90) return;
                const key = text.toLocaleLowerCase();
                if (seen.has(key)) return;
                seen.add(key);
                out.push(text);
              };
              const selector = [
                '[role="option"]', '[role="listitem"]', '[role="row"]',
                'li', 'tr', '[data-brand]', '[data-value]',
                '[class*="brand" i]', '[class*="result" i]',
                '[class*="option" i]', '[class*="suggest" i]'
              ].join(',');
              for (const el of document.querySelectorAll(selector)) {
                if (out.length >= limit || !visible(el)) continue;
                if (el.matches('input,textarea,select') || el.querySelector('input[type="password"]')) continue;
                for (const attr of ['data-brand', 'data-value', 'aria-label', 'title']) {
                  const value = el.getAttribute && el.getAttribute(attr);
                  if (value) push(value);
                }
                const rendered = String(el.innerText || el.textContent || '');
                for (const line of rendered.split(/\n+/)) push(line);
                if (el.children && el.children.length) {
                  for (const child of el.children) {
                    if (visible(child)) push(child.innerText || child.textContent || '');
                  }
                }
              }
              return out.slice(0, limit);
            }""",
            int(limit),
        )
    except Exception:
        return []
    return [
        re.sub(r"\s+", " ", str(item or "")).strip()
        for item in raw or []
        if str(item or "").strip()
    ]


def _live_brand_candidates(page: Page, *, limit: int = 160) -> list[str]:
    """Return a deduplicated snapshot of brands/results currently exposed by Makro."""

    raw = [
        *_structured_brand_candidate_texts(page, limit=limit),
        *_visible_text_candidates(page, limit=limit),
    ]
    output: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = re.sub(r"\s+", " ", str(item or "")).strip()
        key = normalize_label(value)
        if not key or key in _BRAND_CANDIDATE_EXCLUDED or key in seen:
            continue
        seen.add(key)
        output.append(value)
        if len(output) >= limit:
            break
    return output


def _build_live_brand_choice_request(
    hints: BrandHints,
    discovery_query: str,
    candidates: list[str],
) -> dict[str, Any]:
    allowed = list(dict.fromkeys(candidates))
    return {
        "task": "choose_exact_live_makro_brand",
        "system_instruction": (
            "Choose a brand only from exact live Makro candidates. Supplier evidence describes the "
            "product identity; Makro candidates define what is selectable. JSON only."
        ),
        "prompt_instruction": (
            "Compare context.supplier_brand_status / supplier_brand / product_identity with "
            "context.live_candidates. Return one exact candidate only when it represents the same "
            "brand identity, or the correct no-brand/generic sentinel for an explicitly unbranded "
            "product. The discovery query is only how the portal exposed these candidates; it is not "
            "permission to invent or prefer a value."
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
            "For explicit supplier brand, choose only the same brand identity; harmless case, punctuation, localization, transliteration or registered display styling may differ.",
            "For explicitly unbranded supplier status, choose only a candidate that clearly denotes no-brand, unbranded or generic status.",
            "For unknown supplier brand status, return an empty string.",
            "Never substitute a different commercial brand and never choose a recent/favorite brand merely because it is visible.",
            "If the live candidates do not safely represent the supplier brand status, return an empty string.",
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

    raw = provider.extract_json(
        _build_live_brand_choice_request(hints, discovery_query, candidates)
    )
    if not isinstance(raw, dict):
        raise ValueError("live brand chooser response must be a JSON object")
    selected = str(raw.get("selected_brand") or "").strip()
    if not selected:
        return ""
    matches = [
        item
        for item in candidates
        if normalize_label(item) == normalize_label(selected)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"AI returned a brand that is not one unique live Makro candidate: {selected!r}"
        )
    return matches[0]


def _select_from_current_live_candidates(
    page: Page,
    provider: JSONTaskProvider,
    hints: BrandHints,
    *,
    discovery_query: str,
) -> tuple[str, list[str]]:
    candidates = _live_brand_candidates(page)
    selected = choose_live_brand_candidate(
        provider,
        hints,
        discovery_query,
        candidates,
    )
    if not selected:
        return "", candidates

    reconcile_portal_interruptions(page)
    if not _click_exact_visible_text(page, selected):
        raise RuntimeError(
            f"Makro Step 2 could not click selected live brand: {selected!r}"
        )
    _advance_brand_confirmation(page, selected)
    _, actual_brand = _current_target_values(page)
    return _verify_selected_value("Step 2 URL", selected, actual_brand), candidates


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
    selected = choose_live_brand_candidate(
        provider,
        hints,
        discovery_query,
        [actual_brand],
    )
    if not selected:
        raise RuntimeError(
            "Makro Step 2 advanced to a brand that is not compatible with supplier evidence: "
            f"actual_brand={actual_brand!r}, supplier_brand={str(hints.brand or '').strip()!r}, "
            f"brand_status={str(hints.brand_status or '').strip()!r}"
        )
    return actual_brand


def select_brand(
    page: Page,
    provider: JSONTaskProvider,
    hints: BrandHints,
    *,
    wait_ms: int = 900,
) -> str:
    """Select Step 2 brand with a portal-first, live-candidate-only contract.

    1. Inspect candidates already exposed by the current Makro Step 2 page.
    2. If none safely match, use the supplier brand only as a bounded discovery
       query (or Makro's known no-brand sentinel queries).
    3. AI may choose only an exact candidate returned by the live portal.

    This keeps supplier semantics and marketplace availability separate while
    preserving the existing deterministic click/confirmation/URL verification.
    """

    if not is_brand_step(page):
        raise RuntimeError("Makro is not on Step 2 / Select Brand")

    reconcile_portal_interruptions(page)
    brand_input = _brand_input(page)
    brand_input.fill("")
    page.wait_for_timeout(max(0, int(wait_ms)))

    selected, last_candidates = _select_from_current_live_candidates(
        page,
        provider,
        hints,
        discovery_query="",
    )
    if selected:
        return selected

    terms = _brand_search_terms(hints)
    if not terms:
        raise RuntimeError(
            "Supplier evidence did not establish a brand or explicit unbranded status; "
            "refusing to choose a Makro brand from unrelated live candidates"
        )

    attempted: list[str] = []
    for term in terms:
        attempted.append(term)
        reconcile_portal_interruptions(page)
        brand_input.fill("")
        brand_input.fill(term)
        _click_check_brand(page)
        page.wait_for_timeout(max(0, int(wait_ms)))

        if is_product_info_step(page):
            _, actual_brand = _current_target_values(page)
            return _verify_direct_transition_brand(
                provider,
                hints,
                discovery_query=term,
                actual_brand=actual_brand,
            )

        selected, last_candidates = _select_from_current_live_candidates(
            page,
            provider,
            hints,
            discovery_query=term,
        )
        if selected:
            return selected

    sample = last_candidates[:12]
    raise RuntimeError(
        "Makro Step 2 could not match supplier brand evidence to a verified live Makro brand. "
        f"brand_status={str(hints.brand_status or '').strip()!r}, "
        f"supplier_brand={str(hints.brand or '').strip()!r}, "
        f"queries={attempted!r}, live_candidates_sample={sample!r}"
    )


__all__ = [
    "choose_live_brand_candidate",
    "select_brand",
]
