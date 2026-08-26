from __future__ import annotations

from pathlib import Path

import pytest

import app.makro.vertical_selection as vertical_selection
from app.makro.listing_creation import ListingBootstrapHints
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
        self.leaf = level == 2 and node == "Air Purifiers"
        return True


class RejectedLeafPage:
    """Model the soil-tester failure: first real leaf is semantically wrong."""

    def __init__(self) -> None:
        self.selected: dict[int, str] = {}
        self.leaf = False
        self.clicks: list[tuple[int, str]] = []

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def columns(self) -> list[list[str]]:
        columns: list[list[str]] = [["Agricultural Products", "Measuring & Layout Tools"]]
        root = self.selected.get(0)
        if root == "Agricultural Products":
            columns.append(["Plant Nutrition"])
            if self.selected.get(1) == "Plant Nutrition":
                columns.append(["Fertilizer"])
        elif root == "Measuring & Layout Tools":
            columns.append(["Measuring Tools"])
            if self.selected.get(1) == "Measuring Tools":
                columns.append(["Soil Testers"])
        return columns

    def click(self, level: int, node: str) -> bool:
        self.clicks.append((level, node))
        self.selected[level] = node
        for stale_level in list(self.selected):
            if stale_level > level:
                del self.selected[stale_level]
        self.leaf = level == 2 and node in {"Fertilizer", "Soil Testers"}
        return True

    def leaf_ready(self, selected: str) -> bool:
        return self.leaf and self.selected.get(2) == selected


def _choose_soil_tester_path(path: list[str], candidates: list[str]) -> str:
    if path == []:
        return candidates[0]
    if path == ["Agricultural Products"]:
        return "Plant Nutrition"
    if path == ["Agricultural Products", "Plant Nutrition"]:
        return "Fertilizer"
    if path == ["Measuring & Layout Tools"]:
        return "Measuring Tools"
    if path == ["Measuring & Layout Tools", "Measuring Tools"]:
        return "Soil Testers"
    raise AssertionError((path, candidates))


class RetryPage:
    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)

    def goto(self, *_args, **_kwargs):
        raise AssertionError("Step 1 retry must not hard-reset the Makro SPA")


def _hints() -> ListingBootstrapHints:
    return ListingBootstrapHints(
        vertical_search_terms=("air purifier",),
        brand="",
        brand_status="unknown",
        product_summary="portable air purifier",
        product_identity={"product_type_en": "air purifier"},
    )


def test_taxonomy_backtracks_from_semantically_dead_singleton_branch() -> None:
    page = FakePage()

    def choose(path: list[str], candidates: list[str]) -> str:
        if path == []:
            return "Home Appliances"
        if path == ["Home Appliances"]:
            return candidates[0]
        if path == ["Home Appliances", "Small Appliances"]:
            assert candidates == ["Coffee Bean Grinder"]
            return ""
        if path == ["Home Appliances", "Home & Kitchen Appliances"]:
            assert candidates == ["Air Purifiers"]
            return "Air Purifiers"
        raise AssertionError((path, candidates))

    selected = navigate_live_taxonomy(
        page,
        columns_fn=page.columns,
        click_fn=page.click,
        choose_fn=choose,
        leaf_ready_fn=lambda node: page.leaf and page.selected.get(2) == node,
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


def test_taxonomy_rejected_leaf_backtracks_to_alternative_root_branch() -> None:
    page = RejectedLeafPage()
    completed: list[str] = []

    def complete(node: str) -> str:
        if node == "Fertilizer":
            return ""
        completed.append(node)
        return "soil_tester"

    selected = navigate_live_taxonomy(
        page,
        columns_fn=page.columns,
        click_fn=page.click,
        choose_fn=_choose_soil_tester_path,
        leaf_ready_fn=page.leaf_ready,
        complete_leaf_fn=complete,
        wait_ms=0,
        max_node_attempts=12,
        max_backtracks=10,
        transition_polls=2,
    )

    assert selected == "soil_tester"
    assert completed == ["Soil Testers"]
    assert page.clicks == [
        (0, "Agricultural Products"),
        (1, "Plant Nutrition"),
        (2, "Fertilizer"),
        (0, "Measuring & Layout Tools"),
        (1, "Measuring Tools"),
        (2, "Soil Testers"),
    ]


def test_taxonomy_all_rejected_leaves_exhaust_cleanly() -> None:
    page = RejectedLeafPage()

    selected = navigate_live_taxonomy(
        page,
        columns_fn=page.columns,
        click_fn=page.click,
        choose_fn=_choose_soil_tester_path,
        leaf_ready_fn=page.leaf_ready,
        complete_leaf_fn=lambda _node: "",
        wait_ms=0,
        max_node_attempts=12,
        max_backtracks=10,
        transition_polls=2,
    )

    assert selected == ""
    assert (2, "Fertilizer") in page.clicks
    assert (2, "Soil Testers") in page.clicks


def test_taxonomy_exhaustion_returns_empty_for_search_fallback() -> None:
    page = FakePage()

    selected = navigate_live_taxonomy(
        page,
        columns_fn=page.columns,
        click_fn=page.click,
        choose_fn=lambda _path, _candidates: "",
        leaf_ready_fn=lambda _node: False,
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
            leaf_ready_fn=lambda _node: False,
            complete_leaf_fn=lambda node: node,
            wait_ms=0,
            transition_polls=1,
        )


def test_display_label_and_canonical_vertical_slug_are_distinct(monkeypatch) -> None:
    page = RetryPage()
    monkeypatch.setattr(
        vertical_selection,
        "_observe_vertical_brand_transition",
        lambda *_args, **_kwargs: vertical_selection._VerticalBrandTransitionObservation(
            brand_step=True,
            canonical="air_purifier",
        ),
    )

    selected = vertical_selection._complete_exact_live_vertical(page, "Air Purifiers")

    assert selected == "air_purifier"


def test_stage_enum_can_lag_while_taxonomy_is_structurally_operable(monkeypatch) -> None:
    page = RetryPage()

    class ReadyTaxonomy:
        def __init__(self, _page) -> None:
            pass

        def columns(self) -> list[list[str]]:
            return [["Home Appliances", "Electronics"]]

    monkeypatch.setattr(vertical_selection, "is_product_info_step", lambda _page: False)
    monkeypatch.setattr(vertical_selection, "is_brand_step", lambda _page: False)
    monkeypatch.setattr(vertical_selection, "is_vertical_step", lambda _page: False)
    monkeypatch.setattr(vertical_selection, "ResilientMakroTaxonomyBrowser", ReadyTaxonomy)

    assert vertical_selection.is_vertical_interaction_ready(page) is True


def test_stale_partial_taxonomy_still_uses_grounded_search_first(monkeypatch) -> None:
    page = RetryPage()
    monkeypatch.setattr(vertical_selection, "_committed_vertical_from_later_stage", lambda _page: "")
    monkeypatch.setattr(vertical_selection, "is_vertical_interaction_ready", lambda _page: True)
    monkeypatch.setattr(
        vertical_selection,
        "_try_select_via_search",
        lambda *_args, **_kwargs: (
            "air_purifier",
            ["Home Appliances / Air Purifiers"],
            ("air purifier",),
        ),
    )

    def taxonomy_must_not_run(_page):
        raise AssertionError("successful grounded search must not mutate stale taxonomy")

    monkeypatch.setattr(vertical_selection, "ResilientMakroTaxonomyBrowser", taxonomy_must_not_run)

    assert vertical_selection.select_vertical(page, object(), _hints(), wait_ms=0) == "air_purifier"


def test_resilient_dom_reader_has_dedicated_singleton_extension_path() -> None:
    source = (ROOT / "app" / "makro" / "taxonomy_resilient.py").read_text(encoding="utf-8")

    assert "p.scrollable && p.items.length >= 2" in source
    assert "p.clickableCount < 1 || p.items.length < 1" in source
    assert "p.x <= rightmost.x + 24" in source
    assert "p.x > rightmost.x + 360" in source
    assert source.count("for (let depth = 0; depth < 7 && kept.length; depth++)") == 2


def test_retry_selector_never_hard_resets_same_spa_route() -> None:
    source = (ROOT / "app" / "makro" / "vertical_selection.py").read_text(encoding="utf-8")

    assert "page.goto(" not in source
    assert "could not reset a stale partial taxonomy path" not in source
    assert "begin_search_query(search)" in source
    assert "allow_stable_exact=False" in source


def test_formal_batch_delegates_to_the_same_step1_vertical_state_machine() -> None:
    single = (ROOT / "makro_gui_workflow.py").read_text(encoding="utf-8")
    batch = (ROOT / "makro_batch_job.py").read_text(encoding="utf-8")

    assert "from app.makro.vertical_selection import select_vertical" in single
    assert "from app.makro.step1_entry import prepare_single_step1_page" in single
    assert "from app.makro.step1_entry import prepare_owned_step1_page" in batch
    assert "_advance_listing_to_step3" in batch
    assert "_prepare_step1_page" not in single
    assert "def _prepare_owned_step1_page" not in batch
