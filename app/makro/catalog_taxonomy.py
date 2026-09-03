from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from .listing import MakroListingTarget, parse_makro_listing_url
from .listing_creation import _vertical_search_input, is_brand_step, is_product_info_step


CATALOG_PROBE_STEP1_URL = (
    "https://seller.makro.co.za/index.html#dashboard/addListings/single"
)

# Public compatibility metadata only.  Taxonomy ownership no longer depends on
# heading text, language, CSS classes, screen coordinates, or fixed dimensions.
STEP1_SURFACE_MARKERS = (
    "Select The Vertical For Your Product",
    "Browse Verticals",
    "Browse Vertical",
    "选择产品的垂直领域",
    "浏览垂直栏目",
    "浏览垂直领域",
)

_REGISTRY_KEY = "__listingStudioMakroTaxonomyRegistryV3"


class TaxonomySurfaceError(RuntimeError):
    """The live Step-1 taxonomy surface could not be mechanically owned."""


_STRUCTURAL_SURFACE_JS = r"""(anchor, payload) => {{
  const REGISTRY_KEY = __REGISTRY_KEY_LITERAL__;
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const key = (value) => clean(value).toLocaleLowerCase();
  const rendered = (el) => {{
    if (!el || !(el instanceof Element) || !el.isConnected) return false;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || 1) === 0) return false;
    const rect = el.getBoundingClientRect();
    // Deliberately do not require viewport intersection. Rows clipped by an
    // internal taxonomy scroller are still real DOM evidence.
    return rect.width > 0 && rect.height > 0;
  }};
  const leafText = (el) => {{
    const text = clean(el.innerText || el.textContent || '');
    if (!text || text.length < 2 || text.length > 120) return '';
    for (const child of el.children || []) {{
      if (clean(child.innerText || child.textContent || '') === text) return '';
    }}
    return text;
  }};
  const isActionable = (el) => {{
    if (!el || !(el instanceof Element)) return false;
    const tag = String(el.tagName || '').toLocaleLowerCase();
    const role = key(el.getAttribute && el.getAttribute('role'));
    if (tag === 'button' || tag === 'a' || ['button','option','menuitem','treeitem'].includes(role)) return true;
    if (typeof el.onclick === 'function') return true;
    try {{ return getComputedStyle(el).cursor === 'pointer'; }} catch (_) {{ return false; }}
  }};
  const actionTarget = (source, stop) => {{
    let node = source;
    for (let depth = 0; depth < 10 && node && node instanceof Element && node !== stop; depth++, node = node.parentElement) {{
      if (isActionable(node)) return node;
    }}
    return null;
  }};
  const isExplicitList = (el) => {{
    if (!el || !(el instanceof Element)) return false;
    const tag = String(el.tagName || '').toLocaleLowerCase();
    const role = key(el.getAttribute && el.getAttribute('role'));
    return tag === 'ul' || tag === 'ol' || ['list','listbox','menu','tree'].includes(role);
  }};
  const isScrollOwner = (el) => {{
    if (!el || !(el instanceof Element) || el === document.body || el === document.documentElement) return false;
    const style = getComputedStyle(el);
    const overflowY = key(style.overflowY);
    return overflowY === 'auto' || overflowY === 'scroll' || el.scrollHeight > el.clientHeight + 4;
  }};
  const directBranchCount = (container) => {{
    let count = 0;
    for (const child of container.children || []) {{
      if (!rendered(child)) continue;
      let actionable = false;
      if (isActionable(child)) actionable = true;
      if (!actionable) {{
        const descendants = child.querySelectorAll('button,a,[role="button"],[role="option"],[role="menuitem"],[role="treeitem"]');
        actionable = [...descendants].some(rendered);
      }}
      if (actionable) count += 1;
      if (count >= 2) return count;
    }}
    return count;
  }};
  const groupOwner = (target, scope) => {{
    // Prefer an actual scroll/list owner. This is the strongest structural
    // contract and survives text/language/layout changes.
    let node = target.parentElement;
    for (let depth = 0; depth < 12 && node && node instanceof Element && node !== scope; depth++, node = node.parentElement) {{
      if (node.contains(anchor)) continue;
      if (isScrollOwner(node) || isExplicitList(node)) return node;
    }}
    // Some Makro generations render short columns as plain repeated sibling
    // containers. Bind the smallest repeated owner instead of guessing by pixels.
    node = target.parentElement;
    for (let depth = 0; depth < 8 && node && node instanceof Element && node !== scope; depth++, node = node.parentElement) {{
      if (node.contains(anchor)) continue;
      if (directBranchCount(node) >= 2) return node;
    }}
    return null;
  }};
  const followsAnchor = (el) => {{
    if (!el || el.contains(anchor)) return false;
    const position = anchor.compareDocumentPosition(el);
    return !!(position & Node.DOCUMENT_POSITION_FOLLOWING);
  }};
  const registry = (() => {{
    let current = window[REGISTRY_KEY];
    if (!current || current.version !== 3) {{
      current = {{version: 3, nextId: 1, ids: new WeakMap(), nodes: new Map(), rootId: '', scopeId: ''}};
      window[REGISTRY_KEY] = current;
    }}
    for (const [id, node] of [...current.nodes.entries()]) {{
      if (!node || !node.isConnected) current.nodes.delete(id);
    }}
    return current;
  }})();
  const idOf = (el) => {{
    let id = registry.ids.get(el);
    if (!id) {{
      id = `taxonomy-v3-${{registry.nextId++}}`;
      registry.ids.set(el, id);
    }}
    registry.nodes.set(id, el);
    return id;
  }};
  const nodeFor = (id) => {{
    const node = registry.nodes.get(String(id || ''));
    return node && node.isConnected ? node : null;
  }};
  const currentItems = (owner, scope, limit) => {{
    const out = [];
    const seen = new Set();
    for (const el of owner.querySelectorAll('*')) {{
      if (!rendered(el)) continue;
      const text = leafText(el);
      if (!text) continue;
      const target = actionTarget(el, owner);
      if (!target) continue;
      const actualOwner = groupOwner(target, scope);
      if (actualOwner !== owner) continue;
      const normalized = key(text);
      if (!normalized || seen.has(normalized)) continue;
      seen.add(normalized);
      out.push({{text, source: el, target}});
      if (out.length >= limit) break;
    }}
    return out;
  }};
  const discoverGroups = (scope, limit) => {{
    const owners = new Map();
    for (const el of scope.querySelectorAll('*')) {{
      if (!rendered(el)) continue;
      const text = leafText(el);
      if (!text) continue;
      const target = actionTarget(el, scope);
      if (!target) continue;
      const owner = groupOwner(target, scope);
      if (!owner || !rendered(owner) || owner.contains(anchor) || !followsAnchor(owner)) continue;
      if (!owners.has(owner)) owners.set(owner, []);
    }}
    const descriptors = [];
    let domOrder = 0;
    for (const owner of scope.querySelectorAll('*')) {{
      if (!owners.has(owner)) continue;
      const items = currentItems(owner, scope, limit);
      if (items.length < 1) continue;
      const ownerId = idOf(owner);
      const maxScroll = Math.max(0, Number(owner.scrollHeight || 0) - Number(owner.clientHeight || 0));
      descriptors.push({{
        group_id: ownerId,
        owner_id: ownerId,
        items: items.map((item) => item.text),
        scrollable: isScrollOwner(owner),
        scroll_top: Number(owner.scrollTop || 0),
        max_scroll: maxScroll,
        client_height: Number(owner.clientHeight || 0),
        dom_order: domOrder++,
        is_root: false,
      }});
    }}
    return descriptors;
  }};
  const chooseScope = (limit) => {{
    let fallback = null;
    let node = anchor.parentElement;
    for (let depth = 0; depth < 10 && node && node instanceof Element; depth++, node = node.parentElement) {{
      const groups = discoverGroups(node, limit);
      if (!groups.length) continue;
      fallback = {{scope: node, groups}};
      // The smallest ancestor containing the search plus at least one repeated
      // group is the structural Step-1 owner. A single-item transient group is
      // not strong enough to establish ownership.
      if (groups.some((group) => group.items.length >= 2)) return fallback;
    }}
    return fallback;
  }};

  const action = String(payload && payload.action || 'inspect');
  const limit = Math.max(8, Number(payload && payload.limit || 400));

  if (action === 'scroll') {{
    const owner = nodeFor(payload.owner_id);
    if (!owner) return {{ok: false, reason: 'owned_column_missing'}};
    const maxTop = Math.max(0, Number(owner.scrollHeight || 0) - Number(owner.clientHeight || 0));
    const before = Number(owner.scrollTop || 0);
    let target = before;
    const direction = String(payload.direction || '');
    if (direction === 'top') target = 0;
    else if (direction === 'next') {{
      const step = Math.max(1, Math.floor(Math.max(1, Number(owner.clientHeight || 1)) * 0.82));
      target = Math.min(maxTop, before + step);
    }} else return {{ok: false, reason: 'invalid_scroll_action'}};
    owner.scrollTop = target;
    try {{ owner.dispatchEvent(new Event('scroll', {{bubbles: true}})); }} catch (_) {{}}
    const after = Number(owner.scrollTop || 0);
    return {{
      ok: true,
      found: true,
      moved: Math.abs(after - before) > 0.5,
      at_end: after >= maxTop - 1,
      scroll_top: after,
      max_scroll: maxTop,
    }};
  }}

  const chosen = chooseScope(limit);
  if (!chosen || !chosen.scope || !chosen.groups.length) {{
    return {{ok: false, reason: 'taxonomy_structure_not_found', diagnostic: 'no structural taxonomy group follows the live Vertical search input'}};
  }}
  const scope = chosen.scope;
  let groups = chosen.groups;
  registry.scopeId = idOf(scope);

  const liveIds = new Set(groups.map((group) => group.group_id));
  if (!registry.rootId || !liveIds.has(registry.rootId)) {{
    const strong = groups.filter((group) => group.items.length >= 2);
    if (!strong.length) {{
      return {{ok: false, reason: 'taxonomy_root_not_stable', diagnostic: 'taxonomy groups exist but no repeated root group is stable'}};
    }}
    const scrollable = strong.filter((group) => group.scrollable);
    registry.rootId = (scrollable[0] || strong[0]).group_id;
  }}
  groups = groups.map((group) => ({{...group, is_root: group.group_id === registry.rootId}}));
  groups.sort((a, b) => Number(b.is_root) - Number(a.is_root) || a.dom_order - b.dom_order);

  if (action === 'click') {{
    const groupId = String(payload.group_id || '');
    const owner = nodeFor(groupId);
    if (!owner) return {{ok: false, reason: 'owned_column_missing', groups}};
    const wanted = clean(payload.wanted);
    const matches = currentItems(owner, scope, limit).filter((item) => clean(item.text) === wanted);
    if (matches.length !== 1) return {{ok: false, reason: 'node_not_unique_in_owned_column', groups}};
    const item = matches[0];
    try {{ item.source.scrollIntoView({{block: 'nearest', inline: 'nearest'}}); }} catch (_) {{}}
    item.target.click();
    return {{ok: true, reason: '', groups}};
  }}

  return {{
    ok: true,
    reason: '',
    root_group_id: registry.rootId,
    scope_id: registry.scopeId,
    groups,
    diagnostic: '',
  }};
}}""".replace("__REGISTRY_KEY_LITERAL__", repr(_REGISTRY_KEY)).replace("{{", "{").replace("}}", "}")


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


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


class CatalogTaxonomyBrowser:
    """Own the live Step-1 taxonomy structurally from the exact search control."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.last_diagnostic = ""

    def _search(self):
        search = _vertical_search_input(self.page)
        try:
            if not search.is_visible():
                raise TaxonomySurfaceError("Makro Step 1 Vertical Search input is not visible")
        except TaxonomySurfaceError:
            raise
        except Exception as exc:
            raise TaxonomySurfaceError("Makro Step 1 Vertical Search input is not visibly operable") from exc
        return search

    def _evaluate(self, payload: dict[str, Any], *, allow_vertical: bool) -> dict[str, Any]:
        assert_catalog_probe_route(self.page, allow_vertical=allow_vertical)
        search = self._search()
        try:
            raw = search.evaluate(_STRUCTURAL_SURFACE_JS, payload)
        except Exception as exc:
            raise TaxonomySurfaceError("failed to inspect the structural Makro taxonomy owner") from exc
        if not isinstance(raw, dict):
            raise TaxonomySurfaceError("Makro taxonomy structural probe returned an invalid payload")
        self.last_diagnostic = str(raw.get("diagnostic") or raw.get("reason") or "")
        if not raw.get("ok"):
            raise TaxonomySurfaceError(
                "Makro taxonomy structural ownership failed: "
                + (self.last_diagnostic or "unknown structural failure")
            )
        return raw

    def column_descriptors(self, *, max_items_per_level: int = 400) -> list[dict[str, Any]]:
        raw = self._evaluate(
            {"action": "inspect", "limit": max(8, int(max_items_per_level))},
            allow_vertical=True,
        )
        output: list[dict[str, Any]] = []
        for entry in raw.get("groups") or []:
            if not isinstance(entry, dict):
                continue
            values: list[str] = []
            seen: set[str] = set()
            for item in entry.get("items") or []:
                value = _clean(item)
                normalized = value.casefold()
                if not value or normalized in seen:
                    continue
                seen.add(normalized)
                values.append(value)
            group_id = _clean(entry.get("group_id"))
            owner_id = _clean(entry.get("owner_id")) or group_id
            if not group_id or not owner_id or not values:
                continue
            output.append(
                {
                    "group_id": group_id,
                    "owner_id": owner_id,
                    "items": values,
                    "scrollable": bool(entry.get("scrollable")),
                    "scroll_top": float(entry.get("scroll_top") or 0.0),
                    "max_scroll": float(entry.get("max_scroll") or 0.0),
                    "client_height": float(entry.get("client_height") or 0.0),
                    "dom_order": int(entry.get("dom_order") or 0),
                    "is_root": bool(entry.get("is_root")),
                }
            )
        if not output:
            raise TaxonomySurfaceError("Makro taxonomy owner is present but exposes no live category groups")
        roots = [entry for entry in output if entry["is_root"]]
        if len(roots) != 1:
            raise TaxonomySurfaceError(
                f"Makro taxonomy structural probe exposed {len(roots)} root groups; expected exactly one"
            )
        return output

    def scroll_owned_column(self, owner_id: str, action: str) -> dict[str, Any]:
        direction = str(action or "").strip().casefold()
        if direction not in {"top", "next"}:
            raise ValueError("taxonomy scroll action must be top or next")
        raw = self._evaluate(
            {
                "action": "scroll",
                "owner_id": _clean(owner_id),
                "direction": direction,
                "limit": 8,
            },
            allow_vertical=True,
        )
        return {
            "found": bool(raw.get("found", True)),
            "moved": bool(raw.get("moved")),
            "at_end": bool(raw.get("at_end")),
            "scroll_top": float(raw.get("scroll_top") or 0.0),
            "max_scroll": float(raw.get("max_scroll") or 0.0),
        }

    def click_owned_node(
        self,
        group_id: str,
        text: str,
        *,
        max_items_per_level: int = 400,
    ) -> bool:
        wanted = _clean(text)
        if not group_id or not wanted:
            return False
        try:
            raw = self._evaluate(
                {
                    "action": "click",
                    "group_id": _clean(group_id),
                    "wanted": wanted,
                    "limit": max(8, int(max_items_per_level)),
                },
                allow_vertical=False,
            )
        except TaxonomySurfaceError:
            return False
        return bool(raw.get("ok"))

    def surface_snapshot(self, *, max_items_per_level: int = 400) -> dict[str, Any]:
        descriptors = self.column_descriptors(max_items_per_level=max_items_per_level)
        return {
            "marker_found": True,
            "marker_text": "",
            "root_group_id": next(entry["group_id"] for entry in descriptors if entry["is_root"]),
            "columns": [list(entry["items"]) for entry in descriptors],
            "descriptors": descriptors,
            "diagnostic": self.last_diagnostic,
        }

    def columns(self, *, max_items_per_level: int = 400) -> list[list[str]]:
        return [
            list(entry["items"])
            for entry in self.column_descriptors(max_items_per_level=max_items_per_level)
        ]

    def ready(self, *, max_items_per_level: int = 400) -> bool:
        if not is_fresh_catalog_step1_url(str(getattr(self.page, "url", "") or "")):
            return False
        try:
            return bool(self.column_descriptors(max_items_per_level=max_items_per_level))
        except Exception:
            return False

    def click_node(self, level: int, text: str, *, max_items_per_level: int = 400) -> bool:
        wanted = _clean(text)
        if int(level) < 0 or not wanted:
            return False
        try:
            descriptors = self.column_descriptors(max_items_per_level=max_items_per_level)
        except TaxonomySurfaceError:
            return False
        index = int(level)
        if index >= len(descriptors):
            return False
        return self.click_owned_node(
            str(descriptors[index]["group_id"]),
            wanted,
            max_items_per_level=max_items_per_level,
        )


__all__ = [
    "CATALOG_PROBE_STEP1_URL",
    "STEP1_SURFACE_MARKERS",
    "CatalogTaxonomyBrowser",
    "TaxonomySurfaceError",
    "assert_catalog_probe_route",
    "is_fresh_catalog_step1_url",
    "parse_catalog_route",
]
