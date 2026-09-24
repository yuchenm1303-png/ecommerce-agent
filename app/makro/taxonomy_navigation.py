"""Bounded, state-aware traversal for the live Makro taxonomy tree.

This module owns only navigation mechanics: path memory, branch backtracking,
transition settling and search budgets. Product semantics stay in
``listing_creation`` and the DOM reader/clicker stays in ``taxonomy``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


ColumnsFn = Callable[[], list[list[str]]]
ClickFn = Callable[[int, str], bool]
ChooseFn = Callable[[list[str], list[str]], str]
LeafReadyFn = Callable[[str], bool]
CompleteLeafFn = Callable[[str], str]


@dataclass(slots=True)
class TaxonomyNavigationBudget:
    """Hard bounds that keep taxonomy exploration deterministic and finite."""

    max_depth: int = 7
    max_node_attempts: int = 16
    max_backtracks: int = 6
    node_attempts: int = 0
    backtracks: int = 0
    tried_by_path: dict[tuple[str, ...], set[str]] = field(default_factory=dict)

    @property
    def exhausted(self) -> bool:
        return (
            self.node_attempts >= self.max_node_attempts
            or self.backtracks >= self.max_backtracks
        )


def _key(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _column_signature(column: list[str] | None) -> tuple[str, ...]:
    return tuple(_key(item) for item in (column or []) if _key(item))


def _column_at(columns: list[list[str]], level: int) -> list[str]:
    index = int(level)
    if index < 0 or index >= len(columns):
        return []
    return list(columns[index] or [])


def _child_column(columns: list[list[str]], level: int) -> list[str]:
    return _column_at(columns, int(level) + 1)


def _required_stable_polls(max_polls: int) -> int:
    """Use a short quiet window without making small test/recovery budgets impossible."""

    return min(3, max(1, int(max_polls)))


def _wait_for_stable_column(
    page: Any,
    *,
    level: int,
    columns_fn: ColumnsFn,
    poll_ms: int,
    max_polls: int,
) -> list[str]:
    """Return one live taxonomy level only after its exact contents settle.

    Makro paints taxonomy columns incrementally. A newly visible singleton can be
    a legitimate final column, but it can also be merely the first row of a larger
    column. Requiring the same non-empty signature across a bounded quiet window
    preserves real singleton leaves while preventing semantic decisions on a
    partially rendered list.
    """

    required = _required_stable_polls(max_polls)
    candidate_signature: tuple[str, ...] = ()
    candidate_values: list[str] = []
    confirmations = 0

    for _ in range(max(1, int(max_polls))):
        values = _column_at(columns_fn(), level)
        signature = _column_signature(values)
        if signature:
            if signature == candidate_signature:
                confirmations += 1
            else:
                candidate_signature = signature
                candidate_values = values
                confirmations = 1
            if confirmations >= required:
                return list(candidate_values)
        else:
            candidate_signature = ()
            candidate_values = []
            confirmations = 0
        page.wait_for_timeout(max(1, int(poll_ms)))

    return []


def _wait_for_branch_outcome(
    page: Any,
    *,
    level: int,
    selected: str,
    previous_child: tuple[str, ...],
    columns_fn: ColumnsFn,
    leaf_ready_fn: LeafReadyFn,
    poll_ms: int,
    max_polls: int,
) -> str:
    """Wait for a selected-node leaf or a stable, genuinely changed child.

    The leaf probe is selected-node aware. This prevents a rejected leaf's stale
    confirmation state from being mistaken for the next sibling during backtracking.
    Comparing the next-column signature separately prevents stale child columns from
    being mistaken for the newly selected sibling's children while Makro repaints.
    """

    required = _required_stable_polls(max_polls)
    candidate_signature: tuple[str, ...] = ()
    confirmations = 0

    for _ in range(max(1, int(max_polls))):
        if leaf_ready_fn(selected):
            return "leaf"

        child = _column_signature(_child_column(columns_fn(), level))
        if child and child != previous_child:
            if child == candidate_signature:
                confirmations += 1
            else:
                candidate_signature = child
                confirmations = 1
            if confirmations >= required:
                return "child"
        else:
            candidate_signature = ()
            confirmations = 0

        page.wait_for_timeout(max(1, int(poll_ms)))

    if leaf_ready_fn(selected):
        return "leaf"
    return "dead"


def navigate_live_taxonomy(
    page: Any,
    *,
    columns_fn: ColumnsFn,
    click_fn: ClickFn,
    choose_fn: ChooseFn,
    leaf_ready_fn: LeafReadyFn,
    complete_leaf_fn: CompleteLeafFn,
    wait_ms: int,
    max_depth: int = 7,
    max_node_attempts: int = 16,
    max_backtracks: int = 6,
    transition_polls: int = 18,
) -> str:
    """Depth-first live taxonomy traversal with bounded semantic backtracking.

    ``choose_fn`` may return an empty string when none of the exact live nodes is
    semantically suitable. That rejects the current branch instead of failing the
    whole workflow. ``complete_leaf_fn`` follows the same contract at a reached
    leaf: a non-empty canonical Vertical accepts the leaf, while an empty string
    means the leaf failed semantic validation and the navigator must backtrack.
    ``leaf_ready_fn`` receives the node that was just clicked and must only report
    a leaf when the current confirmation belongs to that exact node.

    Exceptions from callbacks remain hard failures because they indicate an invalid
    response or a mechanical/verification problem rather than an unsuitable branch.
    When all bounded tree paths are exhausted this function returns ``""``.
    """

    initial = columns_fn()
    if not initial or not initial[0]:
        return ""

    budget = TaxonomyNavigationBudget(
        max_depth=max(1, int(max_depth)),
        max_node_attempts=max(1, int(max_node_attempts)),
        max_backtracks=max(1, int(max_backtracks)),
    )
    poll_ms = max(100, min(250, int(wait_ms) // 4 if int(wait_ms) > 0 else 200))

    def explore(level: int, path: list[str]) -> str:
        if level >= budget.max_depth or budget.exhausted:
            return ""

        path_key = tuple(_key(item) for item in path)
        tried = budget.tried_by_path.setdefault(path_key, set())

        while not budget.exhausted:
            live_values = _wait_for_stable_column(
                page,
                level=level,
                columns_fn=columns_fn,
                poll_ms=poll_ms,
                max_polls=transition_polls,
            )
            if not live_values:
                return ""

            candidates = [
                item
                for item in live_values
                if _key(item) and _key(item) not in tried
            ]
            if not candidates:
                return ""

            selected = str(choose_fn(list(path), candidates) or "").strip()
            if not selected:
                return ""

            selected_key = _key(selected)
            exact = [item for item in candidates if _key(item) == selected_key]
            if len(exact) != 1:
                raise RuntimeError(
                    f"taxonomy chooser returned a non-unique live node: {selected!r}"
                )
            selected = exact[0]
            tried.add(selected_key)

            before = columns_fn()
            previous_child = _column_signature(_child_column(before, level))
            if not click_fn(level, selected):
                raise RuntimeError(
                    f"Makro Step 1 could not click taxonomy node level={level}: {selected!r}"
                )
            budget.node_attempts += 1

            outcome = _wait_for_branch_outcome(
                page,
                level=level,
                selected=selected,
                previous_child=previous_child,
                columns_fn=columns_fn,
                leaf_ready_fn=leaf_ready_fn,
                poll_ms=poll_ms,
                max_polls=transition_polls,
            )
            if outcome == "leaf":
                resolved = str(complete_leaf_fn(selected) or "").strip()
                if resolved:
                    return resolved

            elif outcome == "child":
                resolved = explore(level + 1, [*path, selected])
                if resolved:
                    return resolved

            budget.backtracks += 1
            if budget.exhausted:
                return ""

        return ""

    return explore(0, [])


__all__ = ["TaxonomyNavigationBudget", "navigate_live_taxonomy"]
