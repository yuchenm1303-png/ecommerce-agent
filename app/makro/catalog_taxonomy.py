from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from .listing import MakroListingTarget, parse_makro_listing_url
from .listing_creation import _vertical_search_input, is_brand_step, is_product_info_step


CATALOG_PROBE_STEP1_URL = (
    "https://seller.makro.co.za/index.html#dashboard/addListings/single"
)

# Exact, observed Step-1 content headings.  The first entry is the current live
# Makro heading confirmed in the Windows portal.  Older Browse Verticals labels
# are retained only as compatible surface aliases; generic progress-step text
# such as "SELECT VERTICAL" is deliberately not accepted because it can appear
# outside the taxonomy content surface.
STEP1_SURFACE_MARKERS = (
    "Select The Vertical For Your Product",
    "Browse Verticals",
    "Browse Vertical",
    "选择产品的垂直领域",
    "浏览垂直栏目",
    "浏览垂直领域",
)


_SURFACE_JS = r"""({anchor, markers, limit, click}) => {
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
  const anchorTop = ay;
  const anchorBottom = ay + ah;

  // Verify the exact Step-1 content surface near the vertical search control.
  // This is intentionally independent from sidebar/progress navigation text.
  const markerCandidates = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const text = clean(el.innerText || el.textContent || '');
    if (!markerKeys.has(text.toLocaleLowerCase())) continue;
    const r = el.getBoundingClientRect();
    if (r.right < anchorLeft - 48) continue;
    if (r.left > anchorRight + 520) continue;
    if (r.bottom < anchorTop - 280) continue;
    if (r.top > anchorBottom + 180) continue;
    const childEcho = [...(el.children || [])].some((child) =>
      markerKeys.has(clean(child.innerText || child.textContent || '').toLocaleLowerCase())
    );
    if (childEcho) continue;
    markerCandidates.push({el, r, text, area: r.width * r.height});
  }
  markerCandidates.sort((a, b) => {
    const da = Math.abs(a.r.left - anchorLeft) + Math.abs(a.r.bottom - anchorTop);
    const db = Math.abs(b.r.left - anchorLeft) + Math.abs(b.r.bottom - anchorTop);
    return da - db || a.area - b.area;
  });
  if (!markerCandidates.length) {
    return {
      marker_found: false,
      columns: [],
      diagnostic: 'Select Vertical content heading not found beside the Step-1 search surface',
    };
  }

  const marker = markerCandidates[0];
  const mr = marker.r;
  const surfaceLeft = Math.max(0, anchorLeft - 40);
  const surfaceTop = Math.max(anchorBottom - 12, mr.bottom - 8);

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
    // Hard ownership boundary: only the central Step-1 content below the search
    // control is eligible. Dashboard/Orders navigation is left of this region.
    if (r.left < surfaceLeft || r.top < surfaceTop) continue;
    if (r.right > innerWidth - 4) continue;
    if (r.width < 48 || r.width > 420 || r.height < 24 || r.height > innerHeight * 0.88) continue;
    const items = itemElements(el);
    if (!items.length) continue;
    const clickableCount = items.filter((item) => item.clickable).length;
    if (clickableCount < 1) continue;
    pools.push({
      el,
      x: r.left,
      y: r.top,
      width: r.width,
      height: r.height,
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
        const sameWidth = Math.abs(existing.width - candidate.width) < 32;
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
    if (pool.x > anchorLeft + 300) return false;
    if (pool.y > anchorBottom + 620) return false;
    return true;
  });
  rootCandidates.sort((a, b) => {
    const scoreA = Math.abs(a.x - anchorLeft) * 4
      + Math.abs(a.y - anchorBottom) - Math.min(a.items.length, 80) * 2;
    const scoreB = Math.abs(b.x - anchorLeft) * 4
      + Math.abs(b.y - anchorBottom) - Math.min(b.items.length, 80) * 2;
    return scoreA - scoreB;
  });
  if (!rootCandidates.length) {
    return {
      marker_found: true,
      marker_text: marker.text,
      columns: [],
      diagnostic: 'Select Vertical heading found but no taxonomy root column exists in its owned Step-1 surface',
    };
  }

  const kept = [rootCandidates[0]];
  for (let depth = 0; depth < 7; depth++) {
    const rightmost = kept[kept.length - 1];
    const candidates = available.filter((pool) => {
      if (kept.includes(pool)) return false;
      if (pool.x <= rightmost.x + 24 || pool.x > rightmost.x + 440) return false;
      if (Math.abs(pool.y - rightmost.y) > 210 && Math.abs(pool.y - anchorBottom) > 320) return false;
      return !kept.some((existing) => Math.abs(existing.x - pool.x) < 16);
    });
    if (!candidates.length) break;
    const minX = Math.min(...candidates.map((pool) => pool.x));
    const sameColumn = candidates.filter((pool) => Math.abs(pool.x - minX) < 20);
    sameColumn.sort((a, b) => b.items.length - a.items.length || b.height - a.height || a.y - b.y);
    kept.push(sameColumn[0]);
  }

  const columns = kept.map((pool) => pool.items.map((item) => item.text));
  if (!click) {
    return {
      marker_found: true,
      marker_text: marker.text,
      columns,
      diagnostic: '',
    };
  }

  const level = Number(click.level);
  const wanted = clean(click.wanted);
  if (!Number.isInteger(level) || level < 0 || level >= kept.length) {
    return {ok: false, reason: 'level_missing', marker_found: true, columns};
  }
  const column = kept[level];
  const matches = column.items.filter((item) => clean(item.text) === wanted);
  if (matches.length !== 1) {
    return {ok: false, reason: 'node_not_unique', marker_found: true, columns};
  }

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
      return {ok: true, reason: '', marker_found: true, columns};
    }
  }
  source.click();
  return {ok: true, reason: '', marker_found: true, columns};
}"""


def parse_catalog_route(url: str) -> MakroListingTarget | None:
    """Return a validated Makro single-listing target or None for every other page."""

    try:
        return parse_makro_listing_url(str(url or ""))
    except (ValueError, AttributeError):
        return None


def is_fresh_catalog_step1_url(url: str) -> bool:
    """Fresh harvester discovery requires an uncommitted Add Single Listing URL."""

    target = parse_catalog_route(url)
    return bool(target is not None and not target.vertical and not target.brand)


def assert_catalog_probe_route(page: Page, *, allow_vertical: bool) -> MakroListingTarget:
    """Fail immediately if the dedicated probe leaves Add Single Listing."""

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
    """Read/click taxonomy only inside the owned Step-1 Select Vertical surface."""

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

    def _surface(
        self,
        *,
        max_items_per_level: int,
        click: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert_catalog_probe_route(self.page, allow_vertical=False)
        anchor = self._search_anchor()
        try:
            raw = self.page.evaluate(
                _SURFACE_JS,
                {
                    "anchor": anchor,
                    "markers": list(STEP1_SURFACE_MARKERS),
                    "limit": int(max_items_per_level),
                    "click": click,
                },
            )
        except Exception as exc:
            raise RuntimeError("failed to inspect Makro Select Vertical taxonomy surface") from exc
        if not isinstance(raw, dict):
            raise RuntimeError("Makro Select Vertical surface probe returned an invalid payload")
        self.last_diagnostic = str(raw.get("diagnostic") or raw.get("reason") or "")
        return raw

    def surface_snapshot(self, *, max_items_per_level: int = 200) -> dict[str, Any]:
        return self._surface(max_items_per_level=max_items_per_level)

    def columns(self, *, max_items_per_level: int = 200) -> list[list[str]]:
        snapshot = self.surface_snapshot(max_items_per_level=max_items_per_level)
        output: list[list[str]] = []
        for raw_column in snapshot.get("columns") or []:
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
        wanted = " ".join(str(text or "").split()).strip()
        if level < 0 or not wanted:
            return False
        result = self._surface(
            max_items_per_level=max_items_per_level,
            click={"level": int(level), "wanted": wanted},
        )
        return bool(result.get("ok"))


__all__ = [
    "CATALOG_PROBE_STEP1_URL",
    "STEP1_SURFACE_MARKERS",
    "CatalogTaxonomyBrowser",
    "assert_catalog_probe_route",
    "is_fresh_catalog_step1_url",
    "parse_catalog_route",
]
