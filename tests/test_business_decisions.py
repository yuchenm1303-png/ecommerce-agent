from __future__ import annotations

from app.business_decisions import (
    PRICE_REFERENCE_DEFAULTS,
    is_user_decision_business_field,
    price_decision_advisory,
    price_reference_advisory,
    user_decision_business_key,
)


def _field(key: str, label: str) -> dict:
    return {
        "attribute_key": key,
        "label": label,
        "required": True,
        "controls": [],
        "options": [],
        "qualifier_options": [],
    }


def test_price_aliases_map_to_price_reference_keys() -> None:
    assert user_decision_business_key("Base Price") == "mrp"
    assert user_decision_business_key("Your selling price") == "flipkart_selling_price"
    assert is_user_decision_business_field(_field("mrp", "Base Price")) is True
    assert is_user_decision_business_field(_field("colour", "Colour")) is False


def test_price_reference_card_explains_existing_automatic_values() -> None:
    advisory = price_reference_advisory(
        _field("flipkart_selling_price", "Your selling price"),
        confirmed_value="5000",
        counterpart_value="6000",
    )

    assert advisory["kind"] == "price_reference"
    assert advisory["auto_write_without_user"] is True
    assert advisory["persistent"] is True
    rows = {row["label"]: row["value"] for row in advisory["rows"]}
    assert rows["参考填写"] == "R 5,000"
    assert rows["Base Price / MRP 参考"] == "R 6,000"
    assert "16.7%" in rows["价格结构"]
    assert "不会暂停等待" in advisory["thought"]
    assert "不是实时市场报价" in advisory["thought"]


def test_price_reference_uses_established_defaults_when_values_are_omitted() -> None:
    assert PRICE_REFERENCE_DEFAULTS == {
        "mrp": "6000",
        "flipkart_selling_price": "5000",
    }
    advisory = price_reference_advisory("Base Price")
    rows = {row["label"]: row["value"] for row in advisory["rows"]}
    assert rows["参考填写"] == "R 6,000"
    assert rows["Selling Price 参考"] == "R 5,000"


def test_price_reference_warns_on_invalid_relation_but_never_blocks_write() -> None:
    advisory = price_reference_advisory(
        "selling price",
        confirmed_value="1800",
        counterpart_value="1600",
    )
    assert "高于 Base Price" in advisory["warning"]
    assert advisory["auto_write_without_user"] is True


def test_legacy_advisory_name_is_non_blocking_alias() -> None:
    advisory = price_decision_advisory(
        "selling price",
        confirmed_value="5000",
        counterpart_value="6000",
    )
    assert advisory["kind"] == "price_reference"
    assert advisory["auto_write_without_user"] is True
