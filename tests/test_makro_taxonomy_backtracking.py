from __future__ import annotations

from pathlib import Path

import pytest

from app.makro.taxonomy_navigation import navigate_live_taxonomy


class FakePage:
    def __init__(self) -> None:
        self.selected: dict[int, str] = {}
        self.leaf = False
        self.clicks: list[tuple[int, str]] = []

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def columns(self) -> list[list[str]]:
        columns: list[list[str]] = [
            ["Home Appliances", "Electronics"],
        ]
        if self.selected.get(0) == "Home Appliances":
            columns.append(["Small Appliances", "Home & Kitchen Appliances"])
            if self.selected.get(1) == "Small Appliances":
                # Regression from the real Makro Air Purifier acceptance: a
                # legitimate next taxonomy column can contain exactly one item.
                columns.append(["Coffee Bean Grinder"])
            elif self.selected.get(1) == "Home & Kitchen Appliances":
                columns.append(["Air Purifiers"])
        return columns

    def click(self, level: int, node: str) -> bool:
        self.clicks.append((level, node))
        self.selected[level] = node
        for stale_level in list(self.selected):
            if stale_level > level:
                del self.selected[stale_level]
        if level == 2 and node == "Air Purifiers":
            self.leaf = True
        return True


def test_taxonomy_backtracks_from_semantically_dead_singleton_branch() -> None:
    page = FakePage()

    def choose(path: list[str], candidates: list[str]) -> str:
        if path == []:
            return "Home Appliances"
        if path == ["Home Appliances"]:
            # First pass chooses Small Appliances. After that branch is rejected,
            # the navigator must call us with only the untried sibling.
            return candidates[0]
        if path == ["Home Appliances", "Small Appliances"]:
            assert candidates == ["Coffee Bean Grinder"]
            return ""  # Air purifier is not a coffee grinder.
        if path == ["Home Appliances", "Home & Kitchen Appliances"]:
            assert candidates == ["Air Purifiers"]
            return "Air Purifiers"
        raise AssertionError((path, candidates))

    selected = navigate_live_taxonomy(
        page,
        columns_fn=page.columns,
        click_fn=page.click,
        choose_fn=choose,
        leaf_ready_fn=lambda: page.leaf,
        complete_leaf_fn=lambda node: node,
        wait_ms=0,
        max_node_attempts=12,
        max_backtracks=5,
        transition_polls=2,
    )

    assert selected == "Air Purifiers"
    assert page.clicks == [
        (0, "Home Appliances"),
        (1, "Small Appliances"),
        (1, "Home & Kitchen Appliances"),
        (2, "Air Purifiers"),
    ]


def test_taxonomy_exhaustion_returns_empty_for_search_fallback() -> None:
    page = FakePage()

    selected = navigate_live_taxonomy(
        page,
        columns_fn=page.columns,
        click_fn=page.click,
        choose_fn=lambda _path, _candidates: "",
        leaf_ready_fn=lambda: False,
        complete_leaf_fn=lambda node: node,
        wait_ms=0,
        transition_polls=1,
    )

    assert selected == ""
    assert page.clicks == []


def test_taxonomy_mechanical_click_failure_is_not_semantic_backtracking() -> None:
    page = FakePage()

    with pytest.raises(RuntimeError, match="could not click taxonomy node"):
        navigate_live_taxonomy(
            page,
            columns_fn=page.columns,
            click_fn=lambda _level, _node: False,
            choose_fn=lambda _path, candidates: candidates[0],
            leaf_ready_fn=lambda: False,
            complete_leaf_fn=lambda node: node,
            wait_ms=0,
            transition_polls=1,
        )


def test_resilient_dom_reader_has_dedicated_singleton_extension_path() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "makro"
        / "taxonomy_resilient.py"
    ).read_text(encoding="utf-8")

    # Proven multi-row scrollable columns remain the primary detector.
    assert "p.scrollable && p.items.length >= 2" in source
    # Singleton columns are admitted only as clickable, right-aligned extensions.
    assert "p.clickableCount < 1 || p.items.length < 1" in source
    assert "p.x <= rightmost.x + 24" in source
    assert "p.x > rightmost.x + 360" in source
    # Read and click paths intentionally use the same column-building policy.
    assert source.count("for (let depth = 0; depth < 7 && kept.length; depth++)") == 2
