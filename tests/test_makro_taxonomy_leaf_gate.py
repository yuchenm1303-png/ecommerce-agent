from __future__ import annotations

import pytest

import app.makro.vertical_selection as vertical_selection
from app.makro.listing_creation import ListingBootstrapHints


def _hints() -> ListingBootstrapHints:
    return ListingBootstrapHints(
        vertical_search_terms=("watering timer",),
        brand="",
        brand_status="unknown",
        product_summary="Programmable outdoor watering timer with two outlets.",
        product_identity={
            "entity_kind": "physical_product",
            "product_type_en": "watering timer",
            "brand": "",
            "brand_status": "unknown",
            "product_summary": "Programmable outdoor watering timer with two outlets.",
            "confidence": 0.98,
            "evidence_refs": ["identity:page-title"],
        },
    )


def test_taxonomy_callbacks_fail_closed_before_vertical_completion_when_leaf_gate_rejects(monkeypatch) -> None:
    monkeypatch.setattr(
        vertical_selection,
        "choose_taxonomy_path_candidate",
        lambda _provider, _hints, _path, _candidates: "Storage Containers",
    )
    monkeypatch.setattr(
        vertical_selection,
        "validate_taxonomy_leaf_candidate",
        lambda _provider, _hints, _breadcrumb: False,
    )
    completed: list[str] = []
    monkeypatch.setattr(
        vertical_selection,
        "_complete_exact_live_vertical",
        lambda _page, node: completed.append(node) or "container",
    )

    choose, complete = vertical_selection._taxonomy_navigation_callbacks(
        object(), object(), _hints()
    )
    selected = choose(["Home Improvement"], ["Storage Containers"])
    assert selected == "Storage Containers"

    with pytest.raises(RuntimeError, match="failed the final Product Identity semantic gate"):
        complete(selected)
    assert completed == []


def test_taxonomy_callbacks_preserve_full_selected_breadcrumb_for_leaf_validation(monkeypatch) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(
        vertical_selection,
        "choose_taxonomy_path_candidate",
        lambda _provider, _hints, _path, _candidates: "Watering Controllers",
    )
    monkeypatch.setattr(
        vertical_selection,
        "validate_taxonomy_leaf_candidate",
        lambda _provider, _hints, breadcrumb: captured.append(list(breadcrumb)) or True,
    )
    monkeypatch.setattr(
        vertical_selection,
        "_complete_exact_live_vertical",
        lambda _page, _node: "watering_controller",
    )

    choose, complete = vertical_selection._taxonomy_navigation_callbacks(
        object(), object(), _hints()
    )
    selected = choose(
        ["Home Improvement", "Gardening Tools"],
        ["Watering Controllers"],
    )
    assert complete(selected) == "watering_controller"
    assert captured == [[
        "Home Improvement",
        "Gardening Tools",
        "Watering Controllers",
    ]]
