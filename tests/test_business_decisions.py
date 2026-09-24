from __future__ import annotations

import pytest

from app.business_decisions import (
    is_user_decision_business_field,
    price_decision_advisory,
    user_decision_business_key,
)
from app.required_overrides import RequiredOverrideError, required_fallback_override


def _field(key: str, label: str) -> dict:
    return {
        "attribute_key": key,
        "label": label,
        "required": True,
        "controls": [],
        "options": [],
        "qualifier_options": [],
    }


def test_price_aliases_are_explicit_seller_decisions() -> None:
    assert user_decision_business_key("Base Price") == "mrp"
    assert user_decision_business_key("Your selling price") == "flipkart_selling_price"
    assert is_user_decision_business_field(_field("mrp", "Base Price")) is True
    assert is_user_decision_business_field(_field("colour", "Colour")) is False


def test_price_decision_advisory_never_invents_market_reference() -> None:
    advisory = price_decision_advisory(
        _field("flipkart_selling_price", "Your selling price"),
        confirmed_value="1499",
        counterpart_value="1699",
    )

    assert advisory["kind"] == "price_decision"
    assert advisory["auto_write_without_user"] is False
    rows = {row["label"]: row["value"] for row in advisory["rows"]}
    assert rows["当前确认"] == "R 1,499"
    assert rows["Base Price / MRP"] == "R 1,699"
    assert rows["自动占位"] == "已禁用"
    assert rows["价差"].startswith("R 200")
    assert "不会凭空生成市场价" in advisory["thought"]


def test_price_decision_advisory_warns_on_invalid_relation() -> None:
    advisory = price_decision_advisory(
        "selling price",
        confirmed_value="1800",
        counterpart_value="1600",
    )
    assert "高于 Base Price" in advisory["warning"]


@pytest.mark.parametrize(
    ("key", "label"),
    (
        ("mrp", "Base Price"),
        ("flipkart_selling_price", "Your selling price"),
    ),
)
def test_price_fields_can_never_use_required_placeholder(key: str, label: str) -> None:
    with pytest.raises(RequiredOverrideError, match="不允许自动必填兜底"):
        required_fallback_override(_field(key, label))
