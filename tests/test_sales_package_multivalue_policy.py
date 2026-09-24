from __future__ import annotations

from app.listing_content_policy import GLOBAL_CONTENT_RULES, field_content_policy


def _sales_package(*, multi_value: bool) -> dict:
    return {
        "attribute_key": "sales_package",
        "label": "Sales Package",
        "section_heading": "Product Description",
        "required": True,
        "multi_value": multi_value,
        "options": [],
        "qualifier_options": [],
        "controls": [],
        "help_text": "",
        "context_text": "",
    }


def test_multi_value_sales_package_requests_one_item_per_live_row() -> None:
    policy = field_content_policy(_sales_package(multi_value=True))

    assert policy["value_shape"] == "one_package_item_per_value"
    assert policy["value_format"] == "<quantity> x <concise item name>"
    assert "one delivered physical item per values[] element" in policy["instruction"]
    assert "1 x Inflatable Pool" in policy["instruction"]
    assert "1 x Electric Air Pump" in policy["instruction"]
    assert "1 x Instruction Manual" in policy["instruction"]
    assert "Do not invent a quantity" in policy["instruction"]


def test_single_value_sales_package_stays_single_input_shape() -> None:
    policy = field_content_policy(_sales_package(multi_value=False))

    assert policy["value_shape"] == "single_package_string"
    assert "single-value" in policy["shape_instruction"]
    assert "separate distinct items with '; '" in policy["shape_instruction"]


def test_global_multi_value_contract_keeps_distinct_values_separate() -> None:
    rule = next(rule for rule in GLOBAL_CONTENT_RULES if "multi_value=true" in rule)

    assert "separate element" in rule
    assert "one live row per value" in rule
