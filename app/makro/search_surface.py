"""Query-owned Makro search/result surface mechanics.

A search result belongs to the query generation that caused it, not merely to a
rectangle below an input. Makro may keep autocomplete/taxonomy DOM mounted while
reusing the same nodes across several searches, so waiting for the old DOM to
vanish is neither necessary nor reliable.

Each call to :func:`begin_search_query` starts a new generation. It snapshots the
currently visible DOM and observes mutations produced after that boundary.
Discovery reads accept only rows that are new, changed, or touched by the current
generation. Exact replay is deliberately separate: once a candidate has already
been grounded by a prior discovery generation, the caller may allow one stable
exact row to be rebound and clicked, after which the normal canonical-Vertical
verification remains authoritative.
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
  const mark = (set, node) => {
    let el = node instanceof Element ? node : node && node.parentElement;
    for (let depth = 0; el && el !== document.body && depth < 10; depth++, el = el.parentElement) {
      set.add(el);
    }
  };

  if (!window.__makroQuerySurfaceState) {
    window.__makroQuerySurfaceState = new WeakMap();
  }
  const stateMap = window.__makroQuerySurfaceState;
  const previous = stateMap.get(input);
  if (previous && previous.observer) {
    try { previous.observer.disconnect(); } catch (_) {}
  }

  const generation = Number(previous && previous.generation || 0) + 1;
  const baseline = new Map();
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const text = clean(el.innerText || el.textContent || '');
    baseline.set(el, text.slice(0, 500));
  }

  const touched = new WeakSet();
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      mark(touched, record.target);
      for (const node of record.addedNodes || []) mark(touched, node);
      for (const node of record.removedNodes || []) mark(touched, node);
    }
  });
  const root = document.body || document.documentElement;
  if (root) {
    observer.observe(root, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: [
        'class', 'style', 'aria-hidden', 'aria-selected', 'aria-expanded',
        'data-value', 'data-vertical', 'data-brand'
      ],
    });
  }

  stateMap.set(input, {generation, baseline, touched, observer});
  return {generation, baseline_size: baseline.size};
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
  const touched = state.touched;
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
  const fresh = (el) => changed(el) || !!(touched && touched.has(el));
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
    // Explicit ownership says where a row lives; it does not prove that the row
    // belongs to this generation. Freshness is always required for discovery.
    if (!fresh(el)) continue;
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


_MOVE_SEARCH_SURFACE_JS = r"""
(input, reset) => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden'
      && Number(s.opacity || 1) !== 0 && r.width > 2 && r.height > 2;
  };
  const stateMap = window.__makroQuerySurfaceState;
  const state = stateMap && stateMap.get(input);
  if (!state || !state.baseline) {
    return {found:false,moved:false,at_end:true,reason:'missing_query_state'};
  }
  const baseline = state.baseline;
  const touched = state.touched;
  const ir = input.getBoundingClientRect();
  const overlapX = (r) => Math.max(0, Math.min(r.right, ir.right) - Math.max(r.left, ir.left));
  const nearInput = (r) => r.bottom >= ir.bottom - 12 && r.top <= ir.bottom + 760
    && overlapX(r) >= Math.min(ir.width * .20, Math.max(30, r.width * .35));
  const label = (el) => clean(el && (el.innerText || el.textContent) || '');
  const changed = (el) => {
    const current = label(el).slice(0, 500);
    return !baseline.has(el) || baseline.get(el) !== current;
  };
  const fresh = (el) => changed(el) || !!(touched && touched.has(el));
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
  const scrollable = (el) => {
    if (!visible(el)) return false;
    const s = getComputedStyle(el);
    return el.scrollHeight > el.clientHeight + 8
      && (['auto','scroll'].includes(s.overflowY) || el.scrollHeight > el.clientHeight + 20);
  };

  const ownedRows = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || el === input || el.contains(input)) continue;
    if (!fresh(el)) continue;
    const r = el.getBoundingClientRect();
    if (!nearInput(r)) continue;
    if (!semantic(el) && !explicitOwned(el)) continue;
    const text = label(el);
    if (!text || text.length < 2 || text.length > 320) continue;
    ownedRows.push(el);
  }

  const scored = new Map();
  const add = (el, score) => {
    if (!el || !scrollable(el)) return;
    scored.set(el, Math.max(scored.get(el) || 0, score));
  };
  for (const attr of ['aria-controls', 'aria-owns']) {
    const id = input.getAttribute(attr);
    add(id && document.getElementById(id), 100000);
  }
  for (const row of ownedRows) {
    add(row.closest?.('[role="listbox"]'), 90000);
    let parent = row.parentElement;
    for (let depth = 0; parent && parent !== document.body && depth < 10; depth++, parent = parent.parentElement) {
      if (scrollable(parent)) {
        add(parent, 50000 - depth * 100 + ownedRows.filter((x) => parent.contains(x)).length * 1000);
        break;
      }
    }
  }
  if (!scored.size) {
    for (const el of document.querySelectorAll('body *')) {
      if (!scrollable(el)) continue;
      const r = el.getBoundingClientRect();
      if (!nearInput(r) || r.width < Math.min(ir.width * .45, 260)) continue;
      const contained = ownedRows.filter((row) => el.contains(row)).length;
      if (contained) add(el, 10000 + contained * 1000);
    }
  }
  const ranked = [...scored.entries()].sort((a, b) => {
    if (b[1] !== a[1]) return b[1] - a[1];
    const ar = a[0].getBoundingClientRect(), br = b[0].getBoundingClientRect();
    return ar.width * ar.height - br.width * br.height;
  });
  if (!ranked.length) {
    return {found:false,moved:false,at_end:true,reason:'no_query_scroll_surface'};
  }

  const surface = ranked[0][0];
  const before = Number(surface.scrollTop || 0);
  const maxScroll = Math.max(0, Number(surface.scrollHeight || 0) - Number(surface.clientHeight || 0));
  const desired = reset
    ? 0
    : Math.min(maxScroll, before + Math.max(80, Number(surface.clientHeight || 0) * 0.78));
  surface.scrollTop = desired;
  surface.dispatchEvent(new Event('scroll', {bubbles:true}));
  const after = Number(surface.scrollTop || 0);
  return {
    found:true,
    moved:Math.abs(after - before) > 1,
    at_end:maxScroll <= 1 || after >= maxScroll - 2,
    scroll_top:after,
    max_scroll:maxScroll,
    scroll_height:Number(surface.scrollHeight || 0),
    client_height:Number(surface.clientHeight || 0),
  };
}
"""


_CLICK_ROW_JS = r"""
async (input, request) => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const normalize = (v) => clean(v).toLocaleLowerCase();
  const wanted = typeof request === 'string' ? request : request && request.wanted;
  const allowStableExact = !!(request && typeof request === 'object' && request.allow_stable_exact);
  const wantedKey = normalize(wanted);
  if (!wantedKey) return {clicked:false, reason:'empty_wanted'};

  const stateMap = window.__makroQuerySurfaceState;
  const state = stateMap && stateMap.get(input);
  if (!state || !state.baseline) return {clicked:false, reason:'missing_query_state'};
  const baseline = state.baseline;
  const touched = state.touched;
  const generation = Number(state.generation || 0);
  const ir = input.getBoundingClientRect();
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
  const fresh = (el) => changed(el) || !!(touched && touched.has(el));
  const eligible = (el) => fresh(el) || allowStableExact;
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
  const scrollable = (el) => {
    if (!visible(el)) return false;
    const s = getComputedStyle(el);
    return el.scrollHeight > el.clientHeight + 8
      && (['auto','scroll'].includes(s.overflowY) || el.scrollHeight > el.clientHeight + 20);
  };
  const exactRows = () => {
    const matches = [], seen = new Set();
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el) || el === input || el.contains(input)) continue;
      if (!eligible(el)) continue;
      const row = rowAncestor(el);
      if (!row || seen.has(row) || normalize(label(row)) !== wantedKey) continue;
      // Stable replay is only allowed for the already-grounded exact label; the
      // geometry/topmost/row constraints above still prevent page-wide clicks.
      if (!fresh(row) && !allowStableExact) continue;
      seen.add(row);
      matches.push(row);
    }
    return matches;
  };
  const visibleOwnedRows = () => {
    const rows = [];
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el) || el === input || el.contains(input)) continue;
      if (!eligible(el)) continue;
      const r = el.getBoundingClientRect();
      if (!nearInput(r) || (!semantic(el) && !explicitOwned(el))) continue;
      rows.push(el);
    }
    return rows;
  };
  const findScroller = () => {
    const rows = visibleOwnedRows();
    const scored = new Map();
    const add = (el, score) => {
      if (!el || !scrollable(el)) return;
      scored.set(el, Math.max(scored.get(el) || 0, score));
    };
    for (const attr of ['aria-controls', 'aria-owns']) {
      const id = input.getAttribute(attr);
      add(id && document.getElementById(id), 100000);
    }
    for (const row of rows) {
      add(row.closest?.('[role="listbox"]'), 90000);
      let parent = row.parentElement;
      for (let depth = 0; parent && parent !== document.body && depth < 10; depth++, parent = parent.parentElement) {
        if (scrollable(parent)) {
          add(parent, 50000 - depth * 100 + rows.filter((x) => parent.contains(x)).length * 1000);
          break;
        }
      }
    }
    const ranked = [...scored.entries()].sort((a, b) => b[1] - a[1]);
    return ranked.length ? ranked[0][0] : null;
  };
  const tryClick = (seekStep) => {
    const matches = exactRows();
    if (matches.length !== 1) {
      return {
        clicked:false,
        reason:'non_unique_exact_row',
        match_count:matches.length,
        seek_step:seekStep,
        generation,
        allow_stable_exact:allowStableExact,
      };
    }
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
        seek_step:seekStep,
        generation,
        allow_stable_exact:allowStableExact,
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
    return {
      clicked:false,
      reason:'no_exact_action_target',
      row_label:label(matches[0]),
      seek_step:seekStep,
      generation,
      allow_stable_exact:allowStableExact,
    };
  };

  let surface = findScroller();
  if (surface) {
    surface.scrollTop = 0;
    surface.dispatchEvent(new Event('scroll', {bubbles:true}));
    await pause(80);
  }
  let last = {clicked:false, reason:'exact_row_not_found', generation, allow_stable_exact:allowStableExact};
  for (let step = 0; step < 64; step++) {
    last = tryClick(step);
    if (last.clicked) return last;
    surface = findScroller() || surface;
    if (!surface || !scrollable(surface)) return last;
    const maxScroll = Math.max(0, surface.scrollHeight - surface.clientHeight);
    const before = Number(surface.scrollTop || 0);
    if (maxScroll <= 1 || before >= maxScroll - 2) {
      return {...last, reason:'exact_row_not_found_after_full_scroll', reached_end:true};
    }
    const next = Math.min(maxScroll, before + Math.max(80, surface.clientHeight * 0.78));
    surface.scrollTop = next;
    surface.dispatchEvent(new Event('scroll', {bubbles:true}));
    await pause(90);
  }
  return {...last, reason:'exact_row_seek_budget_exhausted'};
}
"""


def begin_search_query(search: Any) -> int:
    """Start a new DOM-ownership generation for one search input."""

    try:
        raw = search.evaluate(_BEGIN_QUERY_JS)
    except Exception:
        return 0
    if isinstance(raw, dict):
        try:
            return int(raw.get("generation") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def read_search_rows(search: Any) -> list[str]:
    """Return visible rows proven fresh in the active query generation."""

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


def _move_search_surface(search: Any, *, reset: bool = False) -> dict[str, Any]:
    try:
        raw = search.evaluate(_MOVE_SEARCH_SURFACE_JS, bool(reset))
    except Exception:
        return {"found": False, "moved": False, "at_end": True, "reason": "evaluate_failed"}
    return dict(raw) if isinstance(raw, dict) else {
        "found": False,
        "moved": False,
        "at_end": True,
        "reason": "invalid_scroll_state",
    }


def _append_unique_rows(output: list[str], seen: set[str], rows: list[str]) -> int:
    added = 0
    for raw in rows:
        value = " ".join(str(raw or "").split()).strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        output.append(value)
        added += 1
    return added


def harvest_search_rows(
    page: Any,
    search: Any,
    *,
    poll_ms: int = 160,
    max_scroll_steps: int = 64,
    max_stagnant_rounds: int = 3,
) -> list[str]:
    """Harvest every row proven fresh in the active query generation.

    The dropdown is reset to its top before harvesting and restored there before
    returning. Virtual lists are supported because newly mounted/reused rows are
    marked by the generation observer as the result surface moves. End-of-list is
    confirmed across two quiet polls so a slower concurrent Batch tab cannot make
    a partially rendered result set look complete.
    """

    poll = max(50, int(poll_ms))
    output: list[str] = []
    seen: set[str] = set()
    _move_search_surface(search, reset=True)
    page.wait_for_timeout(poll)
    _append_unique_rows(output, seen, read_search_rows(search))

    stagnant = 0
    end_confirmations = 0
    for _ in range(max(1, int(max_scroll_steps))):
        state = _move_search_surface(search, reset=False)
        found = bool(state.get("found"))
        moved = bool(state.get("moved"))
        at_end = bool(state.get("at_end"))

        if moved or at_end or not found:
            page.wait_for_timeout(poll)
        added = _append_unique_rows(output, seen, read_search_rows(search))
        stagnant = 0 if added else stagnant + 1

        if not found:
            # A short result list may not expose a scrollable surface at all.
            # Require more than one quiet observation before treating it as done.
            if stagnant >= 2:
                break
            continue

        if at_end:
            end_confirmations = 0 if added else end_confirmations + 1
            if end_confirmations >= 2:
                break
            continue

        end_confirmations = 0
        if stagnant >= max(1, int(max_stagnant_rounds)):
            break

    _move_search_surface(search, reset=True)
    page.wait_for_timeout(poll)
    _append_unique_rows(output, seen, read_search_rows(search))
    return output


def wait_for_search_rows(
    page: Any,
    search: Any,
    *,
    timeout_ms: int = 4000,
    poll_ms: int = 200,
) -> list[str]:
    """Wait for fresh rows, then return the generation's complete harvested set."""

    timeout = max(0, int(timeout_ms))
    poll = max(50, int(poll_ms))
    attempts = max(1, timeout // poll)
    for _ in range(attempts):
        rows = read_search_rows(search)
        if rows:
            return harvest_search_rows(page, search, poll_ms=min(poll, 180))
        page.wait_for_timeout(poll)
    rows = read_search_rows(search)
    if not rows:
        return []
    return harvest_search_rows(page, search, poll_ms=min(poll, 180))


def click_search_row(
    search: Any,
    label: str,
    *,
    allow_stable_exact: bool = False,
) -> bool:
    """Seek and click one exact row across the active result surface.

    ``allow_stable_exact`` is reserved for replay of a candidate that was already
    grounded by a previous discovery generation. It never broadens the requested
    label and the caller must independently verify the resulting canonical state.
    """

    requested = str(label or "").strip()
    try:
        raw = search.evaluate(
            _CLICK_ROW_JS,
            {
                "wanted": requested,
                "allow_stable_exact": bool(allow_stable_exact),
            },
        )
    except Exception:
        return False
    if isinstance(raw, dict):
        payload = {
            "event": "click_binding",
            "requested_label": requested,
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
    "harvest_search_rows",
    "read_search_rows",
    "wait_for_search_rows",
]
