"""Scroll-complete structural traversal for the live Makro Step-1 taxonomy.

The raw catalog sensor owns real taxonomy containers from the exact Vertical
search control.  This layer adds lifecycle mechanics only: Search->Browse reset,
complete internal scrolling, exact-node reveal, and child-column rebinding after
our own clicks.  Product/category meaning remains entirely in the AI chooser.

A known-partial category list is never returned.  If a scroll owner cannot be
proved complete or a generated child cannot be uniquely rebound, traversal fails
with an explicit mechanical error instead of silently presenting incomplete live
nodes to AI.
"""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import Page

from .catalog_taxonomy import CatalogTaxonomyBrowser, TaxonomySurfaceError
from .listing_creation import _vertical_search_input


class TaxonomyMechanicalError(RuntimeError):
    """A live taxonomy operation could not be mechanically completed or proven."""


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _key(value: object) -> str:
    return _clean(value).casefold()


def _signature(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_key(value) for value in values if _key(value))


def _diag(event: str, **payload: object) -> None:
    print(
        "MAKRO_TAXONOMY_DIAG "
        + json.dumps(
            {"event": str(event or "unknown"), **payload},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ),
        flush=True,
    )


def _merge_unique(target: list[str], values: list[str]) -> None:
    seen = {_key(value) for value in target if _key(value)}
    for raw in values:
        value = _clean(raw)
        normalized = _key(value)
        if not value or not normalized or normalized in seen:
            continue
        target.append(value)
        seen.add(normalized)


class ResilientMakroTaxonomyBrowser:
    """Expose one complete, click-owned logical taxonomy tree."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self._owned = CatalogTaxonomyBrowser(page)
        self._browse_prepared = False
        self._clicked_depth = -1
        self._logical_group_ids: list[str] = []
        self._logical_owner_ids: list[str] = []
        self._logical_dom_orders: list[int] = []
        self._stale_after_parent: dict[int, dict[str, tuple[str, ...]]] = {}

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
            raise TaxonomyMechanicalError(
                "Makro Step 1 could not clear Vertical Search before Browse taxonomy fallback"
            ) from exc
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
        _diag("browse_prepared", search_cleared=True)

    def _descriptors(self, *, max_items_per_level: int) -> list[dict[str, Any]]:
        try:
            descriptors = self._owned.column_descriptors(
                max_items_per_level=max(8, int(max_items_per_level))
            )
        except TaxonomySurfaceError as exc:
            _diag(
                "mechanical_failure",
                operation="inspect",
                detail=str(exc),
                owner_diagnostic=self.last_diagnostic,
            )
            raise TaxonomyMechanicalError(str(exc)) from exc
        except Exception as exc:
            _diag(
                "mechanical_failure",
                operation="inspect",
                detail=f"{type(exc).__name__}: {exc}",
            )
            raise TaxonomyMechanicalError(
                f"Makro taxonomy structural inspection failed: {type(exc).__name__}: {exc}"
            ) from exc
        if not descriptors:
            raise TaxonomyMechanicalError(
                "Makro taxonomy structural owner returned no live category groups"
            )
        return descriptors

    @staticmethod
    def _by_group(
        descriptors: list[dict[str, Any]],
        group_id: str,
    ) -> dict[str, Any] | None:
        wanted = str(group_id or "")
        matches = [entry for entry in descriptors if str(entry.get("group_id") or "") == wanted]
        if len(matches) == 1:
            return matches[0]
        return None

    def _scroll(
        self,
        owner_id: str,
        action: str,
        *,
        group_id: str,
    ) -> dict[str, Any]:
        try:
            state = self._owned.scroll_owned_column(owner_id, action)
        except Exception as exc:
            _diag(
                "mechanical_failure",
                operation="scroll",
                action=action,
                group_id=group_id,
                owner_id=owner_id,
                detail=f"{type(exc).__name__}: {exc}",
            )
            raise TaxonomyMechanicalError(
                f"Makro taxonomy owned column could not scroll ({action}): {type(exc).__name__}: {exc}"
            ) from exc
        if not state.get("found", True):
            raise TaxonomyMechanicalError(
                f"Makro taxonomy owned column disappeared while scrolling: group={group_id!r} owner={owner_id!r}"
            )
        return state

    def _harvest_owned_column(
        self,
        descriptor: dict[str, Any],
        *,
        max_items_per_level: int,
    ) -> list[str]:
        """Enumerate one real taxonomy owner to a proven end, then restore top."""

        limit = max(8, int(max_items_per_level))
        group_id = str(descriptor.get("group_id") or "")
        owner_id = str(descriptor.get("owner_id") or group_id)
        if not group_id or not owner_id:
            raise TaxonomyMechanicalError("Makro taxonomy descriptor has no stable structural owner id")

        top = self._scroll(owner_id, "top", group_id=group_id)
        if not top.get("at_end"):
            self._wait(90)

        output: list[str] = []
        completed = False
        iterations = 0
        try:
            for iterations in range(1, 97):
                current = self._descriptors(max_items_per_level=limit)
                live = self._by_group(current, group_id)
                if live is None:
                    raise TaxonomyMechanicalError(
                        f"Makro taxonomy owned group was replaced during complete harvest: {group_id!r}"
                    )
                chunk = [_clean(value) for value in live.get("items") or [] if _clean(value)]
                if not chunk:
                    raise TaxonomyMechanicalError(
                        f"Makro taxonomy owned group became empty before harvest completed: {group_id!r}"
                    )
                _merge_unique(output, chunk)
                if len(output) > limit:
                    raise TaxonomyMechanicalError(
                        "Makro taxonomy level exceeds the bounded complete candidate capacity; "
                        f"group={group_id!r} count>{limit}. Refusing to truncate live nodes."
                    )

                scroll_top = float(live.get("scroll_top") or 0.0)
                max_scroll = float(live.get("max_scroll") or 0.0)
                at_end = scroll_top >= max_scroll - 1.0
                _diag(
                    "harvest_progress",
                    group_id=group_id,
                    owner_id=owner_id,
                    iteration=iterations,
                    unique_count=len(output),
                    chunk_count=len(chunk),
                    scroll_top=scroll_top,
                    max_scroll=max_scroll,
                    at_end=at_end,
                )
                if at_end:
                    completed = True
                    break

                moved = self._scroll(owner_id, "next", group_id=group_id)
                if not moved.get("moved") and not moved.get("at_end"):
                    raise TaxonomyMechanicalError(
                        "Makro taxonomy scroll owner could not advance before its real end; "
                        f"group={group_id!r} scroll_top={moved.get('scroll_top')} max_scroll={moved.get('max_scroll')}"
                    )
                self._wait(90)

            if not completed:
                raise TaxonomyMechanicalError(
                    "Makro taxonomy harvest exhausted its mechanical scroll budget before proving end-of-list; "
                    f"group={group_id!r} iterations={iterations}"
                )
        finally:
            try:
                self._scroll(owner_id, "top", group_id=group_id)
                self._wait(60)
            except Exception:
                # Preserve the original mechanical failure if one already exists.
                pass

        _diag(
            "harvest_complete",
            group_id=group_id,
            owner_id=owner_id,
            item_count=len(output),
            iterations=iterations,
            items=output,
        )
        return output

    def _root_descriptor(
        self,
        descriptors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        roots = [entry for entry in descriptors if bool(entry.get("is_root"))]
        if len(roots) != 1:
            raise TaxonomyMechanicalError(
                f"Makro taxonomy requires exactly one structural root owner, observed={len(roots)}"
            )
        return roots[0]

    def _next_descriptor(
        self,
        descriptors: list[dict[str, Any]],
        *,
        depth: int,
        parent_group_id: str,
        parent_dom_order: int,
    ) -> dict[str, Any] | None:
        stale = self._stale_after_parent.get(int(depth), {})
        used = set(self._logical_group_ids[:depth])
        candidates = [
            entry
            for entry in descriptors
            if str(entry.get("group_id") or "") not in used
            and str(entry.get("group_id") or "") != parent_group_id
            and not bool(entry.get("is_root"))
            and int(entry.get("dom_order") or 0) > int(parent_dom_order)
        ]

        new = [
            entry
            for entry in candidates
            if str(entry.get("group_id") or "") not in stale
        ]
        if len(new) == 1:
            return new[0]
        if len(new) > 1:
            raise TaxonomyMechanicalError(
                "Makro generated multiple new structural taxonomy groups after one parent click; "
                f"depth={depth} group_ids={[entry.get('group_id') for entry in new]}. Refusing to guess."
            )

        changed: list[dict[str, Any]] = []
        for entry in candidates:
            group_id = str(entry.get("group_id") or "")
            before = stale.get(group_id)
            after = _signature(list(entry.get("items") or []))
            if before is not None and after and after != before:
                changed.append(entry)
        if len(changed) == 1:
            return changed[0]
        if len(changed) > 1:
            raise TaxonomyMechanicalError(
                "Makro repainted multiple pre-existing groups after one taxonomy click; "
                f"depth={depth} group_ids={[entry.get('group_id') for entry in changed]}. Refusing ambiguous rebinding."
            )
        return None

    def columns(self, *, max_items_per_level: int = 400) -> list[list[str]]:
        """Return complete logical taxonomy levels only after proving each list end."""

        limit = max(8, int(max_items_per_level))
        self._prepare_browse()
        descriptors = self._descriptors(max_items_per_level=limit)
        root = self._root_descriptor(descriptors)

        logical: list[list[str]] = []
        group_ids: list[str] = []
        owner_ids: list[str] = []
        dom_orders: list[int] = []
        max_depth = max(0, self._clicked_depth + 1)
        current = root

        for depth in range(max_depth + 1):
            values = self._harvest_owned_column(current, max_items_per_level=limit)
            if not values:
                raise TaxonomyMechanicalError(
                    f"Makro taxonomy logical level {depth} completed with no live nodes"
                )
            logical.append(values)
            group_id = str(current.get("group_id") or "")
            owner_id = str(current.get("owner_id") or group_id)
            dom_order = int(current.get("dom_order") or 0)
            group_ids.append(group_id)
            owner_ids.append(owner_id)
            dom_orders.append(dom_order)

            if depth >= max_depth:
                break
            descriptors = self._descriptors(max_items_per_level=limit)
            child = self._next_descriptor(
                descriptors,
                depth=depth + 1,
                parent_group_id=group_id,
                parent_dom_order=dom_order,
            )
            if child is None:
                break
            current = child

        self._logical_group_ids = group_ids
        self._logical_owner_ids = owner_ids
        self._logical_dom_orders = dom_orders
        _diag(
            "logical_columns",
            depth=len(logical),
            group_ids=group_ids,
            counts=[len(values) for values in logical],
        )
        return logical

    def _reveal_exact(
        self,
        group_id: str,
        owner_id: str,
        wanted: str,
        *,
        max_items_per_level: int,
    ) -> bool:
        target = _key(wanted)
        limit = max(8, int(max_items_per_level))
        if not target:
            return False
        self._scroll(owner_id, "top", group_id=group_id)
        self._wait(60)

        for _ in range(96):
            descriptors = self._descriptors(max_items_per_level=limit)
            live = self._by_group(descriptors, group_id)
            if live is None:
                raise TaxonomyMechanicalError(
                    f"Makro taxonomy group disappeared while revealing exact node: {group_id!r}"
                )
            chunk = [_clean(value) for value in live.get("items") or [] if _clean(value)]
            exact = [value for value in chunk if _key(value) == target]
            if len(exact) == 1:
                return True
            if len(exact) > 1:
                raise TaxonomyMechanicalError(
                    f"Makro taxonomy exact node is duplicated inside one owned group: {wanted!r}"
                )
            scroll_top = float(live.get("scroll_top") or 0.0)
            max_scroll = float(live.get("max_scroll") or 0.0)
            if scroll_top >= max_scroll - 1.0:
                return False
            state = self._scroll(owner_id, "next", group_id=group_id)
            if not state.get("moved") and not state.get("at_end"):
                raise TaxonomyMechanicalError(
                    f"Makro taxonomy could not reveal exact node before scroll end: {wanted!r}"
                )
            self._wait(90)
        raise TaxonomyMechanicalError(
            f"Makro taxonomy exact-node reveal exhausted its scroll budget: {wanted!r}"
        )

    def click_node(self, level: int, text: str, *, max_items_per_level: int = 400) -> bool:
        wanted = _clean(text)
        logical_level = int(level)
        limit = max(8, int(max_items_per_level))
        if logical_level < 0 or not wanted:
            return False

        logical = self.columns(max_items_per_level=limit)
        if logical_level >= len(logical):
            return False
        if logical_level >= len(self._logical_group_ids) or logical_level >= len(self._logical_owner_ids):
            raise TaxonomyMechanicalError(
                f"Makro taxonomy logical ownership was lost at level={logical_level}"
            )
        exact = [value for value in logical[logical_level] if _key(value) == _key(wanted)]
        if len(exact) != 1:
            return False

        group_id = self._logical_group_ids[logical_level]
        owner_id = self._logical_owner_ids[logical_level]
        parent_dom_order = self._logical_dom_orders[logical_level]
        before = self._descriptors(max_items_per_level=limit)
        stale = {
            str(entry.get("group_id") or ""): _signature(list(entry.get("items") or []))
            for entry in before
            if str(entry.get("group_id") or "")
            and str(entry.get("group_id") or "") != group_id
            and int(entry.get("dom_order") or 0) > int(parent_dom_order)
        }

        if not self._reveal_exact(
            group_id,
            owner_id,
            wanted,
            max_items_per_level=limit,
        ):
            raise TaxonomyMechanicalError(
                "Makro taxonomy had a node in its proven-complete candidate set but could not reveal it again; "
                f"level={logical_level} node={wanted!r}"
            )
        try:
            clicked = bool(
                self._owned.click_owned_node(
                    group_id,
                    wanted,
                    max_items_per_level=limit,
                )
            )
        except Exception as exc:
            raise TaxonomyMechanicalError(
                f"Makro taxonomy exact owned click failed: level={logical_level} node={wanted!r}: {type(exc).__name__}: {exc}"
            ) from exc
        if not clicked:
            raise TaxonomyMechanicalError(
                f"Makro taxonomy exact owned node could not be clicked: level={logical_level} node={wanted!r}"
            )

        self._clicked_depth = max(self._clicked_depth, logical_level)
        self._stale_after_parent[logical_level + 1] = stale
        self._wait(140)
        _diag(
            "node_clicked",
            level=logical_level,
            node=wanted,
            group_id=group_id,
            owner_id=owner_id,
            stale_group_count=len(stale),
        )
        return True


__all__ = ["ResilientMakroTaxonomyBrowser", "TaxonomyMechanicalError"]
