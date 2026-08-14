"""Hit-tested search-result surface reader for Makro portal comboboxes.

The portal can render autocomplete rows through framework portals with unstable
class names and without ARIA roles. This module anchors result ownership to the
live input geometry and browser hit-testing instead of CSS implementation
details. It only reads/clicks rows that are actually visible and topmost around
the search control.
"""

from __future__ import annotations


_READ_ROWS_JS = r"""(input) => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const label = (el) => {
    const raw = String(el.innerText || el.textContent || '');
    const lines = raw.split(/\n+/).map(clean).filter(Boolean);
    const unique = [];
    for (const line of lines) {
      if (!unique.length || unique[unique.length - 1] !== line) unique.push(line);
    }
    if (unique.length >= 2 && unique.length <= 6) return unique.join(' / ');
    return clean(raw);
  };
  const visible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden'
      && Number(s.opacity || 1) !== 0 && s.pointerEvents !== 'none'
      && r.width > 2 && r.height > 2;
  };

  const inputRect = input.getBoundingClientRect();
  const inBand = (el) => {
    const r = el.getBoundingClientRect();
    const overlap = Math.min(r.right, inputRect.right + 48)
      - Math.max(r.left, inputRect.left - 48);
    return overlap > 24
      && r.bottom >= inputRect.bottom - 8
      && r.top <= inputRect.bottom + 680;
  };
  const topmost = (el) => {
    const r = el.getBoundingClientRect();
    const left = Math.max(r.left, inputRect.left);
    const right = Math.min(r.right, inputRect.right);
    const x = right > left ? (left + right) / 2 : r.left + Math.min(r.width / 2, 20);
    const y = Math.min(Math.max(r.top + Math.min(r.height / 2, 18), 0), innerHeight - 1);
    const stack = document.elementsFromPoint(
      Math.min(Math.max(x, 0), innerWidth - 1),
      y
    );
    const hit = stack.find((node) => {
      if (!(node instanceof Element)) return false;
      const s = getComputedStyle(node);
      return s.pointerEvents !== 'none'
        && s.display !== 'none'
        && s.visibility !== 'hidden';
    });
    return !!hit && (el.contains(hit) || hit.contains(el));
  };
  const actionable = (el) => {
    const role = String(el.getAttribute('role') || '').toLowerCase();
    const tag = String(el.tagName || '').toLowerCase();
    const s = getComputedStyle(el);
    return tag === 'button' || tag === 'a'
      || role === 'option' || role === 'menuitem' || role === 'button'
      || !!el.onclick || s.cursor === 'pointer'
      || (el.hasAttribute('tabindex') && Number(el.getAttribute('tabindex')) >= 0);
  };

  const roots = [];
  const addRoot = (el) => {
    if (el && !roots.includes(el) && visible(el) && inBand(el)) roots.push(el);
  };
  for (const attr of ['aria-controls', 'aria-owns']) {
    const id = input.getAttribute(attr);
    if (id) addRoot(document.getElementById(id));
  }
  for (const el of document.querySelectorAll(
    '[role="listbox"], [class*="autocomplete" i], [class*="suggest" i], [class*="search-result" i]'
  )) addRoot(el);

  const out = [], seen = new Set();
  const addRow = (el) => {
    if (!visible(el) || !inBand(el) || !topmost(el)) return;
    const r = el.getBoundingClientRect();
    if (r.height > 150 || r.width < 70) return;
    const text = label(el);
    if (!text || text.length < 2 || text.length > 320) return;

    for (const child of el.children || []) {
      if (visible(child) && inBand(child) && label(child) === text) return;
    }

    const key = text.toLocaleLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push(text);
  };

  const rowSelector = [
    '[role="option"]', '[role="menuitem"]', 'li', 'a', 'button',
    '[tabindex]', '[class*="option" i]', '[class*="result" i]',
    '[class*="suggest" i]'
  ].join(',');
  for (const root of roots) {
    for (const el of root.querySelectorAll(rowSelector)) addRow(el);
  }

  if (!out.length) {
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el) || !inBand(el) || !topmost(el)) continue;
      const r = el.getBoundingClientRect();
      const rowLike = r.height >= 18 && r.height <= 120
        && r.width >= Math.min(120, Math.max(70, inputRect.width * 0.30));
      if (!rowLike) continue;
      if (!actionable(el) && r.width < inputRect.width * 0.55) continue;
      addRow(el);
    }
  }

  return out;
}"""


_CLICK_ROW_JS = r"""(input, wanted) => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const label = (el) => {
    const raw = String(el.innerText || el.textContent || '');
    const lines = raw.split(/\n+/).map(clean).filter(Boolean);
    const unique = [];
    for (const line of lines) {
      if (!unique.length || unique[unique.length - 1] !== line) unique.push(line);
    }
    if (unique.length >= 2 && unique.length <= 6) return unique.join(' / ');
    return clean(raw);
  };
  const visible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden'
      && Number(s.opacity || 1) !== 0 && s.pointerEvents !== 'none'
      && r.width > 2 && r.height > 2;
  };

  const inputRect = input.getBoundingClientRect();
  const inBand = (el) => {
    const r = el.getBoundingClientRect();
    const overlap = Math.min(r.right, inputRect.right + 48)
      - Math.max(r.left, inputRect.left - 48);
    return overlap > 24
      && r.bottom >= inputRect.bottom - 8
      && r.top <= inputRect.bottom + 680;
  };
  const topmost = (el) => {
    const r = el.getBoundingClientRect();
    const left = Math.max(r.left, inputRect.left);
    const right = Math.min(r.right, inputRect.right);
    const x = right > left ? (left + right) / 2 : r.left + Math.min(r.width / 2, 20);
    const y = Math.min(Math.max(r.top + Math.min(r.height / 2, 18), 0), innerHeight - 1);
    const stack = document.elementsFromPoint(
      Math.min(Math.max(x, 0), innerWidth - 1),
      y
    );
    const hit = stack.find((node) => {
      if (!(node instanceof Element)) return false;
      const s = getComputedStyle(node);
      return s.pointerEvents !== 'none'
        && s.display !== 'none'
        && s.visibility !== 'hidden';
    });
    return !!hit && (el.contains(hit) || hit.contains(el));
  };
  const actionable = (el) => {
    const role = String(el.getAttribute('role') || '').toLowerCase();
    const tag = String(el.tagName || '').toLowerCase();
    const s = getComputedStyle(el);
    return tag === 'button' || tag === 'a'
      || role === 'option' || role === 'menuitem' || role === 'button'
      || !!el.onclick || s.cursor === 'pointer'
      || (el.hasAttribute('tabindex') && Number(el.getAttribute('tabindex')) >= 0);
  };

  const matches = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || !inBand(el) || !topmost(el) || label(el) !== wanted) continue;
    const r = el.getBoundingClientRect();
    if (r.height > 150 || r.width < 70) continue;
    let sameChild = false;
    for (const child of el.children || []) {
      if (visible(child) && inBand(child) && label(child) === wanted) {
        sameChild = true;
        break;
      }
    }
    if (!sameChild) matches.push(el);
  }
  if (matches.length !== 1) return false;

  let target = matches[0];
  for (let i = 0; i < 6 && target; i++, target = target.parentElement) {
    if (!visible(target) || !inBand(target)) break;
    if (actionable(target)) {
      target.click();
      return true;
    }
  }
  matches[0].click();
  return true;
}"""


def read_search_rows(search) -> list[str]:
    """Return exact visible row labels belonging to the search surface."""

    try:
        raw = search.evaluate(_READ_ROWS_JS)
    except Exception:
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        value = str(item or "").strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def click_search_row(search, label: str) -> bool:
    """Click one exact, topmost row from the same search surface."""

    wanted = str(label or "").strip()
    if not wanted:
        return False
    try:
        return bool(search.evaluate(_CLICK_ROW_JS, wanted))
    except Exception:
        return False


__all__ = ["click_search_row", "read_search_rows"]
