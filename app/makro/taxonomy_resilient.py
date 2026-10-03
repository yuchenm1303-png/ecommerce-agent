"""Scroll-complete structural traversal for the live Makro Step-1 taxonomy.

The raw catalog sensor owns real taxonomy containers from the exact Vertical
search control. This layer adds lifecycle mechanics only: verified Search->Browse
transition, complete internal scrolling, exact-node reveal, and child-column
rebinding after our own clicks. Product/category meaning remains entirely in the
AI chooser.

A category owner is fully harvested once and that proven-complete snapshot is
reused while the same DOM owner remains structurally valid. A click invalidates
only descendant/repaint candidates that can actually change. This preserves the
bounded stability polling contract without repeatedly scrolling unchanged parent
columns from top to bottom on every poll.

Scroll completion is intentionally tolerant of browser geometry quantization.
Chromium can expose an integer-like ``scrollHeight - clientHeight`` while the
reachable ``scrollTop`` is fractional and stops a little below that number.  The
wrapper therefore normalizes every scroll observation through one shared terminal
contract and gives a materially stalled owner a short settle/retry window before
failing closed.  A sub-pixel/few-pixel tail can never turn into a workflow-fatal
false stall, while a column that is genuinely stuck far from its end still fails.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from playwright.sync_api import Page

from .catalog_taxonomy import CatalogTaxonomyBrowser, TaxonomySurfaceError
from .listing_creation import _vertical_search_input
from .search_surface import read_search_rows


class TaxonomyMechanicalError(RuntimeError):
    """A live taxonomy operation could not be mechanically completed or proven."""


@dataclass(frozen=True, slots=True)
class _CompleteColumnSnapshot:
    owner_id: str
    capacity: int
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ScrollGeometry:
    scroll_top: float
    max_scroll: float
    remaining_scroll: float
    end_tolerance: float
    at_end: bool
    terminal_reason: str


_SCROLL_MIN_END_TOLERANCE = 2.0
_SCROLL_MAX_END_TOLERANCE = 4.0
_SCROLL_END_TOLERANCE_RATIO = 0.005
_SCROLL_PROGRESS_EPSILON = 0.25
_SCROLL_SETTLE_RETRIES = 2


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _key(value: object) -> str:
    return _clean(value).casefold()


def _signature(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_key(value) for value in values if _key(value))


def _number(state: Mapping[str, Any], key: str) -> float:
    try:
        return float(state.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _scroll_end_tolerance(state: Mapping[str, Any]) -> float:
    """Return one bounded CSS-pixel end tolerance shared by all Python checks.

    New catalog sensors publish the tolerance they used in-page.  Older/fake
    sensors are normalized here from client height so rollout remains backwards
    compatible and tests can exercise the Python safety net independently.
    """

    explicit = _number(state, "end_tolerance")
    if explicit > 0:
        return max(0.5, min(8.0, explicit))
    client_height = max(0.0, _number(state, "client_height"))
    derived = client_height * _SCROLL_END_TOLERANCE_RATIO if client_height else 0.0
    return max(
        _SCROLL_MIN_END_TOLERANCE,
        min(_SCROLL_MAX_END_TOLERANCE, derived or _SCROLL_MIN_END_TOLERANCE),
    )


def _scroll_geometry(state: Mapping[str, Any]) -> _ScrollGeometry:
    """Normalize mixed integer/fractional browser scroll geometry.

    ``at_end=True`` from the DOM owner is authoritative.  Otherwise a remaining
    tail no larger than the bounded tolerance is also terminal.  This specifically
    covers Chromium layouts where ``max_scroll`` is integer-like but the physically
    reachable ``scrollTop`` is fractional (for example 345.9047546 vs 347.0).
    """

    scroll_top = max(0.0, _number(state, "scroll_top"))
    max_scroll = max(0.0, _number(state, "max_scroll"))
    remaining = max(0.0, max_scroll - scroll_top)
    tolerance = _scroll_end_tolerance(state)
    reported_end = bool(state.get("at_end"))
    geometry_end = remaining <= tolerance
    at_end = bool(reported_end or geometry_end)
    terminal_reason = "reported_end" if reported_end else "geometry_tolerance" if geometry_end else ""
    return _ScrollGeometry(
        scroll_top=scroll_top,
        max_scroll=max_scroll,
        remaining_scroll=remaining,
        end_tolerance=tolerance,
        at_end=at_end,
        terminal_reason=terminal_reason,
    )


def _normalize_scroll_state(state: Mapping[str, Any]) -> dict[str, Any]:
    geometry = _scroll_geometry(state)
    normalized = dict(state)
    normalized.update(
        scroll_top=geometry.scroll_top,
        max_scroll=geometry.max_scroll,
        remaining_scroll=geometry.remaining_scroll,
        end_tolerance=geometry.end_tolerance,
        at_end=geometry.at_end,
        terminal_reason=geometry.terminal_reason,
    )
    return normalized


def _root_surface_signature(
    descriptors: list[dict[str, Any]],
) -> tuple[str, str, tuple[str, ...]] | None:
    """Describe exactly one structurally owned taxonomy root without semantics."""

    roots = [entry for entry in descriptors if bool(entry.get("is_root"))]
    if len(roots) != 1:
        return None
    root = roots[0]
    group_id = str(root.get("group_id") or "")
    owner_id = str(root.get("owner_id") or group_id)
    items = _signature(list(root.get("items") or []))
    if not group_id or not owner_id or not items:
        return None
    return group_id, owner_id, items


def _browse_transition_ready(
    prior_query_rows: tuple[str, ...],
    current_query_rows: tuple[str, ...],
    current_root: tuple[str, str, tuple[str, ...]] | None,
) -> bool:
    """Prove that a query-owned result surface cannot be reused as Browse root."""

    if current_root is None:
        return False
    if not prior_query_rows:
        return True
    query_surface_retired = current_query_rows != prior_query_rows
    root_is_not_prior_query = current_root[2] != prior_query_rows
    return bool(query_surface_retired and root_is_not_prior_query)


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
        self._complete_columns: dict[str, _CompleteColumnSnapshot] = {}

    @property
    def last_diagnostic(self) -> str:
        return str(getattr(self._owned, "last_diagnostic", "") or "")

    def _wait(self, milliseconds: int = 120) -> None:
        try:
            self.page.wait_for_timeout(max(1, int(milliseconds)))
        except Exception:
            pass

    def _probe_root_surface(
        self,
        *,
        max_items_per_level: int = 400,
    ) -> tuple[str, str, tuple[str, ...]] | None:
        """Best-effort structural root probe used only while a UI transition settles."""

        try:
            descriptors = self._owned.column_descriptors(
                max_items_per_level=max(8, int(max_items_per_level))
            )
        except Exception:
            return None
        return _root_surface_signature(descriptors)

    @staticmethod
    def _search_value(search: Any) -> str:
        try:
            return _clean(search.input_value())
        except Exception:
            try:
                return _clean(search.get_attribute("value"))
            except Exception:
                return ""

    def _wait_for_verified_browse_transition(
        self,
        search: Any,
        prior_query_rows: tuple[str, ...],
        *,
        timeout_s: float = 6.0,
    ) -> tuple[str, str, tuple[str, ...]] | None:
        """Wait for the query surface to retire and a stable Browse root to own Step 1."""

        deadline = time.monotonic() + max(1.0, float(timeout_s))
        stable_root: tuple[str, str, tuple[str, ...]] | None = None
        stable_samples = 0
        last_query_rows = prior_query_rows
        last_root: tuple[str, str, tuple[str, ...]] | None = None

        while time.monotonic() < deadline:
            last_query_rows = _signature(read_search_rows(search))
            last_root = self._probe_root_surface()
            ready = (
                not self._search_value(search)
                and _browse_transition_ready(prior_query_rows, last_query_rows, last_root)
            )
            if ready:
                if last_root == stable_root:
                    stable_samples += 1
                else:
                    stable_root = last_root
                    stable_samples = 1
                if stable_samples >= 2:
                    _diag(
                        "browse_transition_verified",
                        prior_query_row_count=len(prior_query_rows),
                        current_query_row_count=len(last_query_rows),
                        root_group_id=stable_root[0] if stable_root else "",
                        root_owner_id=stable_root[1] if stable_root else "",
                        root_item_count=len(stable_root[2]) if stable_root else 0,
                    )
                    return stable_root
            else:
                stable_root = None
                stable_samples = 0
            self._wait(160)

        _diag(
            "browse_transition_unverified",
            prior_query_rows=list(prior_query_rows)[:12],
            current_query_rows=list(last_query_rows)[:12],
            current_root_group_id=last_root[0] if last_root else "",
            current_root_items=list(last_root[2])[:12] if last_root else [],
        )
        return None

    def _reload_clean_browse_surface(
        self,
        *,
        timeout_s: float = 12.0,
    ) -> tuple[str, str, tuple[str, ...]]:
        """Rebuild the current uncommitted Step-1 DOM when SPA search state will not retire."""

        current_url = str(getattr(self.page, "url", "") or "")
        self._complete_columns.clear()
        try:
            self.page.reload(wait_until="domcontentloaded", timeout=15_000)
        except Exception as exc:
            raise TaxonomyMechanicalError(
                "Makro Step 1 Search->Browse transition stayed stale and the current Step-1 page could not be reloaded"
            ) from exc

        deadline = time.monotonic() + max(2.0, float(timeout_s))
        stable_root: tuple[str, str, tuple[str, ...]] | None = None
        stable_samples = 0
        while time.monotonic() < deadline:
            try:
                search = _vertical_search_input(self.page)
            except Exception:
                self._wait(180)
                continue
            if self._search_value(search):
                try:
                    search.fill("")
                except Exception:
                    self._wait(180)
                    continue
            root = self._probe_root_surface()
            if root is not None:
                if root == stable_root:
                    stable_samples += 1
                else:
                    stable_root = root
                    stable_samples = 1
                if stable_samples >= 2:
                    _diag(
                        "browse_surface_reloaded",
                        previous_url=current_url,
                        current_url=str(getattr(self.page, "url", "") or ""),
                        root_group_id=root[0],
                        root_owner_id=root[1],
                        root_item_count=len(root[2]),
                    )
                    return root
            else:
                stable_root = None
                stable_samples = 0
            self._wait(180)

        raise TaxonomyMechanicalError(
            "Makro Step 1 could not establish a stable Browse taxonomy root after rebuilding the current Step-1 page"
        )

    def _prepare_browse(self) -> None:
        if self._browse_prepared:
            return
        search = _vertical_search_input(self.page)
        prior_query_rows = _signature(read_search_rows(search))
        prior_search_value = self._search_value(search)
        transition_required = bool(prior_search_value or prior_query_rows)

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

        root: tuple[str, str, tuple[str, ...]] | None = None
        recovery = "none"
        if transition_required:
            root = self._wait_for_verified_browse_transition(search, prior_query_rows)
            if root is None:
                recovery = "reload_current_step1"
                root = self._reload_clean_browse_surface()
        else:
            self._wait(120)

        self._browse_prepared = True
        _diag(
            "browse_prepared",
            search_cleared=True,
            transition_required=transition_required,
            transition_verified=bool(root) if transition_required else True,
            recovery=recovery,
            prior_query_row_count=len(prior_query_rows),
            root_group_id=root[0] if root else "",
        )

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
            raw_state = self._owned.scroll_owned_column(owner_id, action)
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
        if not raw_state.get("found", True):
            raise TaxonomyMechanicalError(
                f"Makro taxonomy owned column disappeared while scrolling: group={group_id!r} owner={owner_id!r}"
            )
        state = _normalize_scroll_state(raw_state)
        _diag(
            "scroll_state",
            action=action,
            group_id=group_id,
            owner_id=owner_id,
            moved=bool(state.get("moved")),
            at_end=bool(state.get("at_end")),
            terminal_reason=state.get("terminal_reason", ""),
            scroll_top=state.get("scroll_top"),
            max_scroll=state.get("max_scroll"),
            remaining_scroll=state.get("remaining_scroll"),
            end_tolerance=state.get("end_tolerance"),
        )
        return state

    def _advance_owned_scroll(
        self,
        owner_id: str,
        *,
        group_id: str,
        max_items_per_level: int,
        operation: str,
    ) -> dict[str, Any]:
        """Advance one owned scroller with bounded async-settle recovery.

        A no-move response near the normalized end is terminal, not an error.
        A no-move response materially before the end gets two bounded settle/retry
        opportunities so React/virtual-list paint latency cannot become a false hard
        failure. Persistent material stalls remain fail-closed to prevent truncated
        category candidate sets.
        """

        limit = max(8, int(max_items_per_level))
        current_owner_id = str(owner_id or group_id)
        state = self._scroll(current_owner_id, "next", group_id=group_id)
        if bool(state.get("moved")) or bool(state.get("at_end")):
            return state

        baseline = _scroll_geometry(state).scroll_top
        last_state = state
        for retry in range(1, _SCROLL_SETTLE_RETRIES + 1):
            self._wait(100 + retry * 50)
            descriptors = self._descriptors(max_items_per_level=limit)
            live = self._by_group(descriptors, group_id)
            if live is None:
                raise TaxonomyMechanicalError(
                    "Makro taxonomy owned group disappeared while recovering a stalled scroll; "
                    f"operation={operation!r} group={group_id!r}"
                )

            settled = _normalize_scroll_state(live)
            settled_geometry = _scroll_geometry(settled)
            rebound_owner_id = str(live.get("owner_id") or group_id)
            if settled_geometry.at_end:
                _diag(
                    "scroll_stall_recovered",
                    operation=operation,
                    resolution="settled_at_end",
                    retry=retry,
                    group_id=group_id,
                    owner_id=rebound_owner_id,
                    scroll_top=settled_geometry.scroll_top,
                    max_scroll=settled_geometry.max_scroll,
                    remaining_scroll=settled_geometry.remaining_scroll,
                    end_tolerance=settled_geometry.end_tolerance,
                )
                settled.update(found=True, moved=False)
                return settled
            if settled_geometry.scroll_top > baseline + _SCROLL_PROGRESS_EPSILON:
                _diag(
                    "scroll_stall_recovered",
                    operation=operation,
                    resolution="async_progress",
                    retry=retry,
                    group_id=group_id,
                    owner_id=rebound_owner_id,
                    scroll_top=settled_geometry.scroll_top,
                    max_scroll=settled_geometry.max_scroll,
                )
                settled.update(found=True, moved=True)
                return settled

            if rebound_owner_id != current_owner_id:
                _diag(
                    "scroll_owner_rebound",
                    operation=operation,
                    group_id=group_id,
                    previous_owner_id=current_owner_id,
                    owner_id=rebound_owner_id,
                    retry=retry,
                )
                current_owner_id = rebound_owner_id

            last_state = self._scroll(current_owner_id, "next", group_id=group_id)
            if bool(last_state.get("moved")) or bool(last_state.get("at_end")):
                _diag(
                    "scroll_stall_recovered",
                    operation=operation,
                    resolution="retry_progress" if last_state.get("moved") else "retry_at_end",
                    retry=retry,
                    group_id=group_id,
                    owner_id=current_owner_id,
                    scroll_top=last_state.get("scroll_top"),
                    max_scroll=last_state.get("max_scroll"),
                    remaining_scroll=last_state.get("remaining_scroll"),
                    end_tolerance=last_state.get("end_tolerance"),
                )
                return last_state
            baseline = max(baseline, _scroll_geometry(last_state).scroll_top)

        geometry = _scroll_geometry(last_state)
        raise TaxonomyMechanicalError(
            "Makro taxonomy scroll owner could not advance after bounded settle/retry while materially before its real end; "
            f"operation={operation!r} group={group_id!r} scroll_top={geometry.scroll_top} "
            f"max_scroll={geometry.max_scroll} remaining={geometry.remaining_scroll} "
            f"tolerance={geometry.end_tolerance}"
        )

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

                geometry = _scroll_geometry(live)
                _diag(
                    "harvest_progress",
                    group_id=group_id,
                    owner_id=owner_id,
                    iteration=iterations,
                    unique_count=len(output),
                    chunk_count=len(chunk),
                    scroll_top=geometry.scroll_top,
                    max_scroll=geometry.max_scroll,
                    remaining_scroll=geometry.remaining_scroll,
                    end_tolerance=geometry.end_tolerance,
                    at_end=geometry.at_end,
                    terminal_reason=geometry.terminal_reason,
                )
                if geometry.at_end:
                    completed = True
                    break

                self._advance_owned_scroll(
                    owner_id,
                    group_id=group_id,
                    max_items_per_level=limit,
                    operation="harvest",
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

    def _complete_owned_column(
        self,
        descriptor: dict[str, Any],
        *,
        max_items_per_level: int,
    ) -> list[str]:
        """Return one structurally proven complete column, harvesting it at most once per owner generation."""

        limit = max(8, int(max_items_per_level))
        group_id = str(descriptor.get("group_id") or "")
        owner_id = str(descriptor.get("owner_id") or group_id)
        if not group_id or not owner_id:
            raise TaxonomyMechanicalError("Makro taxonomy descriptor has no stable structural owner id")

        cached = self._complete_columns.get(group_id)
        if cached is not None and cached.owner_id == owner_id and cached.capacity >= limit:
            return list(cached.values)

        values = self._harvest_owned_column(
            descriptor,
            max_items_per_level=limit,
        )
        self._complete_columns[group_id] = _CompleteColumnSnapshot(
            owner_id=owner_id,
            capacity=limit,
            values=tuple(values),
        )
        return values

    def _invalidate_complete_columns(self, group_ids: set[str] | list[str] | tuple[str, ...]) -> None:
        for raw_group_id in group_ids:
            group_id = str(raw_group_id or "")
            if group_id:
                self._complete_columns.pop(group_id, None)

    @staticmethod
    def _root_descriptor(
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
        used_group_ids: list[str],
    ) -> dict[str, Any] | None:
        used = {str(group_id or "") for group_id in used_group_ids if str(group_id or "")}

        # A previously committed logical level remains authoritative while that exact
        # structural owner is still live under the same parent. Makro keeps parent
        # columns mounted when a deeper child opens, so treating every mounted group
        # after the parent as a new candidate confuses the current level with its child.
        if 0 <= int(depth) < len(self._logical_group_ids) and int(depth) < len(self._logical_owner_ids):
            committed_group_id = str(self._logical_group_ids[int(depth)] or "")
            committed_owner_id = str(self._logical_owner_ids[int(depth)] or committed_group_id)
            committed = self._by_group(descriptors, committed_group_id)
            if (
                committed is not None
                and committed_group_id not in used
                and committed_group_id != parent_group_id
                and not bool(committed.get("is_root"))
                and str(committed.get("owner_id") or committed_group_id) == committed_owner_id
                and int(committed.get("dom_order") or 0) > int(parent_dom_order)
            ):
                _diag(
                    "logical_group_reused",
                    depth=int(depth),
                    group_id=committed_group_id,
                    owner_id=committed_owner_id,
                )
                return committed

        stale = self._stale_after_parent.get(int(depth), {})
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
        """Return complete logical taxonomy levels, reusing proven unchanged owners across stability polls."""

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
            values = self._complete_owned_column(
                current,
                max_items_per_level=limit,
            )
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
                used_group_ids=group_ids,
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
            geometry = _scroll_geometry(live)
            if geometry.at_end:
                return False
            self._advance_owned_scroll(
                owner_id,
                group_id=group_id,
                max_items_per_level=limit,
                operation="reveal_exact",
            )
            self._wait(90)
        raise TaxonomyMechanicalError(
            f"Makro taxonomy exact-node reveal exhausted its scroll budget: {wanted!r}"
        )

    def _commit_click_state(
        self,
        logical_level: int,
        stale: dict[str, tuple[str, ...]],
    ) -> None:
        """Commit one click as the sole active branch and invalidate only structurally mutable descendants."""

        keep = int(logical_level) + 1
        discarded_group_ids = list(self._logical_group_ids[keep:])
        discarded_depths = sorted(
            int(depth)
            for depth in self._stale_after_parent
            if int(depth) > int(logical_level)
        )
        self._invalidate_complete_columns(set(discarded_group_ids).union(stale))

        self._clicked_depth = int(logical_level)
        self._logical_group_ids = self._logical_group_ids[:keep]
        self._logical_owner_ids = self._logical_owner_ids[:keep]
        self._logical_dom_orders = self._logical_dom_orders[:keep]
        self._stale_after_parent = {
            int(depth): snapshot
            for depth, snapshot in self._stale_after_parent.items()
            if int(depth) <= int(logical_level)
        }
        self._stale_after_parent[int(logical_level) + 1] = dict(stale)
        _diag(
            "branch_state_committed",
            level=int(logical_level),
            active_group_ids=list(self._logical_group_ids),
            invalidated_group_ids=sorted(set(discarded_group_ids).union(stale)),
            discarded_deeper_depths=discarded_depths,
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

        self._commit_click_state(logical_level, stale)
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