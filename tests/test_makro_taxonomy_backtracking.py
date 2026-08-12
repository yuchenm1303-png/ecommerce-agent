from __future__ import annotations

from pathlib import Path

import pytest

import app.makro.vertical_selection as vertical_selection
from app.makro.taxonomy_navigation import navigate_live_taxonomy


ROOT = Path(__file__).resolve().parents[1]


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


class FakeSearch:
    def __init__(self) -> None:
        self.values: list[str] = []

    def fill(self, value: str) -> None:
        self.values.append(value)


class RetryPage:
    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)

    def goto(self, *_args, **_kwargs):
        raise AssertionError("stale Step 1 retry must not hard-reset the Makro SPA")


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


def test_stale_partial_taxonomy_uses_exact_live_search_without_spa_reset(monkeypatch) -> None:
    page = RetryPage()
    search = FakeSearch()

    class StaleTaxonomy:
        def __init__(self, _page) -> None:
            pass

        def columns(self) -> list[list[str]]:
            return [
                ["Home Appliances", "Electronics"],
                ["Small Appliances", "Home & Kitchen Appliances"],
                ["Coffee Bean Grinder"],
            ]

        def click_node(self, _level: int, _node: str) -> bool:
            raise AssertionError("stale partial path should use search before tree traversal")

    monkeypatch.setattr(vertical_selection, "is_vertical_step", lambda _page: True)
    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: search)
    monkeypatch.setattr(vertical_selection, "ResilientMakroTaxonomyBrowser", StaleTaxonomy)
    monkeypatch.setattr(
        vertical_selection,
        "navigate_live_taxonomy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale partial path must not enter tree traversal")
        ),
    )
    monkeypatch.setattr(
        vertical_selection,
        "_select_vertical_via_search",
        lambda _page, _provider, _hints, *, wait_ms: "Air Purifiers",
    )

    selected = vertical_selection.select_vertical(
        page,
        object(),
        object(),
        wait_ms=0,
    )

    assert selected == "Air Purifiers"
    assert search.values == [""]


def test_resilient_dom_reader_has_dedicated_singleton_extension_path() -> None:
    source = (ROOT / "app" / "makro" / "taxonomy_resilient.py").read_text(encoding="utf-8")

    # Proven multi-row scrollable columns remain the primary detector.
    assert "p.scrollable && p.items.length >= 2" in source
    # Singleton columns are admitted only as clickable, right-aligned extensions.
    assert "p.clickableCount < 1 || p.items.length < 1" in source
    assert "p.x <= rightmost.x + 24" in source
    assert "p.x > rightmost.x + 360" in source
    # Read and click paths intentionally use the same column-building policy.
    assert source.count("for (let depth = 0; depth < 7 && kept.length; depth++)") == 2


def test_retry_selector_never_hard_resets_same_spa_route() -> None:
    source = (ROOT / "app" / "makro" / "vertical_selection.py").read_text(encoding="utf-8")

    assert "page.goto(" not in source
    assert "could not reset a stale partial taxonomy path" not in source
    assert "stale partial taxonomy path from a previous attempt" in source


def test_formal_single_and_batch_use_resilient_vertical_selector() -> None:
    single = (ROOT / "makro_gui_workflow.py").read_text(encoding="utf-8")
    batch = (ROOT / "makro_batch_job.py").read_text(encoding="utf-8")

    expected = "from app.makro.vertical_selection import select_vertical"
    assert expected in single
    assert expected in batch
