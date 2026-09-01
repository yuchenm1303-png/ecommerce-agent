from __future__ import annotations

import pytest

from app.commerce.catalog.domain import BindingMethod, Product, SourceProduct
from app.commerce.listings.domain import ChannelListing, ListingStatus
from app.commerce.scope import CommerceScope
from app.commerce.unit_of_work import CommerceUnitOfWork


class _NullRepository:
    pass


class _RecordingUnitOfWork(CommerceUnitOfWork):
    products = _NullRepository()
    source_products = _NullRepository()
    listings = _NullRepository()
    events = _NullRepository()

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def _begin(self) -> None:
        self.calls.append("begin")

    def _commit(self) -> None:
        self.calls.append("commit")

    def _rollback(self) -> None:
        self.calls.append("rollback")


def test_new_product_has_one_default_variant_without_requiring_sku() -> None:
    product = Product.new(
        scope=CommerceScope("workspace-a"),
        display_name="DEW Shampoo 500 ml",
        product_type_en="shampoo",
        brand="DEW",
    )

    assert len(product.variants) == 1
    assert product.default_variant.is_default is True
    assert product.default_variant.sku == ""
    assert product.default_variant.product_id == product.product_id


def test_product_rejects_duplicate_variant_options_mechanically() -> None:
    product = Product.new(scope=CommerceScope("workspace-a"), display_name="Product")
    product.add_variant(option_values={"Size": "500ml"})

    with pytest.raises(ValueError, match="already exist"):
        product.add_variant(option_values={"size": "500ML"})


def test_source_binding_records_who_established_cross_source_identity() -> None:
    product = Product.new(scope=CommerceScope("workspace-a"), display_name="Product")

    source = SourceProduct.bind(
        scope=product.scope,
        product_id=product.product_id,
        variant_id=product.default_variant.variant_id,
        supplier_url="https://example.com/item?id=1",
        request_identity="https://example.com/item?id=1",
        binding_method=BindingMethod.AI_SEMANTIC_MATCH,
        source_system="supplier",
    )

    assert source.binding_method is BindingMethod.AI_SEMANTIC_MATCH
    assert source.product_id == product.product_id
    assert source.variant_id == product.default_variant.variant_id


def test_listing_state_is_business_state_not_task_failure_state() -> None:
    product = Product.new(scope=CommerceScope("workspace-a"), display_name="Product")
    listing = ChannelListing.draft(
        scope=product.scope,
        product_id=product.product_id,
        variant_id=product.default_variant.variant_id,
        channel="makro",
        channel_account_id="store-a",
    )

    assert listing.status is ListingStatus.DRAFT
    assert not hasattr(listing, "task_status")
    assert not hasattr(product, "task_status")


def test_active_listing_requires_marketplace_identity() -> None:
    product = Product.new(scope=CommerceScope("workspace-a"), display_name="Product")

    with pytest.raises(ValueError, match="external_listing_id"):
        ChannelListing(
            listing_id="listing-a",
            scope=product.scope,
            product_id=product.product_id,
            variant_id=product.default_variant.variant_id,
            channel="makro",
            channel_account_id="store-a",
            status=ListingStatus.ACTIVE,
        )


def test_unit_of_work_requires_explicit_commit() -> None:
    uow = _RecordingUnitOfWork()
    with uow:
        pass
    assert uow.calls == ["begin", "rollback"]

    uow = _RecordingUnitOfWork()
    with uow:
        uow.commit()
    assert uow.calls == ["begin", "commit"]
