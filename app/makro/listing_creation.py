"""Makro Step 1/2 creation for the one-link listing workflow.

Product semantics and portal mechanics are intentionally separated:
- supplier evidence may be in any language;
- a grounded Product Identity is resolved before marketplace search terms;
- ``MakroPortalAdapter`` handles Step 1/2 mechanics using URL/DOM structure
  before localized text;
- only exact live Makro candidates are selected;
- Step 3 writing remains outside this module.

The workflow never clicks Send to QC.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from playwright.sync_api import Page

from ..product_identity import build_vertical_search_terms_request, infer_product_identity
from ..source_snapshot import SourceSnapshot
from .listing import MAKRO_HOME_URL, MAKRO_SINGLE_LISTING_ROUTE, parse_makro_listing_url
from .portal_adapter import ListingStage, MakroPortalAdapter, normalize_ui_text


MAKRO_NEW_LISTING_URL = f"{MAKRO_HOME_URL}#{MAKRO_SINGLE_LISTING_ROUTE}"
_DISALLOWED_VERTICAL_HINT_TOKENS = {"makro", "marketplace", "listing", "seller", "vertical"}
_DISALLOWED_VERTICAL_HINT_VALUES = {"category", "product"}
_ENGLISH_SEARCH_TERM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '&/()+.,-]*$")
_ENGLISH_LETTER = re.compile(r"[A-Za-z]")


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


class BootstrapSearchTermsError(ValueError):
    """The model response had no safe canonical English product-type terms."""


@dataclass(slots=True, frozen=True)
class ListingBootstrapHints:
    vertical_search_terms: tuple[str, ...]
    brand: str
    brand_status: str
    product_summary: str
    product_identity: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "vertical_search_terms": list(self.vertical_search_terms),
            "brand": self.brand,
            "brand_status": self.brand_status,
            "product_summary": self.product_summary,
            "product_identity": dict(self.product_identity or {}),
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
    """Normalize a live UI label without deleting non-Latin scripts."""

    return normalize_ui_text(value)


def _english_search_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _is_usable_vertical_hint(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) < 2 or not text.isascii() or not _ENGLISH_SEARCH_TERM.fullmatch(text):
        return False
    if not _ENGLISH_LETTER.search(text):
        return False
    key = _english_search_key(text)
    if not key or key in _DISALLOWED_VERTICAL_HINT_VALUES:
        return False
    return not bool(set(key.split()) & _DISALLOWED_VERTICAL_HINT_TOKENS)


def _bounded_supplier_evidence(snapshot: SourceSnapshot) -> dict[str, Any]:
    """Legacy prompt helper retained for compatibility tests only.

    Production bootstrap no longer calls this helper; it now routes through the
    grounded Product Identity boundary in ``app.product_identity``.
    """

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
    """Legacy request builder; production uses Product Identity first."""

    return {
        "task": "infer_product_listing_bootstrap_hints",
        "system_instruction": (
            "Infer concise product-type search phrases and product brand status only from exact "
            "supplier evidence. Supplier evidence may be written in any language. Convert the "
            "product type into canonical English marketplace search phrases. Marketplace names "
            "and listing UI terminology are not product types. JSON only."
        ),
        "prompt_instruction": (
            "Use context.supplier_evidence as the sole product evidence. Regardless of the source "
            "language, vertical_search_terms must be short English product-type noun phrases that "
            "can be searched against live Makro options. Do not output marketplace/platform names, "
            "UI labels, or generic words such as product/category/vertical."
        ),
        "context": {"supplier_evidence": _bounded_supplier_evidence(snapshot)},
        "rules": [
            "Return 1 to 4 concise English product-type noun phrases, most specific first.",
            "product_summary should be a concise English description of the product type.",
            "Translate or normalize non-English supplier terminology into ordinary English product nouns.",
            "Search terms are product-type hints only; do not invent an exact marketplace vertical name.",
            "Never return marketplace/platform names or listing UI words as category search terms.",
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


def build_bootstrap_repair_request(snapshot: SourceSnapshot, raw: dict[str, Any]) -> dict[str, Any]:
    """Legacy repair builder retained for compatibility tests only."""

    return {
        "task": "repair_product_listing_bootstrap_search_terms",
        "system_instruction": (
            "Normalize an already identified supplier product type into canonical English search "
            "phrases. Do not add new product facts. JSON only."
        ),
        "prompt_instruction": (
            "Return only corrected vertical_search_terms. Translate any non-English terms to concise "
            "ordinary English product-type noun phrases. Preserve the product meaning and do not "
            "invent a marketplace category name."
        ),
        "context": {
            "supplier_evidence": _bounded_supplier_evidence(snapshot),
            "initial_product_summary": str(raw.get("product_summary") or "").strip(),
            "invalid_search_terms": [
                str(item).strip()
                for item in raw.get("vertical_search_terms") or []
                if str(item).strip()
            ][:4],
        },
        "rules": [
            "Return 1 to 4 English product-type noun phrases, most specific first.",
            "Use ASCII English letters/digits and ordinary spacing/punctuation only.",
            "Do not output Makro, marketplace, listing, seller, vertical, category, or product as a standalone/generic search term.",
            "Do not change brand status or infer any new fact.",
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
                }
            },
            "required": ["vertical_search_terms"],
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
        key = _english_search_key(value)
        if len(value) < 2 or not _is_usable_vertical_hint(value) or key in seen:
            continue
        seen.add(key)
        terms.append(value)
        if len(terms) >= 4:
            break
    if not terms:
        raise BootstrapSearchTermsError("listing bootstrap produced no usable product-type search terms")

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


def infer_listing_bootstrap(
    provider: JSONTaskProvider,
    snapshot: SourceSnapshot,
    *,
    image_paths: Iterable[str | Path] = (),
) -> ListingBootstrapHints:
    """Resolve Product Identity first, then derive marketplace search phrases.

    Raw supplier page-body text never flows into the production bootstrap path.
    The second AI task receives only the already-grounded Product Identity, so a
    supplier platform description cannot become the marketplace search subject.
    """

    identity = infer_product_identity(provider, snapshot, image_paths=image_paths)
    raw_terms = provider.extract_json(build_vertical_search_terms_request(identity))
    if not isinstance(raw_terms, dict):
        raise BootstrapSearchTermsError("product-type search-term response must be a JSON object")

    # The canonical identity itself is always the first candidate. Model-derived
    # synonyms are supplemental, bounded search recall only.
    merged = {
        "vertical_search_terms": [
            identity.product_type_en,
            *list(raw_terms.get("vertical_search_terms") or []),
        ],
        "brand": identity.brand,
        "brand_status": identity.brand_status,
        "product_summary": identity.product_summary,
    }
    parsed = _parse_bootstrap_response(merged)
    return ListingBootstrapHints(
        vertical_search_terms=parsed.vertical_search_terms,
        brand=parsed.brand,
        brand_status=parsed.brand_status,
        product_summary=parsed.product_summary,
        product_identity=identity.as_dict(),
    )


def _portal(page: Page) -> MakroPortalAdapter:
    return MakroPortalAdapter(page)


def _body_text(page: Page) -> str:
    return _portal(page).body_text()


def is_vertical_step(page: Page) -> bool:
    return _portal(page).detect_stage() is ListingStage.VERTICAL


def is_brand_step(page: Page) -> bool:
    return _portal(page).detect_stage() is ListingStage.BRAND


def is_product_info_step(page: Page) -> bool:
    return _portal(page).detect_stage() is ListingStage.PRODUCT_INFO


def _vertical_confirmation_content(page: Page) -> bool:
    text = normalize_label(_body_text(page))
    markers = (
        "Please select a brand to start selling in this vertical",
        "请选择一个品牌开始在此垂直领域销售",
        "请选择品牌以开始在此垂直领域销售",
    )
    if any(normalize_label(marker) in text for marker in markers):
        return True
    try:
        target = _portal(page).target()
        return bool(
            target
            and str(target.vertical or "").strip()
            and not str(target.brand or "").strip()
            and _vertical_select_brand_button(page) is not None
            and not is_brand_step(page)
        )
    except Exception:
        return False


def _vertical_select_brand_button(page: Page):
    return _portal(page).find_action_button("select_brand")


def is_vertical_selected_confirmation(page: Page, selected_vertical: str) -> bool:
    selected = normalize_label(selected_vertical)
    return bool(
        selected
        and _selected_value_verified(page, selected_vertical, index=0, kind="Step 1 URL")
        and _vertical_confirmation_content(page)
        and _vertical_select_brand_button(page) is not None
    )


def _brand_confirmation_content(page: Page) -> bool:
    text = normalize_label(_body_text(page))
    markers = (
        "selected brand",
        "brand details",
        "please select this brand",
        "please select a brand to continue",
        "已选择品牌",
        "品牌详情",
        "请选择此品牌",
        "请选择品牌以继续",
    )
    return any(normalize_label(marker) in text for marker in markers)


def _brand_confirmation_button(page: Page):
    return _portal(page).find_action_button("confirm_brand")


def is_brand_selected_confirmation(page: Page, selected_brand: str) -> bool:
    button = _brand_confirmation_button(page)
    return bool(
        selected_brand.strip()
        and _selected_value_verified(page, selected_brand, index=1, kind="Step 2 URL")
        and (_brand_confirmation_content(page) or button is not None)
        and button is not None
    )


def _create_new_listing_content(page: Page) -> bool:
    text = normalize_label(_body_text(page))
    markers = (
        "You can start selling under this brand",
        "您可以开始使用此品牌销售",
        "您现在可以在此品牌下销售",
    )
    if any(normalize_label(marker) in text for marker in markers):
        return True
    try:
        return _create_new_listing_button(page) is not None
    except Exception:
        return False


def _create_new_listing_button(page: Page):
    return _portal(page).find_action_button("create_listing")


def is_brand_ready_to_create_listing(page: Page, selected_brand: str) -> bool:
    return bool(
        selected_brand.strip()
        and _selected_value_verified(page, selected_brand, index=1, kind="Step 2 URL")
        and _create_new_listing_button(page) is not None
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
    try:
        return _portal(page).find_search_input("vertical")
    except RuntimeError as exc:
        raise RuntimeError("Makro Step 1 vertical/category search input not found") from exc


def _brand_input(page: Page):
    try:
        return _portal(page).find_search_input("brand")
    except RuntimeError as exc:
        raise RuntimeError("Makro Step 2 brand input not found") from exc


def _visible_text_candidates(page: Page, *, limit: int = 160) -> list[str]:
    raw = _portal(page).visible_text_candidates(limit=limit)
    excluded = {
        normalize_label("select vertical"),
        normalize_label("select brand"),
        normalize_label("add product info"),
        normalize_label("your verticals"),
        normalize_label("browse verticals"),
        normalize_label("check brand"),
        normalize_label("选择垂直领域"),
        normalize_label("选择品牌"),
        normalize_label("添加产品信息"),
        normalize_label("浏览垂直栏目"),
        normalize_label("检查品牌"),
        normalize_label("or"),
        normalize_label("或"),
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
        "system_instruction": (
            "Choose the best exact Makro vertical from supplied live candidates. Candidate labels "
            "may be browser-translated or written in another language. JSON only."
        ),
        "prompt_instruction": (
            "Use context.product_summary and context.search_term to choose from "
            "context.live_candidates. Understand candidate meaning regardless of display language. "
            "Do not choose a broad department when a specific matching product type is present."
        ),
        "context": {
            "product_summary": hints.product_summary,
            "search_term": search_term,
            "live_candidates": allowed,
        },
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
    return _portal(page).click_exact_visible_text(text)


def _current_target_values(page: Page) -> tuple[str, str]:
    try:
        target = parse_makro_listing_url(page.url)
        return (target.vertical or "").strip(), (target.brand or "").strip()
    except (ValueError, AttributeError):
        return "", ""


def _contains_non_ascii_letter(value: str) -> bool:
    return any(ch.isalpha() and ord(ch) > 127 for ch in str(value or ""))


def _labels_may_be_localized(selected: str, actual: str) -> bool:
    selected_non_ascii = _contains_non_ascii_letter(selected)
    actual_non_ascii = _contains_non_ascii_letter(actual)
    selected_ascii_letters = bool(re.search(r"[A-Za-z]", selected))
    actual_ascii_letters = bool(re.search(r"[A-Za-z]", actual))
    return bool(
        (selected_non_ascii and actual_ascii_letters)
        or (actual_non_ascii and selected_ascii_letters)
    )


def _verify_selected_value(kind: str, selected: str, actual: str) -> str:
    if actual and normalize_label(actual) != normalize_label(selected):
        # Browser translation can change the displayed label while the URL keeps
        # Makro's canonical English value. Exact display click + canonical URL is
        # a valid verification in that specific cross-script case.
        if not _labels_may_be_localized(selected, actual):
            raise RuntimeError(
                f"Makro {kind} verification failed: selected={selected!r}, actual={actual!r}"
            )
    return actual or selected


def _selected_value_verified(page: Page, selected: str, *, index: int, kind: str) -> bool:
    values = _current_target_values(page)
    actual = values[index] if index < len(values) else ""
    if actual:
        try:
            _verify_selected_value(kind, selected, actual)
            return True
        except RuntimeError:
            return False
    text = normalize_label(_body_text(page))
    return bool(normalize_label(selected) and normalize_label(selected) in text)


def _advance_vertical_confirmation(page: Page, selected: str) -> None:
    transitioned = _wait_for(
        lambda current: is_brand_step(current) or _vertical_confirmation_content(current),
        page,
        timeout_s=15.0,
    )
    if not transitioned:
        raise RuntimeError(
            f"Makro Step 1 selected vertical {selected!r}, but neither Step 2 nor the vertical confirmation appeared"
        )
    if is_brand_step(page):
        return
    if not _selected_value_verified(page, selected, index=0, kind="Step 1 URL"):
        raise RuntimeError(
            f"Makro Step 1 vertical confirmation mismatch: selected={selected!r}"
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


def _click_create_new_listing(page: Page, selected: str) -> None:
    if not _selected_value_verified(page, selected, index=1, kind="Step 2 URL"):
        raise RuntimeError(
            f"Makro Step 2 create-listing confirmation mismatch: selected={selected!r}"
        )
    button = _create_new_listing_button(page)
    if button is None:
        raise RuntimeError(
            "Makro Step 2 brand is ready, but the exact Create New Listing button was not found"
        )
    button.click(timeout=5000)
    if not _wait_for(is_product_info_step, page, timeout_s=15.0):
        raise RuntimeError(
            "Makro Step 2 clicked Create New Listing, but Step 3 did not appear"
        )


def _advance_brand_confirmation(page: Page, selected: str) -> None:
    transitioned = _wait_for(
        lambda current: is_product_info_step(current)
        or _brand_confirmation_content(current)
        or _brand_confirmation_button(current) is not None
        or _create_new_listing_content(current)
        or _create_new_listing_button(current) is not None,
        page,
        timeout_s=15.0,
    )
    if not transitioned:
        raise RuntimeError(
            f"Makro Step 2 selected brand {selected!r}, but no confirmation or create-listing state appeared"
        )
    if is_product_info_step(page):
        return

    if _create_new_listing_content(page) or _create_new_listing_button(page) is not None:
        _click_create_new_listing(page, selected)
        return

    if not _selected_value_verified(page, selected, index=1, kind="Step 2 URL"):
        raise RuntimeError(
            f"Makro Step 2 brand confirmation mismatch: selected={selected!r}"
        )
    button = _brand_confirmation_button(page)
    if button is None:
        raise RuntimeError(
            "Makro Step 2 brand confirmation appeared, but no exact Select/Confirm/Use Brand button was found"
        )
    button.click(timeout=5000)

    advanced = _wait_for(
        lambda current: is_product_info_step(current)
        or _create_new_listing_content(current)
        or _create_new_listing_button(current) is not None,
        page,
        timeout_s=15.0,
    )
    if not advanced:
        raise RuntimeError(
            "Makro Step 2 clicked the brand confirmation button, but neither Step 3 nor Create New Listing appeared"
        )
    if is_product_info_step(page):
        return
    _click_create_new_listing(page, selected)


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
            raise RuntimeError(f"Makro Step 1 could not click selected live vertical: {selected!r}")
        _advance_vertical_confirmation(page, selected)
        actual_vertical, _ = _current_target_values(page)
        return _verify_selected_value("Step 1 URL", selected, actual_vertical)
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
        "system_instruction": (
            "Choose an exact Makro brand result from supplied live candidates. Candidate labels may "
            "be browser-translated. JSON only."
        ),
        "prompt_instruction": (
            "Use context.brand_status, context.supplier_brand and context.search_term to choose only "
            "from context.live_candidates. Understand unbranded/generic labels regardless of display "
            "language. Never substitute another commercial brand."
        ),
        "context": {
            "brand_status": hints.brand_status,
            "supplier_brand": hints.brand,
            "search_term": search_term,
            "live_candidates": allowed,
        },
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
    try:
        brand_input = _brand_input(page)
    except RuntimeError:
        brand_input = None
    button = _portal(page).find_action_button("check_brand", related_input=brand_input)
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
            return _verify_selected_value("Step 2 URL", term, actual_brand)
        candidates = _visible_text_candidates(page)
        selected = choose_brand_candidate(provider, hints, term, candidates)
        if not selected:
            continue
        if not _click_exact_visible_text(page, selected):
            raise RuntimeError(f"Makro Step 2 could not click selected live brand: {selected!r}")
        _advance_brand_confirmation(page, selected)
        _, actual_brand = _current_target_values(page)
        return _verify_selected_value("Step 2 URL", selected, actual_brand)
    raise RuntimeError("Makro Step 2 could not select a verified brand from the live results")


def run_listing_creation(
    page: Page,
    provider: JSONTaskProvider,
    snapshot: SourceSnapshot,
    *,
    image_paths: Iterable[str | Path] = (),
    vertical_override: str = "",
    brand_override: str = "",
) -> ListingCreationResult:
    if not is_vertical_step(page):
        raise RuntimeError("Start page must be Makro Step 1 / Select Vertical")

    hints = infer_listing_bootstrap(provider, snapshot, image_paths=image_paths)
    if vertical_override.strip():
        hints = ListingBootstrapHints(
            vertical_search_terms=(vertical_override.strip(),),
            brand=hints.brand,
            brand_status=hints.brand_status,
            product_summary=hints.product_summary,
            product_identity=hints.product_identity,
        )
    if brand_override.strip():
        hints = ListingBootstrapHints(
            vertical_search_terms=hints.vertical_search_terms,
            brand=brand_override.strip(),
            brand_status="explicit",
            product_summary=hints.product_summary,
            product_identity=hints.product_identity,
        )

    vertical = select_vertical(page, provider, hints)
    brand = select_brand(page, provider, hints)
    if not is_product_info_step(page):
        raise RuntimeError("Makro did not reach Step 3 after Step 1/2 automation")
    actual_vertical, actual_brand = _current_target_values(page)
    if actual_vertical:
        _verify_selected_value("final vertical", vertical, actual_vertical)
    return ListingCreationResult(
        vertical=actual_vertical or vertical,
        brand=actual_brand or brand,
        page_url=page.url,
        hints=hints,
    )


__all__ = [
    "BootstrapSearchTermsError",
    "JSONTaskProvider",
    "ListingBootstrapHints",
    "ListingCreationResult",
    "MAKRO_NEW_LISTING_URL",
    "build_bootstrap_repair_request",
    "build_bootstrap_request",
    "choose_brand_candidate",
    "choose_vertical_candidate",
    "infer_listing_bootstrap",
    "is_brand_ready_to_create_listing",
    "is_brand_step",
    "is_brand_selected_confirmation",
    "is_product_info_step",
    "is_vertical_selected_confirmation",
    "is_vertical_step",
    "normalize_label",
    "run_listing_creation",
    "select_brand",
    "select_vertical",
]
