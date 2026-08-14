from __future__ import annotations

import json

import pytest

from app.makro.catalog_taxonomy import is_fresh_catalog_step1_url, parse_catalog_route
from app.makro.vertical_catalog import (
    CATALOG_SCHEMA_VERSION,
    build_catalog_payload,
    load_checkpoint,
    new_catalog_state,
    path_key,
    prepare_resume_state,
)


def test_path_key_normalizes_case_and_whitespace() -> None:
    assert path_key([" Home  Appliances ", "Vacuum   Sealers"]) == path_key(
        ["home appliances", "vacuum sealers"]
    )


def test_catalog_payload_builds_tree_and_canonical_index() -> None:
    state = new_catalog_state()
    state["roots"] = ["Home Appliances"]
    state["branches"] = [
        {
            "kind": "branch",
            "path": ["Home Appliances"],
            "label": "Home Appliances",
            "children": ["Kitchen Appliances"],
        },
        {
            "kind": "branch",
            "path": ["Home Appliances", "Kitchen Appliances"],
            "label": "Kitchen Appliances",
            "children": ["Vacuum Sealers", "Food Processors"],
        },
    ]
    state["leaves"] = [
        {
            "kind": "leaf",
            "path": ["Home Appliances", "Kitchen Appliances", "Vacuum Sealers"],
            "label": "Vacuum Sealers",
            "canonical_vertical": "vacuum_sealer",
        },
        {
            "kind": "leaf",
            "path": ["Home Appliances", "Kitchen Appliances", "Food Processors"],
            "label": "Food Processors",
            "canonical_vertical": "food_processor",
        },
    ]

    payload = build_catalog_payload(state)

    assert payload["complete"] is True
    assert payload["schema_version"] == CATALOG_SCHEMA_VERSION == 2
    assert payload["surface_contract"] == "step1_browse_verticals_scoped_v2"
    assert payload["stats"]["leaf_path_count"] == 2
    assert payload["stats"]["unique_vertical_count"] == 2
    assert payload["canonical_index"]["vacuum_sealer"] == [
        ["Home Appliances", "Kitchen Appliances", "Vacuum Sealers"]
    ]
    root = payload["tree"][0]
    assert root["label"] == "Home Appliances"
    kitchen = root["children"][0]
    assert kitchen["label"] == "Kitchen Appliances"
    assert {item["canonical_vertical"] for item in kitchen["children"]} == {
        "vacuum_sealer",
        "food_processor",
    }


def test_catalog_payload_stays_incomplete_with_pending_or_failed_paths() -> None:
    state = new_catalog_state()
    state["pending"] = [["Electronics"]]
    payload = build_catalog_payload(state)
    assert payload["complete"] is False
    assert payload["stats"]["pending_count"] == 1

    state["pending"] = []
    state["failed"] = [{"path": ["Home"], "error": "timeout"}]
    payload = build_catalog_payload(state)
    assert payload["complete"] is False
    assert payload["stats"]["failed_count"] == 1


def test_resume_requeues_failed_without_repeating_resolved_paths() -> None:
    state = new_catalog_state()
    state["branches"] = [
        {
            "kind": "branch",
            "path": ["Home"],
            "label": "Home",
            "children": ["Kitchen"],
        }
    ]
    state["pending"] = [["Home"], ["Sports"]]
    state["failed"] = [
        {"path": ["Home"], "error": "stale"},
        {"path": ["Electronics"], "error": "timeout"},
    ]

    resumed = prepare_resume_state(state)

    assert resumed["failed"] == []
    assert resumed["pending"] == [["Sports"], ["Electronics"]]


def test_catalog_probe_route_rejects_orders_and_accepts_fresh_step1() -> None:
    fresh = "https://seller.makro.co.za/index.html#dashboard/addListings/single"
    orders = "https://seller.makro.co.za/index.html#dashboard/orders"
    committed = (
        "https://seller.makro.co.za/index.html#dashboard/addListings/single"
        "?vertical=solar_charge_controller"
    )

    assert parse_catalog_route(fresh) is not None
    assert is_fresh_catalog_step1_url(fresh) is True
    assert parse_catalog_route(orders) is None
    assert is_fresh_catalog_step1_url(orders) is False
    assert is_fresh_catalog_step1_url(committed) is False


def test_v1_checkpoint_is_never_resumed_after_scoped_surface_fix(tmp_path) -> None:
    checkpoint = tmp_path / "vertical-catalog-checkpoint.json"
    checkpoint.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "roots": ["10", "New Orders"],
                "pending": [["New Orders"]],
                "branches": [],
                "leaves": [],
                "failed": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Browse Verticals DOM scoping"):
        load_checkpoint(checkpoint)
