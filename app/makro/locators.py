"""Deterministic locator primitives for Makro listing attributes.

Every real write is scoped to its owning section card. Besides locating the
captured control itself, this module owns the generic ``+`` action attached to a
Makro attribute wrapper so multi-value discovery and real execution use exactly
the same DOM contract.
"""

from __future__ import annotations

from typing import Any


# Self-contained browser-side helper shared with the field scanner. Makro's add
# control is not guaranteed to be a literal <button>; React variants may render
# an icon/span/div inside an actionable ancestor. Detection stays scoped to the
# current EditAttributeItemWrapper and fails closed unless exactly one visible
# add-value action can be identified. Disabled state is intentionally *not* part
# of capability detection: Makro renders repeatable fields with a disabled ``+``
# while the current value slot is empty, then enables the same button after the
# slot has a valid value. Execution checks enabledness separately before click.
ADD_VALUE_CONTROL_JS = r"""
const findMakroAddValueControl = (el) => {
  const wrapper = el && el.closest
    ? el.closest('[class*="EditAttributeItemWrapper"]')
    : null;
  if (!wrapper) return { node: null, count: 0, reason: 'no-wrapper', disabled: false };

  const cleanAddText = (value) => String(value == null ? '' : value)
    .replace(/\s+/g, ' ')
    .trim();
  const visibleAddNode = (node) => {
    if (!node || !node.getBoundingClientRect) return false;
    if (node.closest && node.closest('[hidden], [aria-hidden="true"]')) return false;
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const disabledAddNode = (node) => Boolean(
    node.disabled
    || node.hasAttribute('disabled')
    || node.getAttribute('aria-disabled') === 'true'
  );
  const signalText = (node) => [
    node.getAttribute('aria-label'),
    node.getAttribute('title'),
    node.getAttribute('data-testid'),
    node.getAttribute('data-action'),
    node.getAttribute('data-icon'),
    typeof node.className === 'string' ? node.className : '',
  ].map(cleanAddText).join(' ').toLowerCase();
  const isAddMarker = (node) => {
    const text = cleanAddText(node.innerText || node.textContent);
    const signal = signalText(node);
    if (/(^|[\s_-])(remove|delete|minus|subtract)([\s_-]|$)/i.test(signal)) return false;
    if (text === '+' || text === '＋') return true;
    return /(^|[\s_-])add([\s_-]*(value|another|more|item))?([\s_-]|$)/i.test(signal)
      || /(^|[\s_-])plus([\s_-]|$)/i.test(signal);
  };

  const actions = [];
  const seen = new Set();
  for (const marker of wrapper.querySelectorAll('*')) {
    if (!isAddMarker(marker) || !visibleAddNode(marker)) continue;
    let action = marker.closest('button, a, [role="button"], [onclick], [tabindex]:not([tabindex="-1"])');
    if (!action || !wrapper.contains(action)) {
      action = getComputedStyle(marker).cursor === 'pointer' ? marker : null;
    }
    if (!action || !wrapper.contains(action) || !visibleAddNode(action)) continue;
    if (!seen.has(action)) {
      seen.add(action);
      actions.push(action);
    }
  }

  if (actions.length !== 1) {
    return {
      node: null,
      count: actions.length,
      reason: actions.length ? 'ambiguous-add-actions' : 'no-visible-add',
      disabled: false,
    };
  }
  const node = actions[0];
  return { node, count: 1, reason: '', disabled: disabledAddNode(node) };
};
"""


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
    """Click the unique visible add-value action for one captured attribute.

    ``available`` means the current field is structurally repeatable. A disabled
    add button therefore reports ``available=True`` but ``clicked=False`` so the
    caller can seed the current slot first instead of misclassifying the field as
    single-value.
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
        "el => {\n"
        + ADD_VALUE_CONTROL_JS
        + r"""
          const found = findMakroAddValueControl(el);
          if (!found.node) {
            return {available:false, clicked:false, reason:found.reason, count:found.count};
          }
          if (found.disabled) {
            return {available:true, clicked:false, reason:'add-disabled', count:1};
          }
          found.node.click();
          return {available:true, clicked:true, reason:'', count:1};
        }"""
    )
