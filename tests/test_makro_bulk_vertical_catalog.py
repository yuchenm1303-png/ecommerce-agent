from __future__ import annotations

from app.makro.bulk_vertical_catalog import (
    CATALOG_SCHEMA_VERSION,
    build_catalog_payload,
    normalize_vertical_rows,
)


def test_normalize_vertical_rows_preserves_order_and_dedupes() -> None:
    rows = [
        {"label": " Select Vertical ", "value": ""},
        {"label": "solar_charge_controller", "value": "solar_charge_controller"},
        {"label": "Solar_Charge_Controller", "value": "duplicate"},
        {"label": " vacuum_bag_sealer ", "value": "vacuum_bag_sealer"},
        {"label": "Gas Stove Oven", "value": "gas_stove_oven"},
    ]

    assert normalize_vertical_rows(rows) == [
        {
            "vertical": "solar_charge_controller",
            "portal_value": "solar_charge_controller",
        },
        {
            "vertical": "vacuum_bag_sealer",
            "portal_value": "vacuum_bag_sealer",
        },
        {
            "vertical": "Gas Stove Oven",
            "portal_value": "gas_stove_oven",
        },
    ]


def test_catalog_payload_keeps_bulk_dropdown_as_authoritative_source() -> None:
    payload = build_catalog_payload(
        [
            {"vertical": "solar_charge_controller", "portal_value": ""},
            {"vertical": "vacuum_bag_sealer", "portal_value": ""},
        ],
        source_url="https://seller.makro.co.za/index.html#dashboard/example",
        extraction_mode="native_select_all_options",
        complete=True,
    )

    assert payload["schema_version"] == CATALOG_SCHEMA_VERSION == 1
    assert payload["source"] == "makro_seller_portal_bulk_product_creation_vertical_dropdown"
    assert payload["complete"] is True
    assert payload["stats"] == {
        "vertical_count": 2,
        "underscore_name_count": 2,
    }
    assert payload["safety"]["vertical_selected"] is False
    assert payload["safety"]["template_downloaded"] is False
    assert payload["safety"]["file_uploaded"] is False
    assert payload["safety"]["send_to_qc_clicked"] is False


def test_catalog_payload_is_not_complete_when_scroll_did_not_reach_stable_bottom() -> None:
    payload = build_catalog_payload(
        [{"vertical": "solar_charge_controller"}],
        source_url="https://seller.makro.co.za/index.html#dashboard/example",
        extraction_mode="custom_dropdown_scroll_to_stable_bottom",
        complete=False,
    )
    assert payload["complete"] is False
    assert payload["stats"]["vertical_count"] == 1
