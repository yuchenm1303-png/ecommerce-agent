"""Deterministic locator primitives for Makro listing attributes.

Every real write is scoped to its owning section card. Besides locating the
captured control itself, this module owns the generic ``+`` action attached to a
Makro attribute wrapper so multi-value expansion is shared by the real executor
rather than living only in synthetic coverage code.
"""

from __future__ import annotations

from typing import Any


def _css_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def selector_for_control(control: dict[str, Any]) -> str:
    """Return the deterministic Playwright selector for one Makro control."""

    name = str(control.get("name") or "")
    if name:
        return f'[name="{_css_attr(name)}"]'
    path = str(control.get("path") or "")
    if path:
        return path
    candidates = control.get("selector_candidates") or []
    if candidates:
        return str(candidates[0])
    raise ValueError("控件没有可用 selector。")


def scoped_selector_for_control(
    section_path: str | None, control: dict[str, Any]
) -> str:
    """Scope a control selector to its listing section card."""

    inner = selector_for_control(control)
    if not section_path:
        return inner
    return f"{section_path} >> {inner}"


def click_add_value_for_control(
    page: Any,
    section_path: str,
    control: dict[str, Any],
) -> dict[str, Any]:
    """Click the visible ``+`` belonging to exactly one captured attribute.

    The action never searches the whole page for a plus sign. It first resolves
    the captured value control uniquely inside ``section_path``, then restricts
    the button search to that control's ``EditAttributeItemWrapper``.
    """

    selector = scoped_selector_for_control(section_path, control)
    matches = page.locator(selector)
    count = matches.count()
    if count != 1:
        return {
            "available": False,
            "clicked": False,
            "reason": f"control selector matched {count}, expected 1",
        }
    locator = matches.first
    if not locator.is_visible():
        return {
            "available": False,
            "clicked": False,
            "reason": "captured control is not visible",
        }
    locator.scroll_into_view_if_needed()
    return locator.evaluate(
        """el => {
          const wrapper = el.closest('[class*="EditAttributeItemWrapper"]');
          if (!wrapper) return {available:false, clicked:false, reason:'no-wrapper'};
          const candidates = [...wrapper.querySelectorAll('button, a')].filter((node) => {
            const text = String(node.innerText || node.textContent || '').trim();
            const aria = String(node.getAttribute('aria-label') || '').trim().toLowerCase();
            const title = String(node.getAttribute('title') || '').trim().toLowerCase();
            return text === '+' || aria === 'add' || aria.includes('add value') || title.includes('add value');
          });
          const button = candidates.find((node) => {
            const style = getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            return !node.disabled && node.getAttribute('aria-disabled') !== 'true'
              && style.display !== 'none' && style.visibility !== 'hidden'
              && rect.width > 0 && rect.height > 0;
          });
          if (!button) return {available:false, clicked:false, reason:'no-visible-add'};
          button.click();
          return {available:true, clicked:true, reason:''};
        }"""
    )
