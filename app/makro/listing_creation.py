"""Makro Step 1/2 creation for the one-link listing workflow.

This module owns only the pre-Step-3 browser work:
- derive conservative category-search hints and brand status from the exact
  supplier snapshot;
- search the live Makro vertical UI and choose only an option that actually
  exists on the current page;
- check/select the brand and verify that Makro reached Step 3.

It never writes Step 3 attributes and never clicks Send to QC.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from playwright.sync_api import Page

from ..source_snapshot import SourceSnapshot
from .listing import MAKRO_HOME_URL, MAKRO_SINGLE_LISTING_ROUTE, parse_makro_listing_url


MAKRO_NEW_LISTING_URL = f"{MAKRO_HOME_URL}#{MAKRO_SINGLE_LISTING_ROUTE}"


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(slots=True, frozen=True)
class ListingBootstrapHints:
    vertical_search_terms: tuple[str, ...]
    brand: str
    brand_status: str
    product_summary: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "vertical_search_terms": list(self.vertical_search_terms),
            "brand": self.brand,
            "brand_status": self.brand_status,
            "product_summary": self.product_summary,
        }


@dataclass(slots=True, frozen=True)
class ListingCreationResult:
    vertical: str
    brand: str
    page_url: str
    hints: ListingBootstrapHints

    def as_dict(self) -> dict[str, Any]:
        return {
            "vertical": self.vertical,
            "brand": self.brand,
            "page_url": self.page_url,
            "hints": self.hints.as_dict(),
        }


def normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _bounded_supplier_evidence(snapshot: SourceSnapshot) -> dict[str, Any]:
    rows = [
        {"key": row.key, "value": row.value}
        for row in snapshot.table_rows[:100]
        if row.key or row.value
    ]
    embedded: list[str] = []
    used = 0
    for item in snapshot.embedded_data:
        text = str(item or "").strip()
        if not text:
            continue
        remaining = 3000 - used
        if remaining <= 0:
            break
        embedded.append(text[:remaining])
        used += len(embedded[-1])
    return {
        "product_url": snapshot.final_url or snapshot.requested_url,
        "page_title": snapshot.title,
        "table_rows": rows,
        "visible_text": snapshot.visible_text[:9000],
        "embedded_product_data": embedded,
    }


def build_bootstrap_request(snapshot: SourceSnapshot) -> dict[str, Any]:
    return {
        "task": "infer_makro_listing_bootstrap_hints",
        "system_instruction": (
            "Infer only conservative search hints for Makro Step 1 and the product brand status "
            "from exact supplier evidence. JSON only."
        ),
        "supplier_evidence": _bounded_supplier_evidence(snapshot),
        "rules": [
            "Return 1 to 4 concise English product-category search terms, most specific first.",
            "Search terms are hints only; do not invent an exact Makro vertical name.",
            "Treat model numbers, variant names and descriptive words as non-brand unless the source explicitly identifies them as a brand.",
            "brand_status=explicit only when the supplier evidence explicitly identifies a brand.",
            "brand_status=unbranded only when the supplier evidence explicitly indicates neutral/no-brand/OEM/unbranded status.",
            "Otherwise brand_status=unknown and brand must be empty.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "vertical_search_terms": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {"type": "string", "minLength": 2},
                },
                "brand": {"type": "string"},
                "brand_status": {
                    "type": "string",
                    "enum": ["explicit", "unbranded", "unknown"],
                },
                "product_summary": {"type": "string"},
            },
            "required": [
                "vertical_search_terms",
                "brand",
                "brand_status",
                "product_summary",
            ],
        },
        "strict_json_schema": True,
    }


def _parse_bootstrap_response(raw: Any) -> ListingBootstrapHints:
    if not isinstance(raw, dict):
        raise ValueError("listing bootstrap response must be a JSON object")
    terms: list[str] = []
    seen: set[str] = set()
    for item in raw.get("vertical_search_terms") or []:
        value = re.sub(r"\s+", " ", str(item or "")).strip()
        key = normalize_label(value)
        if len(value) < 2 or not key or key in seen:
            continue
        seen.add(key)
        terms.append(value)
        if len(terms) >= 4:
            break
    if not terms:
        raise ValueError("listing bootstrap produced no usable vertical search terms")

    status = str(raw.get("brand_status") or "").strip().casefold()
    if status not in {"explicit", "unbranded", "unknown"}:
        raise ValueError(f"invalid brand_status={status!r}")
    brand = re.sub(r"\s+", " ", str(raw.get("brand") or "")).strip()
    if status == "explicit" and not brand:
        raise ValueError("explicit brand_status requires a brand")
    if status != "explicit":
        brand = ""
    return ListingBootstrapHints(
        vertical_search_terms=tuple(terms),
        brand=brand,
        brand_status=status,
        product_summary=re.sub(r"\s+", " ", str(raw.get("product_summary") or "")).strip(),
    )


def infer_listing_bootstrap(provider: JSONTaskProvider, snapshot: SourceSnapshot) -> ListingBootstrapHints:
    return _parse_bootstrap_response(provider.extract_json(build_bootstrap_request(snapshot)))


def _body_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def is_vertical_step(page: Page) -> bool:
    text = _body_text(page).casefold()
    return "select the vertical for your product" in text and "browse verticals" in text


def is_brand_step(page: Page) -> bool:
    text = _body_text(page).casefold()
    return "check for the brand you want to sell" in text or "enter brand name" in text


def is_product_info_step(page: Page) -> bool:
    text = _body_text(page).casefold()
    return (
        "add product info" in text
        and "product photos" in text
        and "price, stock and shipping information" in text
    )


def _wait_for(predicate, page: Page, *, timeout_s: float = 15.0, poll_s: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate(page):
            return True
        page.wait_for_timeout(int(poll_s * 1000))
    return False


def _first_visible(locator):
    try:
        count = locator.count()
    except Exception:
        return None
    for index in range(count):
        item = locator.nth(index)
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def _vertical_search_input(page: Page):
    candidates = (
        page.get_by_placeholder(re.compile(r"vertical|category", re.IGNORECASE)),
        page.locator('input[placeholder*="vertical" i], input[placeholder*="category" i]'),
    )
    for locator in candidates:
        item = _first_visible(locator)
        if item is not None:
            return item
    raise RuntimeError("Makro Step 1 vertical/category search input not found")


def _brand_input(page: Page):
    candidates = (
        page.get_by_placeholder(re.compile(r"brand", re.IGNORECASE)),
        page.locator('input[placeholder*="brand" i]'),
    )
    for locator in candidates:
        item = _first_visible(locator)
        if item is not None:
            return item
    raise RuntimeError("Makro Step 2 brand input not found")


def _visible_text_candidates(page: Page, *, limit: int = 160) -> list[str]:
    raw = page.evaluate(
        """(limit) => {
          const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
          const visible = (el) => {
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && rect.width > 2 && rect.height > 2;
          };
          const out = [];
          const seen = new Set();
          for (const el of document.querySelectorAll('body *')) {
            if (out.length >= limit) break;
            if (!visible(el)) continue;
            const text = clean(el.innerText || el.textContent || '');
            if (!text || text.length < 2 || text.length > 90 || text.includes('\n')) continue;
            let sameChild = false;
            for (const child of el.children || []) {
              if (clean(child.innerText || child.textContent || '') === text) {
                sameChild = true;
                break;
              }
            }
            if (sameChild) continue;
            const key = text.toLowerCase();
            if (seen.has(key)) continue;
            seen.add(key);
            out.push(text);
          }
          return out;
        }""",
        int(limit),
    )
    excluded = {
        "select vertical",
        "select brand",
        "add product info",
        "your verticals",
        "browse verticals",
        "check brand",
        "or",
    }
    output: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        value = re.sub(r"\s+", " ", str(item or "")).strip()
        key = normalize_label(value)
        if not key or key in excluded or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _build_vertical_choice_request(
    hints: ListingBootstrapHints,
    search_term: str,
    candidates: list[str],
) -> dict[str, Any]:
    allowed = list(dict.fromkeys(candidates))
    return {
        "task": "choose_exact_makro_vertical",
        "system_instruction": "Choose the best exact Makro vertical from the live candidates. JSON only.",
        "product_summary": hints.product_summary,
        "search_term": search_term,
        "live_candidates": allowed,
        "rules": [
            "selected_vertical must be copied exactly from live_candidates or be empty.",
            "Choose only when the candidate clearly describes the same product type.",
            "Prefer the most specific product type over a broad department/category.",
            "If no candidate is a clear match, return an empty string.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selected_vertical": {"type": "string", "enum": ["", *allowed]},
            },
            "required": ["selected_vertical"],
        },
        "strict_json_schema": True,
    }


def choose_vertical_candidate(
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    search_term: str,
    candidates: list[str],
) -> str:
    if not candidates:
        return ""
    raw = provider.extract_json(_build_vertical_choice_request(hints, search_term, candidates))
    if not isinstance(raw, dict):
        raise ValueError("vertical chooser response must be a JSON object")
    selected = str(raw.get("selected_vertical") or "").strip()
    if not selected:
        return ""
    by_normalized: dict[str, list[str]] = {}
    for candidate in candidates:
        by_normalized.setdefault(normalize_label(candidate), []).append(candidate)
    matches = by_normalized.get(normalize_label(selected), [])
    if len(matches) != 1:
        raise ValueError(f"AI returned a vertical that is not one unique live candidate: {selected!r}")
    return matches[0]


def _click_exact_visible_text(page: Page, text: str) -> bool:
    locator = page.get_by_text(text, exact=True)
    item = _first_visible(locator)
    if item is not None:
        try:
            item.click(timeout=5000)
            return True
        except Exception:
            pass
    try:
        return bool(
            page.evaluate(
                """(wanted) => {
                  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
                  const visible = (el) => {
                    const s = getComputedStyle(el), r = el.getBoundingClientRect();
                    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 2 && r.height > 2;
                  };
                  for (const el of document.querySelectorAll('body *')) {
                    if (!visible(el) || clean(el.innerText || el.textContent || '') !== wanted) continue;
                    let target = el;
                    for (let i = 0; i < 5 && target; i++, target = target.parentElement) {
                      const role = target.getAttribute && target.getAttribute('role');
                      const style = target instanceof Element ? getComputedStyle(target) : null;
                      if (target.tagName === 'BUTTON' || target.tagName === 'A' || role === 'button'
                          || target.onclick || (style && style.cursor === 'pointer')) {
                        target.click();
                        return true;
                      }
                    }
                    el.click();
                    return true;
                  }
                  return false;
                }""",
                text,
            )
        )
    except Exception:
        return False


def _current_target_values(page: Page) -> tuple[str, str]:
    try:
        target = parse_makro_listing_url(page.url)
        return (target.vertical or "").strip(), (target.brand or "").strip()
    except ValueError:
        return "", ""


def select_vertical(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int = 800,
) -> str:
    if not is_vertical_step(page):
        raise RuntimeError("Makro is not on Step 1 / Select Vertical")
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
        if not _click_exact_visible_text(page, selected):
            continue
        if not _wait_for(is_brand_step, page, timeout_s=15.0):
            continue
        actual_vertical, _ = _current_target_values(page)
        if actual_vertical and normalize_label(actual_vertical) != normalize_label(selected):
            raise RuntimeError(
                f"Makro Step 1 URL verification failed: selected={selected!r}, actual={actual_vertical!r}"
            )
        return actual_vertical or selected
    raise RuntimeError("Makro Step 1 could not resolve a live vertical from search terms: " + " | ".join(attempted))


def _brand_search_terms(hints: ListingBootstrapHints) -> tuple[str, ...]:
    if hints.brand_status == "explicit":
        return (hints.brand,)
    if hints.brand_status == "unbranded":
        return ("Unbranded", "No Brand", "Generic")
    return ()


def _build_brand_choice_request(
    hints: ListingBootstrapHints,
    search_term: str,
    candidates: list[str],
) -> dict[str, Any]:
    allowed = list(dict.fromkeys(candidates))
    return {
        "task": "choose_exact_makro_brand",
        "system_instruction": "Choose an exact Makro brand result from the live candidates. JSON only.",
        "brand_status": hints.brand_status,
        "supplier_brand": hints.brand,
        "search_term": search_term,
        "live_candidates": allowed,
        "rules": [
            "selected_brand must be copied exactly from live_candidates or be empty.",
            "For explicit brand, accept only the same brand with harmless case/punctuation variation.",
            "For unbranded status, accept only a candidate that clearly denotes an unbranded/generic product.",
            "Never substitute a different commercial brand.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"selected_brand": {"type": "string", "enum": ["", *allowed]}},
            "required": ["selected_brand"],
        },
        "strict_json_schema": True,
    }


def choose_brand_candidate(
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    search_term: str,
    candidates: list[str],
) -> str:
    if not candidates:
        return ""
    if hints.brand_status == "explicit":
        wanted = normalize_label(hints.brand)
        exact = [item for item in candidates if normalize_label(item) == wanted]
        if len(exact) == 1:
            return exact[0]
    raw = provider.extract_json(_build_brand_choice_request(hints, search_term, candidates))
    if not isinstance(raw, dict):
        raise ValueError("brand chooser response must be a JSON object")
    selected = str(raw.get("selected_brand") or "").strip()
    if not selected:
        return ""
    matches = [item for item in candidates if normalize_label(item) == normalize_label(selected)]
    if len(matches) != 1:
        raise ValueError(f"AI returned a brand that is not one unique live candidate: {selected!r}")
    return matches[0]


def _click_check_brand(page: Page) -> None:
    button = _first_visible(page.get_by_role("button", name=re.compile(r"check\s*brand", re.IGNORECASE)))
    if button is None:
        button = _first_visible(page.get_by_text(re.compile(r"^\s*check\s*brand\s*$", re.IGNORECASE)))
    if button is None:
        raise RuntimeError("Makro Step 2 Check Brand button not found")
    button.click(timeout=5000)


def select_brand(
    page: Page,
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    *,
    wait_ms: int = 900,
) -> str:
    if not is_brand_step(page):
        raise RuntimeError("Makro is not on Step 2 / Select Brand")
    terms = _brand_search_terms(hints)
    if not terms:
        raise RuntimeError(
            "Supplier evidence did not establish a brand or explicit unbranded status; refusing to invent Step 2 brand"
        )
    brand_input = _brand_input(page)
    for term in terms:
        brand_input.fill("")
        brand_input.fill(term)
        _click_check_brand(page)
        page.wait_for_timeout(wait_ms)
        if is_product_info_step(page):
            _, actual_brand = _current_target_values(page)
            return actual_brand or term
        candidates = _visible_text_candidates(page)
        selected = choose_brand_candidate(provider, hints, term, candidates)
        if not selected:
            continue
        if not _click_exact_visible_text(page, selected):
            continue
        if not _wait_for(is_product_info_step, page, timeout_s=15.0):
            continue
        _, actual_brand = _current_target_values(page)
        if hints.brand_status == "explicit" and actual_brand:
            if normalize_label(actual_brand) != normalize_label(selected):
                raise RuntimeError(
                    f"Makro Step 2 URL verification failed: selected={selected!r}, actual={actual_brand!r}"
                )
        return actual_brand or selected
    raise RuntimeError("Makro Step 2 could not select a verified brand from the live results")


def run_listing_creation(
    page: Page,
    provider: JSONTaskProvider,
    snapshot: SourceSnapshot,
    *,
    vertical_override: str = "",
    brand_override: str = "",
) -> ListingCreationResult:
    if not is_vertical_step(page):
        raise RuntimeError("Start page must be Makro Step 1 / Select Vertical")

    hints = infer_listing_bootstrap(provider, snapshot)
    if vertical_override.strip():
        hints = ListingBootstrapHints(
            vertical_search_terms=(vertical_override.strip(),),
            brand=hints.brand,
            brand_status=hints.brand_status,
            product_summary=hints.product_summary,
        )
    if brand_override.strip():
        hints = ListingBootstrapHints(
            vertical_search_terms=hints.vertical_search_terms,
            brand=brand_override.strip(),
            brand_status="explicit",
            product_summary=hints.product_summary,
        )

    vertical = select_vertical(page, provider, hints)
    brand = select_brand(page, provider, hints)
    if not is_product_info_step(page):
        raise RuntimeError("Makro did not reach Step 3 after Step 1/2 automation")
    actual_vertical, actual_brand = _current_target_values(page)
    if actual_vertical and normalize_label(actual_vertical) != normalize_label(vertical):
        raise RuntimeError(
            f"Final vertical verification failed: expected={vertical!r}, actual={actual_vertical!r}"
        )
    return ListingCreationResult(
        vertical=actual_vertical or vertical,
        brand=actual_brand or brand,
        page_url=page.url,
        hints=hints,
    )


__all__ = [
    "JSONTaskProvider",
    "ListingBootstrapHints",
    "ListingCreationResult",
    "MAKRO_NEW_LISTING_URL",
    "build_bootstrap_request",
    "choose_brand_candidate",
    "choose_vertical_candidate",
    "infer_listing_bootstrap",
    "is_brand_step",
    "is_product_info_step",
    "is_vertical_step",
    "normalize_label",
    "run_listing_creation",
    "select_brand",
    "select_vertical",
]
