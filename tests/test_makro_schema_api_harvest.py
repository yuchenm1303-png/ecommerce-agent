from __future__ import annotations

from pathlib import Path

import pytest

from app.makro.schema_api_harvest import (
    MakroSchemaApiHarvestError,
    attribute_contract,
    build_global_catalog,
    build_registry,
    build_vertical_registry_entry,
    extract_attribute_records,
    extract_vertical_catalog,
    sanitize_schema_payload,
    split_vertical_payload,
    variant_definition_path,
    vertical_definition_path,
    vertical_definition_v2_path,
)


ROOT = Path(__file__).resolve().parents[1]
CLI_SOURCE = (ROOT / "makro_harvest_schema_api.py").read_text(encoding="utf-8")


def test_extract_vertical_catalog_from_nested_complete_tree_deduplicates():
    payload = {
        "categories": [
            {
                "name": "Home",
                "children": [
                    {"verticalName": "air_purifier", "verticalDisplayName": "Air Purifiers"},
                    {"verticalName": "vacuum_cleaner", "verticalDisplayName": "Vacuum Cleaners"},
                ],
            },
            {"verticalName": "air_purifier", "verticalDisplayName": "Air Purifiers"},
        ]
    }
    result = extract_vertical_catalog(payload)
    assert result == [
        {"vertical": "air_purifier", "display_name": "Air Purifiers", "description": ""},
        {"vertical": "vacuum_cleaner", "display_name": "Vacuum Cleaners", "description": ""},
    ]


def test_split_vertical_payload_accepts_exact_keyed_batch():
    payload = {
        "verticalDefinitions": {
            "air_purifier": {"attributes": [{"attributeName": "air_flow_level"}]},
            "vacuum_cleaner": {"attributes": [{"attributeName": "power"}]},
        }
    }
    result = split_vertical_payload(payload, ["air_purifier", "vacuum_cleaner"])
    assert set(result) == {"air_purifier", "vacuum_cleaner"}


def test_split_vertical_payload_accepts_exact_named_objects():
    payload = {
        "data": [
            {"verticalName": "air_purifier", "verticalProperties": {"x": 1}},
            {"vertical": "vacuum_cleaner", "verticalProperties": {"x": 2}},
        ]
    }
    result = split_vertical_payload(payload, ["air_purifier", "vacuum_cleaner"])
    assert result["air_purifier"]["verticalName"] == "air_purifier"
    assert result["vacuum_cleaner"]["vertical"] == "vacuum_cleaner"


def test_split_vertical_payload_does_not_guess_multi_vertical_unknown_envelope():
    payload = {"attributes": [{"attributeName": "unknown"}]}
    assert split_vertical_payload(payload, ["a", "b"]) == {}
    assert split_vertical_payload(payload, ["a"]) == {"a": payload}


def test_sanitize_schema_payload_removes_account_credentials_recursively():
    payload = {
        "sellerId": "secret-seller",
        "csrf_token": "secret-csrf",
        "data": {
            "verticalName": "air_purifier",
            "attributes": [
                {
                    "attributeName": "air_flow_level",
                    "token": "secret-token",
                    "attributeType": "NUMBER",
                }
            ],
        },
    }
    safe = sanitize_schema_payload(payload)
    rendered = repr(safe)
    assert "secret-seller" not in rendered
    assert "secret-csrf" not in rendered
    assert "secret-token" not in rendered
    assert "air_purifier" in rendered
    assert "air_flow_level" in rendered


def test_attribute_contract_preserves_makro_mechanical_type_and_qualifier():
    raw = {
        "attributeName": "depth",
        "attributeDisplayName": "Depth",
        "attributeType": "NUMBER",
        "attributeValueType": "DECIMAL",
        "attributePriority": "MANDATORY",
        "maxAttributeValLength": 12,
        "defaultQualifier": "cm",
        "qualifierAllowedValues": [{"value": "cm"}, {"value": "mm"}],
        "attributeDependency": {"dependsOn": "size"},
        "isItemizableAttribute": False,
    }
    contract = attribute_contract(raw)
    assert contract["attribute_name"] == "depth"
    assert contract["display_name"] == "Depth"
    assert contract["attribute_type"] == "NUMBER"
    assert contract["value_type"] == "DECIMAL"
    assert contract["priority"] == "MANDATORY"
    assert contract["qualifier_allowed_values"] == ["cm", "mm"]
    assert contract["has_dependency"] is True


def test_extract_attribute_records_deduplicates_same_definition_signature():
    payload = {
        "attributes": [
            {"attributeName": "colour", "attributeType": "TEXT", "attributeValueType": "STRING"},
            {"attributeName": "colour", "attributeType": "TEXT", "attributeValueType": "STRING"},
            {"attributeName": "depth", "attributeType": "NUMBER", "attributeValueType": "DECIMAL"},
        ]
    }
    result = extract_attribute_records(payload)
    assert len(result) == 2
    assert {item["attributeName"] for item in result} == {"colour", "depth"}


def test_registry_builds_cross_vertical_field_families_and_variant_catalog():
    entry_a = build_vertical_registry_entry(
        vertical="air_purifier",
        catalog_item={"display_name": "Air Purifiers", "description": ""},
        definition={
            "verticalName": "air_purifier",
            "attributes": [
                {
                    "attributeName": "colour",
                    "attributeDisplayName": "Colour",
                    "attributeType": "TEXT",
                    "attributeValueType": "STRING",
                }
            ],
        },
        definition_v2={"verticalName": "air_purifier", "verticalDisplayName": "Air Purifiers"},
        variant_definition={
            "variantAttributes": [
                {
                    "attributeName": "colour",
                    "attributeDisplayName": "Colour",
                    "attributeType": "TEXT",
                    "attributeValueType": "STRING",
                }
            ]
        },
    )
    entry_b = build_vertical_registry_entry(
        vertical="vacuum_cleaner",
        catalog_item={"display_name": "Vacuum Cleaners", "description": ""},
        definition={
            "verticalName": "vacuum_cleaner",
            "attributes": [
                {
                    "attributeName": "power",
                    "attributeDisplayName": "Power",
                    "attributeType": "NUMBER",
                    "attributeValueType": "INTEGER",
                    "qualifierAllowedValues": ["W"],
                }
            ],
        },
        definition_v2={"verticalName": "vacuum_cleaner"},
        variant_definition=None,
    )
    global_catalog = build_global_catalog([entry_a, entry_b])
    assert set(global_catalog["attributes"]) == {"colour", "power"}
    assert global_catalog["attributes"]["colour"]["variant_verticals"] == ["air_purifier"]
    assert any("NUMBER|INTEGER|qualifier" in key for key in global_catalog["field_families"])

    registry = build_registry(
        catalog=[
            {"vertical": "air_purifier", "display_name": "Air Purifiers", "description": ""},
            {"vertical": "vacuum_cleaner", "display_name": "Vacuum Cleaners", "description": ""},
        ],
        vertical_entries=[entry_a, entry_b],
        failures=[],
        batch_fallbacks=[],
        variants_included=True,
    )
    assert registry["stats"]["catalog_vertical_count"] == 2
    assert registry["stats"]["harvested_vertical_count"] == 2
    assert registry["stats"]["unique_attribute_count"] == 2
    assert registry["safety"]["send_to_qc_clicked"] is False


def test_endpoint_builders_are_read_only_get_paths():
    assert vertical_definition_path(["air_purifier", "vacuum_cleaner"]) == (
        "/napi/createProductV2/verticalDefinition?verticals=air_purifier,vacuum_cleaner"
    )
    assert vertical_definition_v2_path(["air_purifier"]).endswith(
        "verticals=air_purifier&context=VERTICAL_PROP"
    )
    assert variant_definition_path("air_purifier").endswith("vertical=air_purifier")


def test_cli_contract_is_new_tab_get_only_and_never_mutates_original_listing():
    assert "context.new_page()" in CLI_SOURCE
    assert "page.expect_response" in CLI_SOURCE
    assert "CATEGORY_TREE_PATH" in CLI_SOURCE
    assert "fetch_partitioned_endpoint(" in CLI_SOURCE
    assert "fetch_many_json(" in CLI_SOURCE
    assert "credentials: 'include'" not in CLI_SOURCE  # transport lives in audited helper
    assert "source_page.goto" not in CLI_SOURCE
    assert "source_page.reload" not in CLI_SOURCE
    assert "source_page.click" not in CLI_SOURCE
    assert ".click(" not in CLI_SOURCE
    assert "Send to QC=False" in CLI_SOURCE
