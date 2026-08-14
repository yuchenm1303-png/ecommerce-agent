from __future__ import annotations

from app.makro.vertical_catalog import (
    build_catalog_payload,
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
