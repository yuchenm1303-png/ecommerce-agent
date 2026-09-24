"""Structural reader/clicker for the live Makro Step 1 taxonomy browser.

The browser UI exposes vertical categories as narrow scrollable columns. This
module reads those columns directly from the current DOM and clicks one exact
node inside a requested level. It intentionally contains no product semantics:
AI selection stays in ``listing_creation`` and only chooses from the exact live
labels returned here.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page


_COLUMNS_JS = r"""(limit) => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const visibleStyle = (el) => {
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) !== 0;
  };
  const searchRects = [...document.querySelectorAll('input:not([type]),input[type="text"],input[type="search"]')]
    .filter((el) => visibleStyle(el))
    .map((el) => el.getBoundingClientRect())
    .filter((r) => r.width >= 180 && r.height >= 20 && r.top < innerHeight * 0.35);
  const anchorBottom = searchRects.length ? Math.max(...searchRects.map((r) => r.bottom)) : 0;
  const leafText = (el) => {
    const text = clean(el.innerText || el.textContent || '');
    if (!text || text.length < 2 || text.length > 90) return '';
    for (const child of el.children || []) {
      if (clean(child.innerText || child.textContent || '') === text) return '';
    }
    return text;
  };
  const itemLabels = (container) => {
    const cr = container.getBoundingClientRect();
    const out = [];
    const seen = new Set();
    for (const el of container.querySelectorAll('*')) {
      if (!visibleStyle(el)) continue;
      const text = leafText(el);
      if (!text) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 20 || r.height < 8 || r.height > 100) continue;
      if (r.left < cr.left - 6 || r.right > cr.right + 6) continue;
      const rowish = r.width >= Math.min(48, Math.max(28, cr.width * 0.25));
      const clickable = !!el.closest('button,a,[role="button"],[role="option"],[role="menuitem"],li')
        || getComputedStyle(el).cursor === 'pointer'
        || typeof el.onclick === 'function';
      if (!rowish && !clickable) continue;
      const key = text.toLocaleLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(text);
      if (out.length >= limit) break;
    }
    return out;
  };

  const pools = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visibleStyle(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 80 || r.width > 360 || r.height < 150) continue;
    if (r.right < 0 || r.left > innerWidth || r.bottom < 0 || r.top > innerHeight) continue;
    if (r.top < anchorBottom - 16) continue;
    if (r.left > innerWidth * 0.68) continue;
    const s = getComputedStyle(el);
    const scrollable = ['auto', 'scroll'].includes(s.overflowY)
      || el.scrollHeight > el.clientHeight + 12;
    if (!scrollable) continue;
    const items = itemLabels(el);
    if (items.length < 2) continue;
    pools.push({el, x: r.left, y: r.top, width: r.width, height: r.height, items});
  }

  pools.sort((a, b) => a.x - b.x || a.y - b.y || a.width - b.width);
  const kept = [];
  for (const candidate of pools) {
    let duplicate = false;
    for (const existing of kept) {
      const sameX = Math.abs(existing.x - candidate.x) < 10;
      const sameWidth = Math.abs(existing.width - candidate.width) < 22;
      const a = existing.items.join('\u0001').toLocaleLowerCase();
      const b = candidate.items.join('\u0001').toLocaleLowerCase();
      if (sameX && sameWidth && (a === b || a.includes(b) || b.includes(a))) {
        duplicate = true;
        if (candidate.items.length > existing.items.length) {
          existing.el = candidate.el;
          existing.items = candidate.items;
          existing.width = candidate.width;
          existing.height = candidate.height;
        }
        break;
      }
    }
    if (!duplicate) kept.push(candidate);
  }

  kept.sort((a, b) => a.x - b.x || a.y - b.y);
  return kept.map((item) => item.items);
}"""


_CLICK_JS = r"""({level, wanted, limit}) => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const visibleStyle = (el) => {
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) !== 0;
  };
  const searchRects = [...document.querySelectorAll('input:not([type]),input[type="text"],input[type="search"]')]
    .filter((el) => visibleStyle(el))
    .map((el) => el.getBoundingClientRect())
    .filter((r) => r.width >= 180 && r.height >= 20 && r.top < innerHeight * 0.35);
  const anchorBottom = searchRects.length ? Math.max(...searchRects.map((r) => r.bottom)) : 0;
  const leafText = (el) => {
    const text = clean(el.innerText || el.textContent || '');
    if (!text || text.length < 2 || text.length > 90) return '';
    for (const child of el.children || []) {
      if (clean(child.innerText || child.textContent || '') === text) return '';
    }
    return text;
  };
  const itemElements = (container) => {
    const cr = container.getBoundingClientRect();
    const out = [];
    const seen = new Set();
    for (const el of container.querySelectorAll('*')) {
      if (!visibleStyle(el)) continue;
      const text = leafText(el);
      if (!text) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 20 || r.height < 8 || r.height > 100) continue;
      if (r.left < cr.left - 6 || r.right > cr.right + 6) continue;
      const rowish = r.width >= Math.min(48, Math.max(28, cr.width * 0.25));
      const clickable = !!el.closest('button,a,[role="button"],[role="option"],[role="menuitem"],li')
        || getComputedStyle(el).cursor === 'pointer'
        || typeof el.onclick === 'function';
      if (!rowish && !clickable) continue;
      const key = text.toLocaleLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({el, text});
      if (out.length >= limit) break;
    }
    return out;
  };

  const pools = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visibleStyle(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 80 || r.width > 360 || r.height < 150) continue;
    if (r.right < 0 || r.left > innerWidth || r.bottom < 0 || r.top > innerHeight) continue;
    if (r.top < anchorBottom - 16) continue;
    if (r.left > innerWidth * 0.68) continue;
    const s = getComputedStyle(el);
    const scrollable = ['auto', 'scroll'].includes(s.overflowY)
      || el.scrollHeight > el.clientHeight + 12;
    if (!scrollable) continue;
    const items = itemElements(el);
    if (items.length < 2) continue;
    pools.push({el, x: r.left, y: r.top, width: r.width, items});
  }
  pools.sort((a, b) => a.x - b.x || a.y - b.y || a.width - b.width);

  const kept = [];
  for (const candidate of pools) {
    let duplicate = false;
    for (const existing of kept) {
      const sameX = Math.abs(existing.x - candidate.x) < 10;
      const sameWidth = Math.abs(existing.width - candidate.width) < 22;
      const a = existing.items.map((x) => x.text).join('\u0001').toLocaleLowerCase();
      const b = candidate.items.map((x) => x.text).join('\u0001').toLocaleLowerCase();
      if (sameX && sameWidth && (a === b || a.includes(b) || b.includes(a))) {
        duplicate = true;
        if (candidate.items.length > existing.items.length) {
          existing.el = candidate.el;
          existing.items = candidate.items;
          existing.width = candidate.width;
        }
        break;
      }
    }
    if (!duplicate) kept.push(candidate);
  }
  kept.sort((a, b) => a.x - b.x || a.y - b.y);
  if (level < 0 || level >= kept.length) return false;

  const column = kept[level];
  const matches = column.items.filter((item) => clean(item.text) === clean(wanted));
  if (matches.length !== 1) return false;
  const source = matches[0].el;
  source.scrollIntoView({block: 'center', inline: 'nearest'});
  let target = source;
  for (let i = 0; i < 6 && target && target !== column.el; i++, target = target.parentElement) {
    const role = target.getAttribute && target.getAttribute('role');
    const style = target instanceof Element ? getComputedStyle(target) : null;
    if (target.tagName === 'BUTTON' || target.tagName === 'A'
        || role === 'button' || role === 'option' || role === 'menuitem'
        || target.onclick || (style && style.cursor === 'pointer')) {
      target.click();
      return true;
    }
  }
  source.click();
  return true;
}"""


class MakroTaxonomyBrowser:
    """Read and click the live, currently-rendered vertical taxonomy columns."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def columns(self, *, max_items_per_level: int = 160) -> list[list[str]]:
        try:
            raw = self.page.evaluate(_COLUMNS_JS, int(max_items_per_level))
        except Exception:
            return []
        output: list[list[str]] = []
        for column in raw or []:
            if not isinstance(column, list):
                continue
            values: list[str] = []
            seen: set[str] = set()
            for item in column:
                value = re.sub(r"\s+", " ", str(item or "")).strip()
                key = value.casefold()
                if not value or key in seen:
                    continue
                seen.add(key)
                values.append(value)
            if values:
                output.append(values)
        return output

    def click_node(self, level: int, text: str, *, max_items_per_level: int = 160) -> bool:
        value = re.sub(r"\s+", " ", str(text or "")).strip()
        if level < 0 or not value:
            return False
        try:
            return bool(
                self.page.evaluate(
                    _CLICK_JS,
                    {
                        "level": int(level),
                        "wanted": value,
                        "limit": int(max_items_per_level),
                    },
                )
            )
        except Exception:
            return False


__all__ = ["MakroTaxonomyBrowser"]
