import pytest

import app.business_fields as business_fields
from app.business_fields import generate_listing_sku, generated_business_bundle


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
