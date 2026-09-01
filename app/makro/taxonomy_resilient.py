"""Production wrapper for the owned Makro Step 1 taxonomy surface.

Makro exposes two different Step-1 mechanisms at once: free-text Vertical search
and the Browse Verticals tree.  The catalog sensor remains the single DOM owner for
that tree, but production traversal must additionally own three mechanical state
transitions that the raw sensor cannot infer by itself:

* search mode must be reset before Browse traversal starts;
* only taxonomy columns created by our own parent clicks may become deeper levels;
* scrollable taxonomy columns must be exhausted, not sampled only from the current
  viewport.

Those rules keep product semantics in AI while making Python responsible only for
browser state, scroll enumeration, exact-node ownership and bounded rebinding.
Product cards or other stable Step-1 content that happen to look column-like are
never promoted into the taxonomy merely because they are visible to the raw sensor.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from .catalog_taxonomy import CatalogTaxonomyBrowser
from .listing_creation import _vertical_search_input


_SCROLL_COLUMN_JS = r"""({seed, anchor, action}) => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || 1) === 0) {
      return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0
      && r.left < innerWidth && r.top < innerHeight;
  };
  const leafText = (el) => {
    const value = clean(el.innerText || el.textContent || '');
    if (!value) return '';
    for (const child of el.children || []) {
      if (clean(child.innerText || child.textContent || '') === value) return '';
    }
    return value;
  };
  const scrollOwner = (source) => {
    let node = source;
    for (let depth = 0; depth < 10 && node && node instanceof Element; depth++, node = node.parentElement) {
      const r = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      const overflowY = String(style.overflowY || '').toLocaleLowerCase();
      const scrollable = node.scrollHeight > node.clientHeight + 8 || overflowY === 'auto' || overflowY === 'scroll';
      if (!scrollable) continue;
      if (r.width < 60 || r.width > 460 || r.height < 60 || r.height > innerHeight * 0.9) continue;
      if (r.left < Number(anchor.x || 0) - 60 || r.right > innerWidth - 2) continue;
      if (r.top < Number(anchor.bottom || 0) - 40) continue;
      return node;
    }
    return null;
  };

  const wanted = clean(seed).toLocaleLowerCase();
  if (!wanted) return {found: false, moved: false, at_end: true};
  const candidates = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    if (leafText(el).toLocaleLowerCase() !== wanted) continue;
    const owner = scrollOwner(el);
    if (!owner) continue;
    const r = owner.getBoundingClientRect();
    candidates.push({owner, r});
  }
  if (!candidates.length) return {found: false, moved: false, at_end: true};
  candidates.sort((a, b) => a.r.left - b.r.left || a.r.top - b.r.top || a.r.width - b.r.width);
  const owner = candidates[0].owner;
  const maxTop = Math.max(0, owner.scrollHeight - owner.clientHeight);
  const before = Number(owner.scrollTop || 0);
  let target = before;
  if (action === 'top') {
    target = 0;
  } else if (action === 'next') {
    const step = Math.max(80, Math.floor(owner.clientHeight * 0.72));
    target = Math.min(maxTop, before + step);
  }
  owner.scrollTop = target;
  const after = Number(owner.scrollTop || 0);
  return {
    found: true,
    moved: Math.abs(after - before) > 1,
    at_end: after >= maxTop - 1,
    scroll_top: after,
    max_top: maxTop,
  };
}"""


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _key(value: object) -> str:
    return _clean(value).casefold()


def _signature(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_key(value) for value in values if _key(value))


def _merge_unique(target: list[str], values: list[str], *, limit: int) -> None:
    seen = {_key(value) for value in target if _key(value)}
    for raw in values:
        value = _clean(raw)
        key = _key(value)
        if not value or not key or key in seen:
            continue
        target.append(value)
        seen.add(key)
        if len(target) >= max(1, int(limit)):
            break


class ResilientMakroTaxonomyBrowser:
    """Expose one scroll-complete, click-owned logical taxonomy tree."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self._owned = CatalogTaxonomyBrowser(page)
        self._browse_prepared = False
        self._clicked_depth = -1
        self._stale_after_parent: dict[int, set[tuple[str, ...]]] = {}
        self._logical_raw_indices: list[int] = []

    @property
    def last_diagnostic(self) -> str:
        return str(getattr(self._owned, "last_diagnostic", "") or "")

    def _wait(self, milliseconds: int = 120) -> None:
        try:
            self.page.wait_for_timeout(max(1, int(milliseconds)))
        except Exception:
            pass

    def _prepare_browse(self) -> None:
        if self._browse_prepared:
            return
        search = _vertical_search_input(self.page)
        try:
            search.fill("")
        except Exception as exc:
            raise RuntimeError("Makro Step 1 could not clear Vertical Search before Browse fallback") from exc
        try:
            search.press("Escape")
        except Exception:
            pass
        try:
            search.evaluate("el => el.blur()")
        except Exception:
            pass
        self._wait(180)
        self._browse_prepared = True

    def _raw_columns(self, *, max_items_per_level: int) -> list[list[str]]:
        try:
            return self._owned.columns(max_items_per_level=int(max_items_per_level))
        except Exception:
            return []

    def _search_anchor(self) -> dict[str, float]:
        search = _vertical_search_input(self.page)
        box = search.bounding_box() or {}
        x = float(box.get("x") or 0)
        y = float(box.get("y") or 0)
        height = float(box.get("height") or 0)
        return {"x": x, "bottom": y + height}

    def _scroll_column(self, seed: str, action: str) -> dict[str, Any]:
        try:
            raw = self.page.evaluate(
                _SCROLL_COLUMN_JS,
                {"seed": _clean(seed), "anchor": self._search_anchor(), "action": str(action)},
            )
        except Exception:
            return {"found": False, "moved": False, "at_end": True}
        return raw if isinstance(raw, dict) else {"found": False, "moved": False, "at_end": True}

    def _harvest_physical_column(
        self,
        raw_index: int,
        seed_values: list[str],
        *,
        max_items_per_level: int,
    ) -> list[str]:
        """Enumerate one owned taxonomy scroller from top to bottom and restore it."""

        output: list[str] = []
        initial = [_clean(value) for value in seed_values if _clean(value)]
        if not initial:
            return output

        top_state = self._scroll_column(initial[0], "top")
        if top_state.get("found"):
            self._wait(90)

        last_chunk: list[str] = initial
        for _ in range(32):
            raw = self._raw_columns(max_items_per_level=max_items_per_level)
            if raw_index >= len(raw):
                break
            chunk = [_clean(value) for value in raw[raw_index] if _clean(value)]
            if not chunk:
                break
            last_chunk = chunk
            _merge_unique(output, chunk, limit=max_items_per_level)
            if len(output) >= max(1, int(max_items_per_level)):
                break

            state = self._scroll_column(chunk[-1], "next")
            if not state.get("found") or not state.get("moved"):
                break
            self._wait(90)

        restore_seed = last_chunk[0] if last_chunk else initial[0]
        restore = self._scroll_column(restore_seed, "top")
        if restore.get("found"):
            self._wait(90)
        return output or initial

    def _next_physical_index(
        self,
        raw: list[list[str]],
        *,
        depth: int,
        after_index: int,
    ) -> int | None:
        blocked = self._stale_after_parent.get(int(depth), set())
        for raw_index in range(max(0, int(after_index) + 1), len(raw)):
            values = [_clean(value) for value in raw[raw_index] if _clean(value)]
            signature = _signature(values)
            if not signature or signature in blocked:
                continue
            return raw_index
        return None

    def columns(self, *, max_items_per_level: int = 160) -> list[list[str]]:
        """Return logical taxonomy levels, complete across each internal scroller.

        Before any taxonomy click only the root column is eligible.  After a parent
        click, exactly one newly-created column to its right may become the next
        logical level.  Columns that already existed before that click are treated
        as stable Step-1 presentation content, not taxonomy descendants.
        """

        limit = max(1, int(max_items_per_level))
        try:
            self._prepare_browse()
        except Exception:
            return []

        raw = self._raw_columns(max_items_per_level=limit)
        if not raw or not raw[0]:
            return []

        logical: list[list[str]] = []
        physical: list[int] = []
        max_depth = max(0, self._clicked_depth + 1)
        previous_index = -1

        for depth in range(max_depth + 1):
            raw = self._raw_columns(max_items_per_level=limit)
            if not raw:
                break
            if depth == 0:
                raw_index = 0
            else:
                candidate = self._next_physical_index(
                    raw,
                    depth=depth,
                    after_index=previous_index,
                )
                if candidate is None:
                    break
                raw_index = candidate

            values = self._harvest_physical_column(
                raw_index,
                list(raw[raw_index]),
                max_items_per_level=limit,
            )
            if not values:
                break
            logical.append(values)
            physical.append(raw_index)
            previous_index = raw_index

        self._logical_raw_indices = physical
        return logical

    def _reveal_exact(
        self,
        raw_index: int,
        wanted: str,
        *,
        max_items_per_level: int,
    ) -> bool:
        target_key = _key(wanted)
        raw = self._raw_columns(max_items_per_level=max_items_per_level)
        if raw_index >= len(raw) or not raw[raw_index]:
            return False

        seed = _clean(raw[raw_index][0])
        top = self._scroll_column(seed, "top")
        if top.get("found"):
            self._wait(90)

        for _ in range(32):
            raw = self._raw_columns(max_items_per_level=max_items_per_level)
            if raw_index >= len(raw):
                return False
            chunk = [_clean(value) for value in raw[raw_index] if _clean(value)]
            if any(_key(value) == target_key for value in chunk):
                return True
            if not chunk:
                return False
            state = self._scroll_column(chunk[-1], "next")
            if not state.get("found") or not state.get("moved"):
                return False
            self._wait(90)
        return False

    def click_node(self, level: int, text: str, *, max_items_per_level: int = 160) -> bool:
        wanted = _clean(text)
        logical_level = int(level)
        limit = max(1, int(max_items_per_level))
        if logical_level < 0 or not wanted:
            return False

        try:
            logical = self.columns(max_items_per_level=limit)
            if logical_level >= len(logical) or logical_level >= len(self._logical_raw_indices):
                return False
            if sum(1 for value in logical[logical_level] if _key(value) == _key(wanted)) != 1:
                return False

            raw_index = self._logical_raw_indices[logical_level]
            before = self._raw_columns(max_items_per_level=limit)
            stale = {
                _signature([_clean(value) for value in column if _clean(value)])
                for column in before[raw_index + 1 :]
                if _signature([_clean(value) for value in column if _clean(value)])
            }

            if not self._reveal_exact(raw_index, wanted, max_items_per_level=limit):
                return False
            clicked = bool(
                self._owned.click_node(
                    raw_index,
                    wanted,
                    max_items_per_level=limit,
                )
            )
            if not clicked:
                return False

            self._clicked_depth = max(self._clicked_depth, logical_level)
            self._stale_after_parent[logical_level + 1] = stale
            self._wait(120)
            return True
        except Exception:
            # Mechanical ownership verification failed.  Never substitute a
            # body-wide click or semantic guess; the caller may safely backtrack.
            return False


__all__ = ["ResilientMakroTaxonomyBrowser"]
