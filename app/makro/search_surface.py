"""Query-owned Makro search/result surface mechanics.

A search result belongs to the query that caused it, not merely to a rectangle
below an input. The portal keeps stale taxonomy/brand content mounted while
autocomplete/results are painted over it, so geometry-only scans can read the
wrong layer.

This module snapshots the visible DOM immediately before a query. Afterwards it
accepts only elements that are new or whose rendered text changed, plus explicit
ARIA-owned popup rows. Reads and clicks use the same ownership rule.
"""

from __future__ import annotations

import json
from typing import Any


_BEGIN_QUERY_JS = r"""
(input) => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden'
      && Number(s.opacity || 1) !== 0 && r.width > 2 && r.height > 2;
  };
  if (!window.__makroQuerySurfaceState) {
    window.__makroQuerySurfaceState = new WeakMap();
  }
  const baseline = new Map();
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const text = clean(el.innerText || el.textContent || '');
    baseline.set(el, text.slice(0, 500));
  }
  window.__makroQuerySurfaceState.set(input, {baseline});
  return baseline.size;
}
"""


_READ_ROWS_JS = r"""
(input) => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const label = (el) => {
    const raw = String(el && (el.innerText || el.textContent) || '').trim();
    if (!raw) return '';
    const lines = raw.split(/\n+/).map(clean).filter(Boolean);
    if (lines.length > 1 && !raw.includes('/')) return lines.join(' / ');
    return clean(raw);
  };
  const visible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden'
      && Number(s.opacity || 1) !== 0 && s.pointerEvents !== 'none'
      && r.width > 2 && r.height > 2;
  };
  const stateMap = window.__makroQuerySurfaceState;
  const state = stateMap && stateMap.get(input);
  if (!state || !state.baseline) return [];
  const baseline = state.baseline;
  const ir = input.getBoundingClientRect();

  const overlapX = (r) => Math.max(0, Math.min(r.right, ir.right) - Math.max(r.left, ir.left));
  const nearInput = (r) => {
    if (r.bottom < ir.bottom - 12 || r.top > ir.bottom + 760) return false;
    return overlapX(r) >= Math.min(ir.width * 0.20, Math.max(30, r.width * 0.35));
  };
  const topmost = (el, r) => {
    const x = Math.max(1, Math.min(innerWidth - 2, Math.max(r.left + 3, Math.min(r.right - 3, ir.left + Math.min(ir.width * .18, 80)))));
    const y = Math.max(1, Math.min(innerHeight - 2, r.top + Math.min(Math.max(r.height / 2, 3), r.height - 2)));
    const hit = document.elementFromPoint(x, y);
    return !!hit && (hit === el || el.contains(hit));
  };
  const changed = (el) => {
    const current = clean(el.innerText || el.textContent || '').slice(0, 500);
    return !baseline.has(el) || baseline.get(el) !== current;
  };
  const semantic = (el) => {
    const role = String(el.getAttribute && el.getAttribute('role') || '').toLowerCase();
    const cls = String(el.className || '').toLowerCase();
    return ['option','menuitem','listitem','row'].includes(role)
      || ['LI','TR','OPTION'].includes(el.tagName)
      || !!el.getAttribute?.('data-brand')
      || !!el.getAttribute?.('data-value')
      || /autocomplete|suggest|result|option|brand/.test(cls);
  };
  const explicitOwned = (el) => {
    for (const attr of ['aria-controls', 'aria-owns']) {
      const id = input.getAttribute(attr);
      const root = id && document.getElementById(id);
      if (root && root.contains(el)) return true;
    }
    return !!el.closest?.('[role="listbox"]');
  };
  const rowAncestor = (start) => {
    let el = start;
    for (let depth = 0; el && el !== document.body && depth < 8; depth++, el = el.parentElement) {
      if (el === input || el.contains(input) || !visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (!nearInput(r)) continue;
      const text = label(el);
      if (!text || text.length < 2 || text.length > 320) continue;

      const owned = explicitOwned(el);
      const wide = r.width >= Math.min(ir.width * .55, 360)
        && overlapX(r) >= Math.min(ir.width * .55, r.width * .70)
        && r.height >= 14 && r.height <= 110;
      if ((owned || semantic(el) || wide) && topmost(el, r)) return el;
    }
    return null;
  };

  const candidates = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || el === input || el.contains(input)) continue;
    if (!changed(el) && !explicitOwned(el)) continue;
    const r = el.getBoundingClientRect();
    if (!nearInput(r) && !semantic(el)) continue;
    const row = rowAncestor(el);
    if (row) candidates.push(row);
  }

  const rows = [];
  const seenElements = new Set();
  for (const row of candidates) {
    if (seenElements.has(row)) continue;
    seenElements.add(row);
    const r = row.getBoundingClientRect();
    rows.push({el: row, text: label(row), top: r.top, left: r.left});
  }
  rows.sort((a,b) => a.top - b.top || a.left - b.left);

  const out = [], seen = new Set();
  for (const item of rows) {
    const text = item.text;
    const key = text.toLocaleLowerCase();
    if (!text || seen.has(key)) continue;
    let duplicateChild = false;
    for (const child of item.el.querySelectorAll('*')) {
      if (child === item.el || !visible(child)) continue;
      if (label(child) === text && rowAncestor(child) === child) {
        duplicateChild = true;
        break;
      }
    }
    if (duplicateChild) continue;
    seen.add(key);
    out.push(text);
    if (out.length >= 80) break;
  }
  return out;
}
"""


_CLICK_ROW_JS = r"""
(input, wanted) => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const normalize = (v) => clean(v).toLocaleLowerCase();
  const wantedKey = normalize(wanted);
  if (!wantedKey) return {clicked:false, reason:'empty_wanted'};

  const stateMap = window.__makroQuerySurfaceState;
  const state = stateMap && stateMap.get(input);
  if (!state || !state.baseline) return {clicked:false, reason:'missing_query_state'};
  const baseline = state.baseline;
  const ir = input.getBoundingClientRect();

  const visible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden'
      && Number(s.opacity || 1) !== 0 && s.pointerEvents !== 'none'
      && r.width > 2 && r.height > 2;
  };
  const label = (el) => {
    const raw = String(el && (el.innerText || el.textContent) || '').trim();
    const lines = raw.split(/\n+/).map(clean).filter(Boolean);
    return lines.length > 1 && !raw.includes('/') ? lines.join(' / ') : clean(raw);
  };
  const overlapX = (r) => Math.max(0, Math.min(r.right, ir.right) - Math.max(r.left, ir.left));
  const nearInput = (r) => r.bottom >= ir.bottom - 12 && r.top <= ir.bottom + 760
    && overlapX(r) >= Math.min(ir.width * .20, Math.max(30, r.width * .35));
  const topmost = (el, r) => {
    const x = Math.max(1, Math.min(innerWidth - 2, Math.max(r.left + 3, Math.min(r.right - 3, ir.left + Math.min(ir.width * .18, 80)))));
    const y = Math.max(1, Math.min(innerHeight - 2, r.top + Math.min(Math.max(r.height / 2, 3), r.height - 2)));
    const hit = document.elementFromPoint(x, y);
    return !!hit && (hit === el || el.contains(hit));
  };
  const changed = (el) => {
    const current = clean(el.innerText || el.textContent || '').slice(0, 500);
    return !baseline.has(el) || baseline.get(el) !== current;
  };
  const explicitOwned = (el) => {
    for (const attr of ['aria-controls', 'aria-owns']) {
      const id = input.getAttribute(attr);
      const root = id && document.getElementById(id);
      if (root && root.contains(el)) return true;
    }
    return !!el.closest?.('[role="listbox"]');
  };
  const semantic = (el) => {
    const role = String(el.getAttribute && el.getAttribute('role') || '').toLowerCase();
    const cls = String(el.className || '').toLowerCase();
    return ['option','menuitem','listitem','row'].includes(role)
      || ['LI','TR','OPTION'].includes(el.tagName)
      || !!el.getAttribute?.('data-brand')
      || !!el.getAttribute?.('data-value')
      || /autocomplete|suggest|result|option|brand/.test(cls);
  };
  const actionable = (el) => {
    if (!el || !visible(el)) return false;
    const role = String(el.getAttribute?.('role') || '').toLowerCase();
    const style = getComputedStyle(el);
    return el.tagName === 'A' || el.tagName === 'BUTTON'
      || ['option','menuitem','button','listitem','row'].includes(role)
      || typeof el.onclick === 'function' || style.cursor === 'pointer';
  };
  const rowAncestor = (start) => {
    let el = start;
    for (let depth = 0; el && el !== document.body && depth < 8; depth++, el = el.parentElement) {
      if (el === input || el.contains(input) || !visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (!nearInput(r)) continue;
      const text = label(el);
      if (!text || text.length < 2 || text.length > 320) continue;
      const wide = r.width >= Math.min(ir.width * .55, 360)
        && overlapX(r) >= Math.min(ir.width * .55, r.width * .70)
        && r.height >= 14 && r.height <= 110;
      if ((explicitOwned(el) || semantic(el) || wide) && topmost(el, r)) return el;
    }
    return null;
  };

  const matches = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || el === input || el.contains(input)) continue;
    if (!changed(el) && !explicitOwned(el)) continue;
    const row = rowAncestor(el);
    if (!row || seen.has(row) || normalize(label(row)) !== wantedKey) continue;
    seen.add(row);
    matches.push(row);
  }
  if (matches.length !== 1) {
    return {clicked:false, reason:'non_unique_exact_row', match_count:matches.length};
  }

  // The exact query-owned row is the semantic boundary. We may use an actionable
  // wrapper only while that wrapper renders exactly the same row label. Never
  // climb into a larger result/list/container whose text no longer equals the
  // selected candidate: that can dispatch the wrong Vertical.
  let target = matches[0];
  for (let depth = 0; target && target !== document.body && depth < 5; depth++, target = target.parentElement) {
    if (target === input || target.contains(input) || !visible(target)) break;
    const targetText = label(target);
    if (normalize(targetText) !== wantedKey) break;
    if (!actionable(target)) continue;
    const role = String(target.getAttribute?.('role') || '').toLowerCase();
    const result = {
      clicked:true,
      strategy: depth === 0 ? 'exact_row' : 'same_label_wrapper',
      depth,
      row_label: label(matches[0]),
      target_label: targetText,
      target_tag: String(target.tagName || '').toLowerCase(),
      target_role: role,
      data_value: clean(target.getAttribute?.('data-value') || ''),
      data_vertical: clean(target.getAttribute?.('data-vertical') || ''),
      href: clean(target.getAttribute?.('href') || ''),
    };
    target.click();
    return result;
  }
  return {clicked:false, reason:'no_exact_action_target', row_label:label(matches[0])};
}
"""


def begin_search_query(search: Any) -> None:
    """Capture the visible pre-query DOM for one search input."""

    search.evaluate(_BEGIN_QUERY_JS)


def read_search_rows(search: Any) -> list[str]:
    """Return only rows owned by the query begun for ``search``."""

    try:
        raw = search.evaluate(_READ_ROWS_JS)
    except Exception:
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        value = " ".join(str(item or "").split()).strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def wait_for_search_rows(
    page: Any,
    search: Any,
    *,
    timeout_ms: int = 4000,
    poll_ms: int = 200,
) -> list[str]:
    """Boundedly wait for query-owned rows; never fall back to page-wide text."""

    timeout = max(0, int(timeout_ms))
    poll = max(50, int(poll_ms))
    attempts = max(1, timeout // poll)
    for _ in range(attempts):
        rows = read_search_rows(search)
        if rows:
            return rows
        page.wait_for_timeout(poll)
    return read_search_rows(search)


def click_search_row(search: Any, label: str) -> bool:
    """Click one exact query-owned row without escaping its semantic boundary."""

    try:
        raw = search.evaluate(_CLICK_ROW_JS, str(label or "").strip())
    except Exception:
        return False
    if isinstance(raw, dict):
        payload = {
            "event": "click_binding",
            "requested_label": str(label or "").strip(),
            **raw,
        }
        print(
            "MAKRO_VERTICAL_DIAG "
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
            flush=True,
        )
        return bool(raw.get("clicked"))
    return bool(raw)


__all__ = [
    "begin_search_query",
    "click_search_row",
    "read_search_rows",
    "wait_for_search_rows",
]
