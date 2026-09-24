from __future__ import annotations

import json

import pytest

from app.makro.catalog_taxonomy import (
    CATALOG_PROBE_STEP1_URL,
    STEP1_SURFACE_MARKERS,
    is_fresh_catalog_step1_url,
    parse_catalog_route,
)
from app.makro.vertical_catalog import (
    CATALOG_SCHEMA_VERSION,
    SURFACE_CONTRACT,
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
    assert payload["schema_version"] == CATALOG_SCHEMA_VERSION == 3
    assert payload["surface_contract"] == SURFACE_CONTRACT == "step1_select_vertical_scoped_v3"
    assert payload["source_url"] == CATALOG_PROBE_STEP1_URL
    assert payload["safety"]["taxonomy_surface"] == "Select The Vertical For Your Product"
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


def test_catalog_probe_uses_observed_index_html_step1_route() -> None:
    assert CATALOG_PROBE_STEP1_URL == (
        "https://seller.makro.co.za/index.html#dashboard/addListings/single"
    )
    assert is_fresh_catalog_step1_url(CATALOG_PROBE_STEP1_URL) is True

    old_bare_route = "https://seller.makro.co.za/#dashboard/addListings/single"
    # The parser still recognizes the hash route, but the harvester contract no
    # longer navigates to this form because the live portal redirects it home.
    assert parse_catalog_route(old_bare_route) is not None


def test_catalog_probe_route_rejects_orders_and_committed_vertical() -> None:
    orders = "https://seller.makro.co.za/index.html#dashboard/orders"
    committed = (
        "https://seller.makro.co.za/index.html#dashboard/addListings/single"
        "?vertical=solar_charge_controller"
    )

    assert parse_catalog_route(orders) is None
    assert is_fresh_catalog_step1_url(orders) is False
    assert is_fresh_catalog_step1_url(committed) is False


def test_real_step1_heading_is_part_of_surface_contract() -> None:
    assert "Select The Vertical For Your Product" in STEP1_SURFACE_MARKERS
    # Generic progress-step text is intentionally excluded because it can live
    # outside the owned taxonomy content area.
    assert "SELECT VERTICAL" not in STEP1_SURFACE_MARKERS


@pytest.mark.parametrize("version", [1, 2])
def test_pre_v3_checkpoint_is_never_resumed(tmp_path, version: int) -> None:
    checkpoint = tmp_path / "vertical-catalog-checkpoint.json"
    checkpoint.write_text(
        json.dumps(
            {
                "schema_version": version,
                "surface_contract": "step1_browse_verticals_scoped_v2",
                "roots": ["10", "New Orders"],
                "pending": [["New Orders"]],
                "branches": [],
                "leaves": [],
                "failed": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="verified Step-1 route/surface contract"):
        load_checkpoint(checkpoint)
