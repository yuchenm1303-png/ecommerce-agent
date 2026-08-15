import pytest

import app.business_fields as business_fields
from app.business_fields import (
    BUSINESS_ATTRIBUTE_ALIASES,
    MAKRO_ACCOUNT_FIXED_DEFAULTS,
    generate_listing_sku,
    generated_business_bundle,
    is_business_question,
)


URL = "https://detail.1688.com/offer/850845635717.html?spm=tracking"


def test_generated_listing_sku_is_fresh_12_digit_numeric(monkeypatch):
    draws = iter((7, 8))
    limits: list[int] = []

    def fake_randbelow(limit: int) -> int:
        limits.append(limit)
        return next(draws)

    monkeypatch.setattr(business_fields.secrets, "randbelow", fake_randbelow)

    first = generate_listing_sku(URL)
    second = generate_listing_sku("https://detail.1688.com/offer/850845635717.html")

    assert first == "100000000007"
    assert second == "100000000008"
    assert first != second
    assert first.isdigit() and second.isdigit()
    assert len(first) == 12 and len(second) == 12
    assert limits == [900_000_000_000, 900_000_000_000]


def test_generated_sku_is_explicit_business_rule_not_product_evidence():
    sku = "812345678901"
    bundle = generated_business_bundle(URL, sku=sku)
    items = bundle.candidates(("SKU ID",))

    assert len(items) == 1
    assert items[0].source_type == "rule"
    assert items[0].source_reference == "generated:fresh-listing-sku"
    assert items[0].value == sku


def test_generated_business_bundle_rejects_invalid_explicit_sku():
    with pytest.raises(ValueError, match="12 位纯数字"):
        generated_business_bundle(URL, sku="not-a-sku")


def test_account_fixed_defaults_are_single_config_source_for_all_listings():
    bundle = generated_business_bundle(URL, sku="812345678901")
    expected = {
        "flipkart_selling_price": "6000",
        "minimum_order_quantity": "5000",
        "max_order_quantity_allowed": "99",
        "service_profile": "FBS",
        "shipping_days": "14",
        "forbid_shipping": "National",
        "country_of_origin": "China",
        "manufacturer_details": "LILI",
        "packer_details": "LILI",
        "importer_details": "LILI",
    }

    assert {key: value for key, value, _source in MAKRO_ACCOUNT_FIXED_DEFAULTS} == expected
    for attribute_key, expected_value in expected.items():
        items = bundle.candidates((attribute_key, *BUSINESS_ATTRIBUTE_ALIASES[attribute_key]))
        assert len(items) == 1
        assert items[0].value == expected_value
        assert items[0].source_type == "config"
        assert items[0].source_reference.startswith("account-default:")
        assert items[0].confidence == 1.0


def test_account_fixed_labels_are_business_fields_and_skip_product_reasoning():
    labels = (
        "Your selling price",
        "Minimum Order Quantity (MinOQ)",
        "Maximum Order Quantity (MaxOQ)",
        "Fulfilment by",
        "Pick Pack SLA",
        "Selling region preference",
        "Country Of Origin",
        "Manufacturer Details",
        "Packer Details",
        "Importer Details",
    )
    assert all(is_business_question(label) for label in labels)


def test_selling_price_is_fixed_while_base_price_and_listing_status_remain_unset():
    bundle = generated_business_bundle(URL, sku="812345678901")
    assert bundle.candidates(("mrp", "Base Price")) == []
    selling = bundle.candidates(("flipkart_selling_price", "Your selling price"))
    assert len(selling) == 1
    assert selling[0].value == "6000"
    assert selling[0].source_type == "config"
    assert bundle.candidates(("listing_status", "Listing Status")) == []