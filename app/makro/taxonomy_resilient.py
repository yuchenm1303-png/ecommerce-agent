"""Resilient structural reader for Makro Step 1 taxonomy columns.

The original reader intentionally recognized only sizeable scrollable columns
with at least two items. Real Makro taxonomies can expose a legitimate next
column containing exactly one child (and that column may not be scrollable).
This reader preserves the proven primary-column heuristic, then extends the
recognized tree only with the nearest aligned clickable column to its right.
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
      const role = el.getAttribute && el.getAttribute('role');
      const style = getComputedStyle(el);
      const clickable = !!el.closest('button,a,[role="button"],[role="option"],[role="menuitem"],li')
        || style.cursor === 'pointer'
        || typeof el.onclick === 'function';
      const rowish = r.width >= Math.min(48, Math.max(28, cr.width * 0.25));
      if (!rowish && !clickable) continue;
      const key = text.toLocaleLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({el, text, clickable});
      if (out.length >= limit) break;
    }
    return out;
  };

  const pools = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visibleStyle(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 48 || r.width > 380 || r.height < 24 || r.height > innerHeight * 0.85) continue;
    if (r.right < 0 || r.left > innerWidth || r.bottom < 0 || r.top > innerHeight) continue;
    if (r.top < anchorBottom - 16) continue;
    if (r.left > innerWidth * 0.72) continue;
    const items = itemElements(el);
    if (!items.length) continue;
    const s = getComputedStyle(el);
    const scrollable = ['auto', 'scroll'].includes(s.overflowY)
      || el.scrollHeight > el.clientHeight + 12;
    const clickableCount = items.filter((item) => item.clickable).length;
    pools.push({
      el, x: r.left, y: r.top, width: r.width, height: r.height,
      items, scrollable, clickableCount
    });
  }

  const itemKey = (pool) => pool.items.map((x) => x.text).join('\u0001').toLocaleLowerCase();
  const dedupe = (input) => {
    const sorted = [...input].sort((a, b) => a.x - b.x || a.y - b.y || b.items.length - a.items.length || b.height - a.height);
    const kept = [];
    for (const candidate of sorted) {
      let duplicate = false;
      for (const existing of kept) {
        const sameX = Math.abs(existing.x - candidate.x) < 12;
        const sameWidth = Math.abs(existing.width - candidate.width) < 28;
        const a = itemKey(existing);
        const b = itemKey(candidate);
        if (sameX && sameWidth && (a === b || a.includes(b) || b.includes(a))) {
          duplicate = true;
          if (candidate.items.length > existing.items.length
              || (candidate.items.length === existing.items.length && candidate.height > existing.height)) {
            existing.el = candidate.el;
            existing.items = candidate.items;
            existing.width = candidate.width;
            existing.height = candidate.height;
            existing.y = candidate.y;
            existing.scrollable = candidate.scrollable;
            existing.clickableCount = candidate.clickableCount;
          }
          break;
        }
      }
      if (!duplicate) kept.push(candidate);
    }
    return kept.sort((a, b) => a.x - b.x || a.y - b.y);
  };

  // Preserve the proven old heuristic for established columns.
  let kept = dedupe(pools.filter((p) => p.width >= 80 && p.height >= 150 && p.scrollable && p.items.length >= 2));

  // If Makro changed enough that no primary column is scrollable, recover a
  // conservative first column containing multiple clickable taxonomy rows.
  if (!kept.length) {
    const roots = dedupe(pools.filter((p) => p.items.length >= 2 && p.clickableCount >= 1));
    if (roots.length) kept = [roots[0]];
  }

  // Extend only to the nearest aligned column on the right. This admits a real
  // one-child/non-scrollable column without treating unrelated singleton text as
  // a taxonomy level.
  for (let depth = 0; depth < 7 && kept.length; depth++) {
    const rightmost = kept[kept.length - 1];
    const nextPools = dedupe(pools.filter((p) => {
      if (p.clickableCount < 1 || p.items.length < 1) return false;
      if (p.x <= rightmost.x + 24 || p.x > rightmost.x + 360) return false;
      if (Math.abs(p.y - rightmost.y) > 180 && Math.abs(p.y - anchorBottom) > 220) return false;
      return !kept.some((k) => Math.abs(k.x - p.x) < 14);
    }));
    if (!nextPools.length) break;
    const minX = Math.min(...nextPools.map((p) => p.x));
    const sameColumn = nextPools.filter((p) => Math.abs(p.x - minX) < 18);
    sameColumn.sort((a, b) => b.items.length - a.items.length || b.height - a.height || a.y - b.y);
    kept.push(sameColumn[0]);
  }

  kept.sort((a, b) => a.x - b.x || a.y - b.y);
  return kept.map((item) => item.items.map((entry) => entry.text));
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
      const style = getComputedStyle(el);
      const clickable = !!el.closest('button,a,[role="button"],[role="option"],[role="menuitem"],li')
        || style.cursor === 'pointer'
        || typeof el.onclick === 'function';
      const rowish = r.width >= Math.min(48, Math.max(28, cr.width * 0.25));
      if (!rowish && !clickable) continue;
      const key = text.toLocaleLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({el, text, clickable});
      if (out.length >= limit) break;
    }
    return out;
  };

  const pools = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visibleStyle(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 48 || r.width > 380 || r.height < 24 || r.height > innerHeight * 0.85) continue;
    if (r.right < 0 || r.left > innerWidth || r.bottom < 0 || r.top > innerHeight) continue;
    if (r.top < anchorBottom - 16) continue;
    if (r.left > innerWidth * 0.72) continue;
    const items = itemElements(el);
    if (!items.length) continue;
    const s = getComputedStyle(el);
    const scrollable = ['auto', 'scroll'].includes(s.overflowY)
      || el.scrollHeight > el.clientHeight + 12;
    const clickableCount = items.filter((item) => item.clickable).length;
    pools.push({
      el, x: r.left, y: r.top, width: r.width, height: r.height,
      items, scrollable, clickableCount
    });
  }

  const itemKey = (pool) => pool.items.map((x) => x.text).join('\u0001').toLocaleLowerCase();
  const dedupe = (input) => {
    const sorted = [...input].sort((a, b) => a.x - b.x || a.y - b.y || b.items.length - a.items.length || b.height - a.height);
    const kept = [];
    for (const candidate of sorted) {
      let duplicate = false;
      for (const existing of kept) {
        const sameX = Math.abs(existing.x - candidate.x) < 12;
        const sameWidth = Math.abs(existing.width - candidate.width) < 28;
        const a = itemKey(existing);
        const b = itemKey(candidate);
        if (sameX && sameWidth && (a === b || a.includes(b) || b.includes(a))) {
          duplicate = true;
          if (candidate.items.length > existing.items.length
              || (candidate.items.length === existing.items.length && candidate.height > existing.height)) {
            existing.el = candidate.el;
            existing.items = candidate.items;
            existing.width = candidate.width;
            existing.height = candidate.height;
            existing.y = candidate.y;
            existing.scrollable = candidate.scrollable;
            existing.clickableCount = candidate.clickableCount;
          }
          break;
        }
      }
      if (!duplicate) kept.push(candidate);
    }
    return kept.sort((a, b) => a.x - b.x || a.y - b.y);
  };

  let kept = dedupe(pools.filter((p) => p.width >= 80 && p.height >= 150 && p.scrollable && p.items.length >= 2));
  if (!kept.length) {
    const roots = dedupe(pools.filter((p) => p.items.length >= 2 && p.clickableCount >= 1));
    if (roots.length) kept = [roots[0]];
  }
  for (let depth = 0; depth < 7 && kept.length; depth++) {
    const rightmost = kept[kept.length - 1];
    const nextPools = dedupe(pools.filter((p) => {
      if (p.clickableCount < 1 || p.items.length < 1) return false;
      if (p.x <= rightmost.x + 24 || p.x > rightmost.x + 360) return false;
      if (Math.abs(p.y - rightmost.y) > 180 && Math.abs(p.y - anchorBottom) > 220) return false;
      return !kept.some((k) => Math.abs(k.x - p.x) < 14);
    }));
    if (!nextPools.length) break;
    const minX = Math.min(...nextPools.map((p) => p.x));
    const sameColumn = nextPools.filter((p) => Math.abs(p.x - minX) < 18);
    sameColumn.sort((a, b) => b.items.length - a.items.length || b.height - a.height || a.y - b.y);
    kept.push(sameColumn[0]);
  }

  kept.sort((a, b) => a.x - b.x || a.y - b.y);
  if (level < 0 || level >= kept.length) return false;
  const column = kept[level];
  const matches = column.items.filter((item) => clean(item.text) === clean(wanted));
  if (matches.length !== 1) return false;
  const source = matches[0].el;
  source.scrollIntoView({block: 'center', inline: 'nearest'});
  let target = source;
  for (let i = 0; i < 7 && target && target !== column.el; i++, target = target.parentElement) {
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


class ResilientMakroTaxonomyBrowser:
    """Read/click exact live columns, including legitimate singleton children."""

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


__all__ = ["ResilientMakroTaxonomyBrowser"]
