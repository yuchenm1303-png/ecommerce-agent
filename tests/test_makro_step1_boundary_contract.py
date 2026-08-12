from __future__ import annotations

from pathlib import Path

import pytest

import app.makro.vertical_selection as vertical_selection


ROOT = Path(__file__).resolve().parents[1]


class FakePage:
    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class FakeInput:
    def __init__(self, **attributes: str) -> None:
        self.attributes = attributes

    def get_attribute(self, name: str):
        return self.attributes.get(name)


def _brand_ready(monkeypatch, canonical: str) -> None:
    monkeypatch.setattr(vertical_selection, "_wait_for", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(vertical_selection, "is_brand_step", lambda _page: True)
    monkeypatch.setattr(
        vertical_selection,
        "_current_target_values",
        lambda _page: (canonical, ""),
    )
    monkeypatch.setattr(vertical_selection, "_body_text", lambda _page: "")


def test_repeated_air_purifier_selection_accepts_display_slug_equivalence(monkeypatch) -> None:
    _brand_ready(monkeypatch, "air_purifier")

    selected = vertical_selection._complete_exact_live_vertical(
        FakePage(),
        "Air Purifiers",
        previous_canonical="air_purifier",
    )

    assert selected == "air_purifier"
    assert vertical_selection._display_slug_equivalent("Air Purifiers", "air_purifier")
    assert vertical_selection._display_slug_equivalent("Home Appliances", "home_appliance")
    assert vertical_selection._display_slug_equivalent("Cases & Covers", "cases_covers")


def test_repeated_unrelated_vertical_is_not_accepted(monkeypatch) -> None:
    _brand_ready(monkeypatch, "air_purifier")

    with pytest.raises(RuntimeError, match="independently verifiable vertical state"):
        vertical_selection._complete_exact_live_vertical(
            FakePage(),
            "Coffee Bean Grinder",
            previous_canonical="air_purifier",
        )


def test_lone_brand_input_is_not_independent_step1_evidence(monkeypatch) -> None:
    page = FakePage()
    brand_input = FakeInput(placeholder="Enter Brand Name", name="brand")

    monkeypatch.setattr(vertical_selection, "is_product_info_step", lambda _page: False)
    monkeypatch.setattr(vertical_selection, "is_brand_step", lambda _page: False)
    monkeypatch.setattr(vertical_selection, "is_vertical_step", lambda _page: False)
    monkeypatch.setattr(
        vertical_selection,
        "ResilientMakroTaxonomyBrowser",
        lambda _page: type("NoTaxonomy", (), {"columns": lambda self: []})(),
    )
    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: brand_input)
    monkeypatch.setattr(vertical_selection, "_body_text", lambda _page: "Check for the brand you want to sell")

    assert vertical_selection.is_vertical_interaction_ready(page) is False


def test_vertical_specific_input_can_prove_step1_when_stage_enum_lags(monkeypatch) -> None:
    page = FakePage()
    vertical_input = FakeInput(placeholder="Search Vertical", name="verticalSearch")

    monkeypatch.setattr(vertical_selection, "is_product_info_step", lambda _page: False)
    monkeypatch.setattr(vertical_selection, "is_brand_step", lambda _page: False)
    monkeypatch.setattr(vertical_selection, "is_vertical_step", lambda _page: False)
    monkeypatch.setattr(
        vertical_selection,
        "ResilientMakroTaxonomyBrowser",
        lambda _page: type("NoTaxonomy", (), {"columns": lambda self: []})(),
    )
    monkeypatch.setattr(vertical_selection, "_vertical_search_input", lambda _page: vertical_input)
    monkeypatch.setattr(vertical_selection, "_body_text", lambda _page: "")

    assert vertical_selection.is_vertical_interaction_ready(page) is True


def test_step1_entry_uses_structural_operability_contract() -> None:
    source = (ROOT / "app" / "makro" / "step1_entry.py").read_text(encoding="utf-8")

    assert "is_vertical_interaction_ready(page)" in source
    assert "timeout_s=30.0" in source
    assert "taxonomy_columns" in source
    assert "detect_stage().value" in source
    assert "page.goto(MAKRO_NEW_LISTING_URL" in source


def test_gui_and_batch_no_longer_own_independent_step1_wait_loops() -> None:
    single = (ROOT / "makro_gui_workflow.py").read_text(encoding="utf-8")
    batch = (ROOT / "makro_batch_job.py").read_text(encoding="utf-8")

    assert "prepare_single_step1_page(harness)" in single
    assert "prepare_owned_step1_page(page)" in batch
    assert "_prepare_step1_page(harness)" not in single
    assert "while elapsed < 20_000" not in batch
