from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from .listing import MakroListingTarget, parse_makro_listing_url
from .listing_creation import _vertical_search_input, is_brand_step, is_product_info_step


_BROWSE_MARKERS = (
    "Browse Verticals",
    "Browse Vertical",
    "浏览垂直栏目",
    "浏览垂直领域",
)


_SURFACE_JS = r"""({anchor, markers, limit}) => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity || 1) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0
      && r.left < innerWidth && r.top < innerHeight;
  };
  const markerKeys = new Set((markers || []).map((v) => clean(v).toLocaleLowerCase()));
  const ax = Number(anchor.x || 0);
  const ay = Number(anchor.y || 0);
  const aw = Number(anchor.width || 0);
  const ah = Number(anchor.height || 0);
  const anchorLeft = ax;
  const anchorRight = ax + aw;
  const anchorBottom = ay + ah;

  const markerCandidates = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const text = clean(el.innerText || el.textContent || '');
    if (!markerKeys.has(text.toLocaleLowerCase())) continue;
    const r = el.getBoundingClientRect();
    // A real Browse Verticals heading lives with the Step-1 search surface.
    // Sidebar/menu text to the left of the search box is never eligible.
    if (r.right < anchorLeft - 24) continue;
    if (r.top < anchorBottom - 120) continue;
    const childEcho = [...(el.children || [])].some((child) =>
      markerKeys.has(clean(child.innerText || child.textContent || '').toLocaleLowerCase())
    );
    if (childEcho) continue;
    markerCandidates.push({el, r, area: r.width * r.height});
  }
  markerCandidates.sort((a, b) => a.area - b.area || a.r.top - b.r.top || a.r.left - b.r.left);
  if (!markerCandidates.length) {
    return {
      marker_found: false,
      columns: [],
      anchor: {left: anchorLeft, right: anchorRight, bottom: anchorBottom},
      diagnostic: 'Browse Verticals marker not found beside the Step-1 search surface',
    };
  }

  const marker = markerCandidates[0];
  const mr = marker.r;
  const surfaceLeft = Math.max(0, Math.min(anchorLeft, mr.left) - 28);
  const surfaceTop = Math.max(anchorBottom - 20, mr.bottom - 12);

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
      if (!visible(el)) continue;
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
      if (!clickable && !rowish) continue;
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
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    // Hard ownership boundary: taxonomy pools must live in the central Step-1
    // content region, at or to the right of the search/Browse-Verticals anchor.
    if (r.left < surfaceLeft) continue;
    if (r.top < surfaceTop) continue;
    if (r.right > innerWidth - 4) continue;
    if (r.width < 48 || r.width > 390 || r.height < 24 || r.height > innerHeight * 0.86) continue;
    const items = itemElements(el);
    if (!items.length) continue;
    const clickableCount = items.filter((item) => item.clickable).length;
    if (clickableCount < 1) continue;
    const style = getComputedStyle(el);
    const scrollable = ['auto', 'scroll'].includes(style.overflowY)
      || el.scrollHeight > el.clientHeight + 12;
    pools.push({
      el,
      x: r.left,
      y: r.top,
      width: r.width,
      height: r.height,
      scrollable,
      clickableCount,
      items,
    });
  }

  const itemKey = (pool) => pool.items.map((item) => item.text).join('\u0001').toLocaleLowerCase();
  const dedupe = (input) => {
    const sorted = [...input].sort((a, b) =>
      a.x - b.x || a.y - b.y || b.items.length - a.items.length || b.height - a.height
    );
    const kept = [];
    for (const candidate of sorted) {
      let duplicate = false;
      for (const existing of kept) {
        const sameX = Math.abs(existing.x - candidate.x) < 14;
        const sameWidth = Math.abs(existing.width - candidate.width) < 30;
        const a = itemKey(existing);
        const b = itemKey(candidate);
        if (sameX && sameWidth && (a === b || a.includes(b) || b.includes(a))) {
          duplicate = true;
          if (candidate.items.length > existing.items.length
              || (candidate.items.length === existing.items.length && candidate.height > existing.height)) {
            Object.assign(existing, candidate);
          }
          break;
        }
      }
      if (!duplicate) kept.push(candidate);
    }
    return kept;
  };

  const available = dedupe(pools);
  const rootCandidates = available.filter((pool) => {
    if (pool.items.length < 2) return false;
    if (pool.x < surfaceLeft) return false;
    if (pool.x > Math.max(anchorLeft, mr.left) + 260) return false;
    if (pool.y > mr.bottom + 520) return false;
    return true;
  });
  rootCandidates.sort((a, b) => {
    const scoreA = Math.abs(a.x - Math.max(anchorLeft, mr.left)) * 4
      + Math.abs(a.y - mr.bottom)
      - Math.min(a.items.length, 60) * 2;
    const scoreB = Math.abs(b.x - Math.max(anchorLeft, mr.left)) * 4
      + Math.abs(b.y - mr.bottom)
      - Math.min(b.items.length, 60) * 2;
    return scoreA - scoreB;
  });
  if (!rootCandidates.length) {
    return {
      marker_found: true,
      columns: [],
      anchor: {left: anchorLeft, right: anchorRight, bottom: anchorBottom},
      marker: {left: mr.left, right: mr.right, top: mr.top, bottom: mr.bottom},
      diagnostic: 'Browse Verticals marker found but no taxonomy root column exists inside its Step-1 surface',
    };
  }

  const kept = [rootCandidates[0]];
  for (let depth = 0; depth < 7; depth++) {
    const rightmost = kept[kept.length - 1];
    const candidates = available.filter((pool) => {
      if (kept.includes(pool)) return false;
      if (pool.x <= rightmost.x + 24 || pool.x > rightmost.x + 420) return false;
      if (Math.abs(pool.y - rightmost.y) > 190 && Math.abs(pool.y - mr.bottom) > 260) return false;
      return !kept.some((existing) => Math.abs(existing.x - pool.x) < 16);
    });
    if (!candidates.length) break;
    const minX = Math.min(...candidates.map((pool) => pool.x));
    const sameColumn = candidates.filter((pool) => Math.abs(pool.x - minX) < 20);
    sameColumn.sort((a, b) => b.items.length - a.items.length || b.height - a.height || a.y - b.y);
    kept.push(sameColumn[0]);
  }

  return {
    marker_found: true,
    columns: kept.map((pool) => pool.items.map((item) => item.text)),
    anchor: {left: anchorLeft, right: anchorRight, bottom: anchorBottom},
    marker: {left: mr.left, right: mr.right, top: mr.top, bottom: mr.bottom},
    root: {
      left: kept[0].x,
      top: kept[0].y,
      width: kept[0].width,
      height: kept[0].height,
      item_count: kept[0].items.length,
    },
    diagnostic: '',
  };
}"""


_CLICK_JS = r"""({anchor, markers, limit, level, wanted}) => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity || 1) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0
      && r.left < innerWidth && r.top < innerHeight;
  };
  const markerKeys = new Set((markers || []).map((v) => clean(v).toLocaleLowerCase()));
  const ax = Number(anchor.x || 0);
  const ay = Number(anchor.y || 0);
  const aw = Number(anchor.width || 0);
  const ah = Number(anchor.height || 0);
  const anchorLeft = ax;
  const anchorBottom = ay + ah;

  const markerCandidates = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const text = clean(el.innerText || el.textContent || '');
    if (!markerKeys.has(text.toLocaleLowerCase())) continue;
    const r = el.getBoundingClientRect();
    if (r.right < anchorLeft - 24 || r.top < anchorBottom - 120) continue;
    const childEcho = [...(el.children || [])].some((child) =>
      markerKeys.has(clean(child.innerText || child.textContent || '').toLocaleLowerCase())
    );
    if (childEcho) continue;
    markerCandidates.push({el, r, area: r.width * r.height});
  }
  markerCandidates.sort((a, b) => a.area - b.area || a.r.top - b.r.top || a.r.left - b.r.left);
  if (!markerCandidates.length) return {ok: false, reason: 'marker_missing'};
  const mr = markerCandidates[0].r;
  const surfaceLeft = Math.max(0, Math.min(anchorLeft, mr.left) - 28);
  const surfaceTop = Math.max(anchorBottom - 20, mr.bottom - 12);

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
      if (!visible(el)) continue;
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
      if (!clickable && !rowish) continue;
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
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.left < surfaceLeft || r.top < surfaceTop || r.right > innerWidth - 4) continue;
    if (r.width < 48 || r.width > 390 || r.height < 24 || r.height > innerHeight * 0.86) continue;
    const items = itemElements(el);
    if (!items.length) continue;
    const clickableCount = items.filter((item) => item.clickable).length;
    if (clickableCount < 1) continue;
    pools.push({el, x: r.left, y: r.top, width: r.width, height: r.height, items});
  }

  const itemKey = (pool) => pool.items.map((item) => item.text).join('\u0001').toLocaleLowerCase();
  const dedupe = (input) => {
    const sorted = [...input].sort((a, b) =>
      a.x - b.x || a.y - b.y || b.items.length - a.items.length || b.height - a.height
    );
    const kept = [];
    for (const candidate of sorted) {
      let duplicate = false;
      for (const existing of kept) {
        const sameX = Math.abs(existing.x - candidate.x) < 14;
        const sameWidth = Math.abs(existing.width - candidate.width) < 30;
        const a = itemKey(existing);
        const b = itemKey(candidate);
        if (sameX && sameWidth && (a === b || a.includes(b) || b.includes(a))) {
          duplicate = true;
          if (candidate.items.length > existing.items.length
              || (candidate.items.length === existing.items.length && candidate.height > existing.height)) {
            Object.assign(existing, candidate);
          }
          break;
        }
      }
      if (!duplicate) kept.push(candidate);
    }
    return kept;
  };

  const available = dedupe(pools);
  const rootCandidates = available.filter((pool) =>
    pool.items.length >= 2
    && pool.x >= surfaceLeft
    && pool.x <= Math.max(anchorLeft, mr.left) + 260
    && pool.y <= mr.bottom + 520
  );
  rootCandidates.sort((a, b) => {
    const scoreA = Math.abs(a.x - Math.max(anchorLeft, mr.left)) * 4
      + Math.abs(a.y - mr.bottom) - Math.min(a.items.length, 60) * 2;
    const scoreB = Math.abs(b.x - Math.max(anchorLeft, mr.left)) * 4
      + Math.abs(b.y - mr.bottom) - Math.min(b.items.length, 60) * 2;
    return scoreA - scoreB;
  });
  if (!rootCandidates.length) return {ok: false, reason: 'root_missing'};

  const kept = [rootCandidates[0]];
  for (let depth = 0; depth < 7; depth++) {
    const rightmost = kept[kept.length - 1];
    const candidates = available.filter((pool) => {
      if (kept.includes(pool)) return false;
      if (pool.x <= rightmost.x + 24 || pool.x > rightmost.x + 420) return false;
      if (Math.abs(pool.y - rightmost.y) > 190 && Math.abs(pool.y - mr.bottom) > 260) return false;
      return !kept.some((existing) => Math.abs(existing.x - pool.x) < 16);
    });
    if (!candidates.length) break;
    const minX = Math.min(...candidates.map((pool) => pool.x));
    const sameColumn = candidates.filter((pool) => Math.abs(pool.x - minX) < 20);
    sameColumn.sort((a, b) => b.items.length - a.items.length || b.height - a.height || a.y - b.y);
    kept.push(sameColumn[0]);
  }

  if (level < 0 || level >= kept.length) return {ok: false, reason: 'level_missing'};
  const column = kept[level];
  const matches = column.items.filter((item) => clean(item.text) === clean(wanted));
  if (matches.length !== 1) return {ok: false, reason: 'node_not_unique'};
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
      return {ok: true, reason: ''};
    }
  }
  source.click();
  return {ok: true, reason: ''};
}"""


def parse_catalog_route(url: str) -> MakroListingTarget | None:
    """Return a validated Makro single-listing target or None for every other page."""

    try:
        return parse_makro_listing_url(str(url or ""))
    except (ValueError, AttributeError):
        return None


def is_fresh_catalog_step1_url(url: str) -> bool:
    """Fresh harvester root discovery must be the uncommitted Step-1 route."""

    target = parse_catalog_route(url)
    return bool(target is not None and not target.vertical and not target.brand)


def assert_catalog_probe_route(page: Page, *, allow_vertical: bool) -> MakroListingTarget:
    """Fail immediately if the dedicated probe ever leaves Add Single Listing."""

    target = parse_catalog_route(str(getattr(page, "url", "") or ""))
    if target is None:
        raise RuntimeError(
            "dedicated Makro catalog probe left the Add Single Listing route; "
            f"refusing to inspect or click outside Step 1: url={getattr(page, 'url', '')!r}"
        )
    if target.brand:
        raise RuntimeError("Makro catalog probe unexpectedly reached a brand-committed route")
    try:
        if is_product_info_step(page):
            raise RuntimeError("Makro catalog probe unexpectedly reached Step 3")
        if is_brand_step(page) and not (allow_vertical and target.vertical):
            raise RuntimeError("Makro catalog probe unexpectedly reached Step 2 without a leaf vertical")
    except RuntimeError:
        raise
    except Exception:
        pass
    if not allow_vertical and target.vertical:
        raise RuntimeError(
            f"Makro catalog root discovery expected fresh Step 1 but vertical={target.vertical!r} is already committed"
        )
    return target


class CatalogTaxonomyBrowser:
    """Read/click taxonomy only inside Step 1's Browse Verticals surface.

    Unlike the general production taxonomy reader, this harvester reader never
    scans the whole Makro page for arbitrary clickable columns. The visible
    Vertical search input and exact Browse Verticals heading jointly define the
    owned content region, which structurally excludes Dashboard/Orders sidebar
    controls from both discovery and clicking.
    """

    def __init__(self, page: Page) -> None:
        self.page = page
        self.last_diagnostic = ""

    def _search_anchor(self) -> dict[str, float]:
        search = _vertical_search_input(self.page)
        try:
            if not search.is_visible():
                raise RuntimeError("Makro Step 1 vertical search input is not visible")
        except Exception as exc:
            raise RuntimeError("Makro Step 1 vertical search input is not visibly operable") from exc
        box = search.bounding_box()
        if not box or float(box.get("width") or 0) < 120 or float(box.get("height") or 0) < 16:
            raise RuntimeError("Makro Step 1 vertical search input has no stable layout box")
        return {
            "x": float(box["x"]),
            "y": float(box["y"]),
            "width": float(box["width"]),
            "height": float(box["height"]),
        }

    def surface_snapshot(self, *, max_items_per_level: int = 200) -> dict[str, Any]:
        assert_catalog_probe_route(self.page, allow_vertical=False)
        anchor = self._search_anchor()
        try:
            raw = self.page.evaluate(
                _SURFACE_JS,
                {
                    "anchor": anchor,
                    "markers": list(_BROWSE_MARKERS),
                    "limit": int(max_items_per_level),
                },
            )
        except Exception as exc:
            raise RuntimeError("failed to inspect Makro Browse Verticals surface") from exc
        if not isinstance(raw, dict):
            raise RuntimeError("Makro Browse Verticals surface probe returned an invalid payload")
        self.last_diagnostic = str(raw.get("diagnostic") or "")
        return raw

    def columns(self, *, max_items_per_level: int = 200) -> list[list[str]]:
        snapshot = self.surface_snapshot(max_items_per_level=max_items_per_level)
        raw_columns = snapshot.get("columns") or []
        output: list[list[str]] = []
        for raw_column in raw_columns:
            if not isinstance(raw_column, list):
                continue
            values: list[str] = []
            seen: set[str] = set()
            for raw in raw_column:
                value = " ".join(str(raw or "").split()).strip()
                key = value.casefold()
                if not value or key in seen:
                    continue
                seen.add(key)
                values.append(value)
            if values:
                output.append(values)
        return output

    def ready(self, *, max_items_per_level: int = 200) -> bool:
        if not is_fresh_catalog_step1_url(str(getattr(self.page, "url", "") or "")):
            return False
        try:
            snapshot = self.surface_snapshot(max_items_per_level=max_items_per_level)
        except Exception:
            return False
        columns = snapshot.get("columns") or []
        return bool(snapshot.get("marker_found") and columns and columns[0])

    def click_node(self, level: int, text: str, *, max_items_per_level: int = 200) -> bool:
        assert_catalog_probe_route(self.page, allow_vertical=False)
        wanted = " ".join(str(text or "").split()).strip()
        if level < 0 or not wanted:
            return False
        anchor = self._search_anchor()
        try:
            result = self.page.evaluate(
                _CLICK_JS,
                {
                    "anchor": anchor,
                    "markers": list(_BROWSE_MARKERS),
                    "limit": int(max_items_per_level),
                    "level": int(level),
                    "wanted": wanted,
                },
            )
        except Exception as exc:
            raise RuntimeError("failed to click inside Makro Browse Verticals surface") from exc
        if not isinstance(result, dict):
            return False
        self.last_diagnostic = str(result.get("reason") or "")
        return bool(result.get("ok"))


__all__ = [
    "CatalogTaxonomyBrowser",
    "assert_catalog_probe_route",
    "is_fresh_catalog_step1_url",
    "parse_catalog_route",
]
