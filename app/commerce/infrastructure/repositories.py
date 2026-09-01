from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ..catalog.domain import (
    BindingMethod,
    Product,
    ProductStatus,
    ProductVariant,
    SourceProduct,
)
from ..catalog.repository import ProductRepository, SourceProductRepository
from ..channels.domain import ChannelAccountIdentity, ChannelAccountStatus
from ..channels.repository import ChannelAccountRepository
from ..listings.domain import ChannelListing, ListingStatus
from ..listings.repository import ChannelListingRepository
from ..scope import CommerceScope
from .records import CommerceRecordGateway


_PRODUCTS = "commerce_products"
_VARIANTS = "commerce_product_variants"
_SOURCES = "commerce_source_products"
_CHANNEL_ACCOUNTS = "commerce_channel_accounts"
_LISTINGS = "commerce_channel_listings"


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("persisted datetime must not be empty")
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _scope(workspace_id: Any) -> CommerceScope:
    return CommerceScope(str(workspace_id or ""))


def _options(value: Any) -> tuple[tuple[str, str], ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("persisted option_values must be a JSON object")
    return tuple(sorted(((str(k), str(v)) for k, v in value.items()), key=lambda item: item[0].casefold()))


def _options_json(values: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {str(name): str(value) for name, value in values}


def _product_row(product: Product) -> dict[str, Any]:
    return {
        "workspace_id": product.scope.workspace_id,
        "product_id": product.product_id,
        "display_name": product.display_name,
        "product_type_en": product.product_type_en,
        "brand": product.brand,
        "status": product.status.value,
        "created_at": _iso(product.created_at),
        "updated_at": _iso(product.updated_at),
    }


def _variant_row(variant: ProductVariant) -> dict[str, Any]:
    return {
        "workspace_id": variant.scope.workspace_id,
        "variant_id": variant.variant_id,
        "product_id": variant.product_id,
        "sku": variant.sku,
        "option_values": _options_json(variant.option_values),
        "is_default": variant.is_default,
        "created_at": _iso(variant.created_at),
    }


def _source_row(source: SourceProduct) -> dict[str, Any]:
    return {
        "workspace_id": source.scope.workspace_id,
        "source_product_id": source.source_product_id,
        "product_id": source.product_id,
        "variant_id": source.variant_id,
        "supplier_url": source.supplier_url,
        "request_identity": source.request_identity,
        "binding_method": source.binding_method.value,
        "source_system": source.source_system,
        "external_product_id": source.external_product_id,
        "created_at": _iso(source.created_at),
    }


def _channel_account_row(account: ChannelAccountIdentity) -> dict[str, Any]:
    return {
        "workspace_id": account.scope.workspace_id,
        "channel_account_id": account.channel_account_id,
        "channel": account.channel,
        "label": account.label,
        "status": account.status.value,
        "created_at": _iso(account.created_at),
        "updated_at": _iso(account.updated_at),
    }


def _listing_row(listing: ChannelListing) -> dict[str, Any]:
    return {
        "workspace_id": listing.scope.workspace_id,
        "listing_id": listing.listing_id,
        "product_id": listing.product_id,
        "variant_id": listing.variant_id,
        "channel": listing.channel,
        "channel_account_id": listing.channel_account_id,
        "external_listing_id": listing.external_listing_id,
        "status": listing.status.value,
        "created_at": _iso(listing.created_at),
        "updated_at": _iso(listing.updated_at),
    }


class RecordProductRepository(ProductRepository):
    def __init__(self, gateway: CommerceRecordGateway) -> None:
        self._gateway = gateway

    def get(self, scope: CommerceScope, product_id: str) -> Product | None:
        row = self._gateway.get_one(
            _PRODUCTS,
            {"workspace_id": scope.workspace_id, "product_id": product_id},
        )
        if row is None:
            return None
        variants = tuple(
            ProductVariant(
                variant_id=str(item["variant_id"]),
                product_id=str(item["product_id"]),
                scope=_scope(item["workspace_id"]),
                sku=str(item.get("sku") or ""),
                option_values=_options(item.get("option_values")),
                is_default=bool(item.get("is_default")),
                created_at=_dt(item["created_at"]),
            )
            for item in self._gateway.get_many(
                _VARIANTS,
                {"workspace_id": scope.workspace_id, "product_id": product_id},
            )
        )
        return Product(
            product_id=str(row["product_id"]),
            scope=_scope(row["workspace_id"]),
            display_name=str(row["display_name"]),
            product_type_en=str(row.get("product_type_en") or ""),
            brand=str(row.get("brand") or ""),
            status=ProductStatus(str(row["status"])),
            variants=list(variants),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    def add(self, product: Product) -> None:
        self._gateway.insert(_PRODUCTS, _product_row(product))
        for variant in product.variants:
            self._gateway.insert(_VARIANTS, _variant_row(variant))

    def save(self, product: Product) -> None:
        self._gateway.upsert(
            _PRODUCTS,
            _product_row(product),
            conflict_columns=("workspace_id", "product_id"),
        )
        for variant in product.variants:
            self._gateway.upsert(
                _VARIANTS,
                _variant_row(variant),
                conflict_columns=("workspace_id", "variant_id"),
            )


class RecordSourceProductRepository(SourceProductRepository):
    def __init__(self, gateway: CommerceRecordGateway) -> None:
        self._gateway = gateway

    @staticmethod
    def _from_row(row: Mapping[str, Any]) -> SourceProduct:
        return SourceProduct(
            source_product_id=str(row["source_product_id"]),
            scope=_scope(row["workspace_id"]),
            product_id=str(row["product_id"]),
            variant_id=str(row["variant_id"]),
            supplier_url=str(row["supplier_url"]),
            request_identity=str(row["request_identity"]),
            binding_method=BindingMethod(str(row["binding_method"])),
            source_system=str(row.get("source_system") or ""),
            external_product_id=str(row.get("external_product_id") or ""),
            created_at=_dt(row["created_at"]),
        )

    def get(self, scope: CommerceScope, source_product_id: str) -> SourceProduct | None:
        row = self._gateway.get_one(
            _SOURCES,
            {"workspace_id": scope.workspace_id, "source_product_id": source_product_id},
        )
        return None if row is None else self._from_row(row)

    def get_by_request_identity(
        self,
        scope: CommerceScope,
        request_identity: str,
    ) -> SourceProduct | None:
        row = self._gateway.get_one(
            _SOURCES,
            {"workspace_id": scope.workspace_id, "request_identity": request_identity},
        )
        return None if row is None else self._from_row(row)

    def add(self, source_product: SourceProduct) -> None:
        self._gateway.insert(_SOURCES, _source_row(source_product))


class RecordChannelAccountRepository(ChannelAccountRepository):
    def __init__(self, gateway: CommerceRecordGateway) -> None:
        self._gateway = gateway

    @staticmethod
    def _from_row(row: Mapping[str, Any]) -> ChannelAccountIdentity:
        return ChannelAccountIdentity(
            channel_account_id=str(row["channel_account_id"]),
            scope=_scope(row["workspace_id"]),
            channel=str(row["channel"]),
            label=str(row["label"]),
            status=ChannelAccountStatus(str(row["status"])),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    def get(
        self,
        scope: CommerceScope,
        channel_account_id: str,
    ) -> ChannelAccountIdentity | None:
        row = self._gateway.get_one(
            _CHANNEL_ACCOUNTS,
            {"workspace_id": scope.workspace_id, "channel_account_id": channel_account_id},
        )
        return None if row is None else self._from_row(row)

    def list_for_channel(
        self,
        scope: CommerceScope,
        channel: str,
    ) -> tuple[ChannelAccountIdentity, ...]:
        rows = self._gateway.get_many(
            _CHANNEL_ACCOUNTS,
            {"workspace_id": scope.workspace_id, "channel": str(channel or "").strip().casefold()},
        )
        return tuple(self._from_row(row) for row in rows)

    def add(self, account: ChannelAccountIdentity) -> None:
        self._gateway.insert(_CHANNEL_ACCOUNTS, _channel_account_row(account))

    def save(self, account: ChannelAccountIdentity) -> None:
        self._gateway.upsert(
            _CHANNEL_ACCOUNTS,
            _channel_account_row(account),
            conflict_columns=("workspace_id", "channel_account_id"),
        )


class RecordChannelListingRepository(ChannelListingRepository):
    def __init__(self, gateway: CommerceRecordGateway) -> None:
        self._gateway = gateway

    @staticmethod
    def _from_row(row: Mapping[str, Any]) -> ChannelListing:
        return ChannelListing(
            listing_id=str(row["listing_id"]),
            scope=_scope(row["workspace_id"]),
            product_id=str(row["product_id"]),
            variant_id=str(row["variant_id"]),
            channel=str(row["channel"]),
            channel_account_id=str(row["channel_account_id"]),
            external_listing_id=str(row.get("external_listing_id") or ""),
            status=ListingStatus(str(row["status"])),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    def get(self, scope: CommerceScope, listing_id: str) -> ChannelListing | None:
        row = self._gateway.get_one(
            _LISTINGS,
            {"workspace_id": scope.workspace_id, "listing_id": listing_id},
        )
        return None if row is None else self._from_row(row)

    def find_for_variant_account(
        self,
        scope: CommerceScope,
        variant_id: str,
        channel_account_id: str,
    ) -> tuple[ChannelListing, ...]:
        rows = self._gateway.get_many(
            _LISTINGS,
            {
                "workspace_id": scope.workspace_id,
                "variant_id": variant_id,
                "channel_account_id": channel_account_id,
            },
        )
        return tuple(self._from_row(row) for row in rows)

    def add(self, listing: ChannelListing) -> None:
        self._gateway.insert(_LISTINGS, _listing_row(listing))

    def save(self, listing: ChannelListing) -> None:
        self._gateway.upsert(
            _LISTINGS,
            _listing_row(listing),
            conflict_columns=("workspace_id", "listing_id"),
        )


__all__ = [
    "RecordChannelAccountRepository",
    "RecordChannelListingRepository",
    "RecordProductRepository",
    "RecordSourceProductRepository",
]
