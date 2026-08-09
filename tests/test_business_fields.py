from app.business_fields import generate_listing_sku, generated_business_bundle


URL = "https://detail.1688.com/offer/850845635717.html?spm=tracking"


def test_generated_listing_sku_is_stable_numeric_and_ignores_query_noise():
    first = generate_listing_sku(URL)
    second = generate_listing_sku("https://detail.1688.com/offer/850845635717.html")
    assert first == second
    assert first.isdigit()
    assert len(first) == 12


def test_generated_sku_is_explicit_business_rule_not_product_evidence():
    bundle = generated_business_bundle(URL)
    items = bundle.candidates(("SKU ID",))
    assert len(items) == 1
    assert items[0].source_type == "rule"
    assert items[0].value == generate_listing_sku(URL)
