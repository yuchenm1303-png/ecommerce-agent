"""Language-tolerant Makro Step 1/2 portal adapter.

This module is deliberately narrow: it recognizes the current Add Listing stage,
locates the small set of pre-Step-3 controls, reads live candidate labels and
performs exact UI clicks. Product semantics stay in ``listing_creation`` and
Step 3 field execution stays in the existing domain/executor layers.

Recognition order is structural first (route/query + form shape), then stable
control attributes, then localized text as a conservative fallback. Display
language is therefore not a workflow invariant.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Any

from playwright.sync_api import Page

from .listing import parse_makro_listing_url


class ListingStage(str, Enum):
    VERTICAL = "step1"
    BRAND = "step2"
    PRODUCT_INFO = "step3"
    UNKNOWN = "unknown"


_VERTICAL_TOKENS = (
    "vertical",
    "category",
    "categories",
    "垂直",
    "类别",
    "分类",
    "类目",
    "品类",
)
_BRAND_TOKENS = ("brand", "品牌")
_ACTION_TOKENS: dict[str, tuple[str, ...]] = {
    "check_brand": (
        "check brand",
        "brand check",
        "检查品牌",
        "核验品牌",
        "查看品牌",
    ),
    "select_brand": (
        "select brand",
        "choose brand",
        "选择品牌",
        "选取品牌",
    ),
    "confirm_brand": (
        "confirm brand",
        "use brand",
        "select brand",
        "确认品牌",
        "使用品牌",
        "选择品牌",
    ),
    "create_listing": (
        "create new listing",
        "create listing",
        "new listing",
        "创建新商品",
        "创建新上架",
        "创建新刊登",
        "新建商品",
        "新建刊登",
    ),
}


def normalize_ui_text(value: object) -> str:
    """Unicode-safe normalization for labels shown by the portal.

    Unlike the old ASCII-only normalizer, this preserves letters/numbers from
    every script, so Chinese or browser-translated labels do not disappear.
    """

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    chars = [ch if (ch.isalnum() or ch.isspace()) else " " for ch in text]
    return re.sub(r"\s+", " ", "".join(chars)).strip()


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


def _visible_items(locator, *, editable: bool = False) -> list[Any]:
    output: list[Any] = []
    try:
        count = locator.count()
    except Exception:
        return output
    for index in range(count):
        item = locator.nth(index)
        try:
            if not item.is_visible():
                continue
            if editable and not item.is_editable():
                continue
            output.append(item)
        except Exception:
            continue
    return output


def _attribute_blob(locator) -> str:
    values: list[str] = []
    for name in (
        "placeholder",
        "name",
        "id",
        "aria-label",
        "title",
        "data-testid",
        "data-test",
        "data-action",
        "class",
    ):
        try:
            value = locator.get_attribute(name)
        except Exception:
            value = None
        if value:
            values.append(str(value))
    try:
        text = locator.inner_text(timeout=500)
    except Exception:
        text = ""
    if text:
        values.append(text)
    return normalize_ui_text(" ".join(values))


def _contains_any(value: str, tokens: tuple[str, ...]) -> bool:
    normalized = normalize_ui_text(value)
    return any(normalize_ui_text(token) in normalized for token in tokens)


class MakroPortalAdapter:
    """Small, conservative adapter for Makro pre-Step-3 portal mechanics."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def body_text(self) -> str:
        try:
            return self.page.locator("body").inner_text(timeout=3000)
        except Exception:
            return ""

    def target(self):
        try:
            return parse_makro_listing_url(self.page.url)
        except (ValueError, AttributeError):
            return None

    def _has_password(self) -> bool:
        try:
            return _first_visible(self.page.locator('input[type="password"]')) is not None
        except Exception:
            return False

    def _text_inputs(self) -> list[Any]:
        selector = (
            'input:not([type]), input[type="text"], input[type="search"], '
            'textarea'
        )
        return _visible_items(self.page.locator(selector), editable=True)

    def _form_control_count(self) -> int:
        try:
            value = self.page.evaluate(
                r"""() => {
                  const visible = (el) => {
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden'
                      && rect.width > 2 && rect.height > 2;
                  };
                  const selector = [
                    'input:not([type="hidden"]):not([type="password"])',
                    'textarea', 'select', '[role="combobox"]', '[contenteditable="true"]'
                  ].join(',');
                  return [...document.querySelectorAll(selector)].filter(visible).length;
                }"""
            )
            return int(value or 0)
        except Exception:
            return 0

    def _text_stage_fallback(self) -> ListingStage:
        text = normalize_ui_text(self.body_text())
        vertical_markers = (
            "select the vertical for your product",
            "browse verticals",
            "选择待产品的垂直领域",
            "浏览垂直栏目",
            "进入垂直类别",
        )
        brand_markers = (
            "check for the brand you want to sell",
            "enter brand name",
            "检查您要销售的品牌",
            "输入品牌名称",
        )
        product_markers = (
            "add product info",
            "product photos",
            "price stock and shipping information",
            "添加产品信息",
            "产品照片",
            "价格 库存和配送信息",
        )
        if sum(normalize_ui_text(marker) in text for marker in product_markers) >= 2:
            return ListingStage.PRODUCT_INFO
        if any(normalize_ui_text(marker) in text for marker in brand_markers):
            return ListingStage.BRAND
        if any(normalize_ui_text(marker) in text for marker in vertical_markers):
            return ListingStage.VERTICAL
        return ListingStage.UNKNOWN

    def detect_stage(self) -> ListingStage:
        """Detect Step 1/2/3 without making display language the primary signal."""

        target = self.target()
        if target is None or self._has_password():
            return ListingStage.UNKNOWN

        controls = self._form_control_count()
        inputs = self._text_inputs()
        vertical = str(target.vertical or "").strip()
        brand = str(target.brand or "").strip()

        # Step 3 exposes a dense attribute form. requestId/vid are useful hints,
        # but density prevents a brand-confirmation page from being mistaken for
        # the editable product form.
        if (target.request_id or target.vid or (vertical and brand)) and controls >= 5:
            return ListingStage.PRODUCT_INFO

        # The URL is the strongest pre-Step-3 state signal. The single editable
        # search control is structural confirmation that the SPA has rendered.
        if not vertical and inputs:
            return ListingStage.VERTICAL
        if vertical and not brand and inputs:
            return ListingStage.BRAND

        fallback = self._text_stage_fallback()
        if fallback is not ListingStage.UNKNOWN:
            return fallback
        return ListingStage.UNKNOWN

    def find_search_input(self, kind: str):
        """Find the Step 1/2 search box using stable attributes, then form shape."""

        if kind not in {"vertical", "brand"}:
            raise ValueError(f"unsupported Makro search input kind={kind!r}")
        tokens = _VERTICAL_TOKENS if kind == "vertical" else _BRAND_TOKENS
        inputs = self._text_inputs()
        if not inputs:
            raise RuntimeError(f"Makro {kind} search input not found")

        scored: list[tuple[int, Any]] = []
        for item in inputs:
            blob = _attribute_blob(item)
            score = 0
            if any(normalize_ui_text(token) in blob for token in tokens):
                score += 100
            try:
                if item.evaluate(
                    "(el) => !!el.closest('main,[role=main],[class*=listing i],[class*=content i]')"
                ):
                    score += 10
            except Exception:
                pass
            scored.append((score, item))

        best = max(score for score, _ in scored)
        winners = [item for score, item in scored if score == best]
        if best > 0 and len(winners) == 1:
            return winners[0]
        if len(inputs) == 1:
            return inputs[0]

        in_main: list[Any] = []
        for item in inputs:
            try:
                if item.evaluate("(el) => !!el.closest('main,[role=main]')"):
                    in_main.append(item)
            except Exception:
                continue
        if len(in_main) == 1:
            return in_main[0]
        raise RuntimeError(
            f"Makro {kind} search input is ambiguous: {len(inputs)} editable text controls are visible"
        )

    def _button_candidates(self, root=None) -> list[Any]:
        scope = root if root is not None else self.page
        try:
            locator = scope.locator('button, [role="button"]')
        except Exception:
            return []
        output: list[Any] = []
        for item in _visible_items(locator):
            try:
                if not item.is_enabled():
                    continue
            except Exception:
                pass
            output.append(item)
        return output

    def _nearby_buttons(self, related_input) -> list[Any]:
        if related_input is None:
            return []
        xpaths = (
            "xpath=ancestor::form[1]",
            "xpath=ancestor::*[@role='dialog'][1]",
            "xpath=ancestor::section[1]",
            "xpath=ancestor::div[1]",
        )
        for xpath in xpaths:
            try:
                scope = related_input.locator(xpath)
                if scope.count() < 1:
                    continue
                buttons = self._button_candidates(scope.first)
                if buttons:
                    return buttons
            except Exception:
                continue
        return []

    def find_action_button(self, action: str, *, related_input=None):
        """Find one semantic action conservatively; never guess among many buttons."""

        tokens = _ACTION_TOKENS.get(action)
        if tokens is None:
            raise ValueError(f"unsupported Makro action={action!r}")

        buttons = self._button_candidates()
        scored: list[tuple[int, Any]] = []
        for item in buttons:
            blob = _attribute_blob(item)
            score = 0
            for token in tokens:
                normalized = normalize_ui_text(token)
                if blob == normalized:
                    score = max(score, 200)
                elif normalized and normalized in blob:
                    score = max(score, 100)
            if score:
                scored.append((score, item))
        if scored:
            best = max(score for score, _ in scored)
            winners = [item for score, item in scored if score == best]
            if len(winners) == 1:
                return winners[0]

        nearby = self._nearby_buttons(related_input)
        if len(nearby) == 1:
            return nearby[0]

        main_buttons: list[Any] = []
        try:
            mains = _visible_items(self.page.locator('main, [role="main"]'))
        except Exception:
            mains = []
        for main in mains:
            main_buttons.extend(self._button_candidates(main))
        deduped: list[Any] = []
        seen: set[str] = set()
        for item in main_buttons:
            try:
                key = str(item.evaluate("(el) => el.outerHTML"))
            except Exception:
                key = str(id(item))
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        if len(deduped) == 1:
            return deduped[0]
        if len(buttons) == 1:
            return buttons[0]
        return None

    def visible_text_candidates(self, *, limit: int = 160) -> list[str]:
        raw = self.page.evaluate(
            r"""(limit) => {
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
                if (!text || text.length < 2 || text.length > 90 || text.includes('\\n')) continue;
                let sameChild = false;
                for (const child of el.children || []) {
                  if (clean(child.innerText || child.textContent || '') === text) {
                    sameChild = true;
                    break;
                  }
                }
                if (sameChild) continue;
                const key = text.toLocaleLowerCase();
                if (seen.has(key)) continue;
                seen.add(key);
                out.push(text);
              }
              return out;
            }""",
            int(limit),
        )
        return [re.sub(r"\s+", " ", str(item or "")).strip() for item in raw or [] if str(item or "").strip()]

    def click_exact_visible_text(self, text: str) -> bool:
        try:
            item = _first_visible(self.page.get_by_text(text, exact=True))
        except Exception:
            item = None
        if item is not None:
            try:
                item.click(timeout=5000)
                return True
            except Exception:
                pass
        try:
            return bool(
                self.page.evaluate(
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

    def diagnostics(self) -> dict[str, Any]:
        target = self.target()
        return {
            "stage": self.detect_stage().value,
            "url": str(getattr(self.page, "url", "") or ""),
            "vertical": str(getattr(target, "vertical", "") or "") if target else "",
            "brand": str(getattr(target, "brand", "") or "") if target else "",
            "request_id": str(getattr(target, "request_id", "") or "") if target else "",
            "vid": str(getattr(target, "vid", "") or "") if target else "",
            "editable_text_inputs": len(self._text_inputs()),
            "form_controls": self._form_control_count(),
        }


__all__ = ["ListingStage", "MakroPortalAdapter", "normalize_ui_text"]
