from __future__ import annotations

import pytest

from app.supplier_url_identity import supplier_request_identity
from gui.batch_model import normalize_batch_urls


def test_supplier_request_identity_only_normalizes_scheme_host_and_fragment() -> None:
    first = "HTTPS://SUPPLIER.EXAMPLE/Product/ABC/?SKU=Red#details"
    same_request = "https://supplier.example/Product/ABC/?SKU=Red#reviews"

    assert supplier_request_identity(first) == (
        "https://supplier.example/Product/ABC/?SKU=Red"
    )
    assert supplier_request_identity(first) == supplier_request_identity(same_request)


def test_supplier_request_identity_preserves_path_query_case_and_trailing_slash() -> None:
    upper_path = "https://supplier.example/Product/ABC?sku=Red"
    lower_path = "https://supplier.example/product/ABC?sku=Red"
    upper_query = "https://supplier.example/Product/ABC?sku=Red"
    lower_query = "https://supplier.example/Product/ABC?sku=red"
    slash = "https://supplier.example/Product/ABC/"
    no_slash = "https://supplier.example/Product/ABC"

    assert supplier_request_identity(upper_path) != supplier_request_identity(lower_path)
    assert supplier_request_identity(upper_query) != supplier_request_identity(lower_query)
    assert supplier_request_identity(slash) != supplier_request_identity(no_slash)


def test_batch_dedupe_uses_request_identity_without_collapsing_distinct_variants() -> None:
    text = "\n".join(
        (
            "HTTPS://SUPPLIER.EXAMPLE/Product/ABC?sku=Red#details",
            "https://supplier.example/Product/ABC?sku=Red#reviews",
            "https://supplier.example/Product/ABC?sku=red",
            "https://supplier.example/Product/ABC/?sku=Red",
        )
    )

    assert normalize_batch_urls(text) == [
        "HTTPS://SUPPLIER.EXAMPLE/Product/ABC?sku=Red#details",
        "https://supplier.example/Product/ABC?sku=red",
        "https://supplier.example/Product/ABC/?sku=Red",
    ]


def test_supplier_request_identity_rejects_non_http_urls() -> None:
    with pytest.raises(ValueError, match="http\\(s\\)"):
        supplier_request_identity("file:///tmp/product.html")
