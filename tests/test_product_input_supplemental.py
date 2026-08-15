from __future__ import annotations

from app.product_input import validate_product_input


def test_supplier_url_can_carry_raw_customer_supplement_files() -> None:
    mode = validate_product_input(
        product_url="https://detail.1688.com/offer/123.html",
        product_files=("customer-spec.pdf", "package-photo.jpg"),
    )
    assert mode == "supplier_url"


def test_files_only_remain_customer_product_pack() -> None:
    assert validate_product_input(product_files=("customer-spec.pdf",)) == "customer_product_pack"


def test_empty_product_input_is_rejected() -> None:
    try:
        validate_product_input()
    except ValueError as exc:
        assert "商品输入不能为空" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("empty product input must be rejected")
