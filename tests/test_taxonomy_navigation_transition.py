from __future__ import annotations

import pytest

from app.makro import taxonomy_navigation as navigation


class _FakePage:
    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(int(milliseconds))


def test_verified_leaf_wins_over_provisional_taxonomy_observation_error() -> None:
    page = _FakePage()
    leaf_checks = 0

    def leaf_ready(_selected: str) -> bool:
        nonlocal leaf_checks
        leaf_checks += 1
        # First probe sees the still-transitioning Step 1 DOM. By the probe made
        # immediately after the structural observation fails, Makro has rendered
        # the authoritative Select Brand confirmation for the clicked Vertical.
        return leaf_checks >= 2

    def provisional_columns() -> list[list[str]]:
        raise RuntimeError(
            "Makro generated multiple new structural taxonomy groups after one parent click"
        )

    outcome = navigation._wait_for_branch_outcome(
        page,
        level=0,
        selected="Household Care & Supplies / Housekeeping & Laundry / Home Cleaning Set",
        previous_child=(),
        columns_fn=provisional_columns,
        leaf_ready_fn=leaf_ready,
        poll_ms=100,
        max_polls=4,
    )

    assert outcome == "leaf"


def test_persistent_taxonomy_observation_error_remains_hard_failure() -> None:
    page = _FakePage()
    failure = RuntimeError("persistent taxonomy ownership failure")

    def never_leaf(_selected: str) -> bool:
        return False

    def broken_columns() -> list[list[str]]:
        raise failure

    with pytest.raises(RuntimeError, match="persistent taxonomy ownership failure") as captured:
        navigation._wait_for_branch_outcome(
            page,
            level=0,
            selected="Unconfirmed Node",
            previous_child=(),
            columns_fn=broken_columns,
            leaf_ready_fn=never_leaf,
            poll_ms=100,
            max_polls=3,
        )

    assert captured.value is failure
