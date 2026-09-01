from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.commerce.catalog.domain import BindingMethod, Product, SourceProduct
from app.commerce.channels.domain import ChannelAccountIdentity
from app.commerce.infrastructure.repositories import (
    RecordChannelAccountRepository,
    RecordChannelListingRepository,
    RecordProductRepository,
    RecordSourceProductRepository,
)
from app.commerce.listings.domain import ChannelListing
from app.commerce.scope import CommerceScope


_MIGRATION = Path("supabase/migrations/20260901100000_commerce_foundation_v1.sql")


class _MemoryGateway:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {}

    def _rows(self, table: str) -> list[dict[str, Any]]:
        return self.tables.setdefault(table, [])

    @staticmethod
    def _matches(row: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
        return all(row.get(key) == value for key, value in filters.items())

    def get_one(self, table: str, filters: Mapping[str, Any]) -> dict[str, Any] | None:
        matches = [row for row in self._rows(table) if self._matches(row, filters)]
        if len(matches) > 1:
            raise AssertionError(f"fake gateway expected at most one {table} row")
        return None if not matches else dict(matches[0])

    def get_many(self, table: str, filters: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        return tuple(dict(row) for row in self._rows(table) if self._matches(row, filters))

    def insert(self, table: str, row: Mapping[str, Any]) -> None:
        self._rows(table).append(dict(row))

    def upsert(
        self,
        table: str,
        row: Mapping[str, Any],
        *,
        conflict_columns: Sequence[str],
    ) -> None:
        candidate = dict(row)
        for index, existing in enumerate(self._rows(table)):
            if all(existing.get(column) == candidate.get(column) for column in conflict_columns):
                self._rows(table)[index] = candidate
                return
        self._rows(table).append(candidate)


def test_record_repositories_round_trip_commerce_aggregates() -> None:
    gateway = _MemoryGateway()
    scope = CommerceScope("11111111-1111-1111-1111-111111111111")

    product = Product.new(
        scope=scope,
        display_name="DEW Shampoo",
        product_type_en="shampoo",
        brand="DEW",
    )
    size_variant = product.add_variant(sku="DEW-500", option_values={"Size": "500ml"})

    products = RecordProductRepository(gateway)
    products.add(product)
    loaded_product = products.get(scope, product.product_id)

    assert loaded_product is not None
    assert loaded_product.product_id == product.product_id
    assert len(loaded_product.variants) == 2
    assert loaded_product.default_variant.is_default is True
    assert any(item.variant_id == size_variant.variant_id for item in loaded_product.variants)

    source = SourceProduct.bind(
        scope=scope,
        product_id=product.product_id,
        variant_id=size_variant.variant_id,
        supplier_url="https://example.com/product?id=42",
        request_identity="https://example.com/product?id=42",
        binding_method=BindingMethod.AI_SEMANTIC_MATCH,
        source_system="example",
        external_product_id="42",
    )
    sources = RecordSourceProductRepository(gateway)
    sources.add(source)
    loaded_source = sources.get_by_request_identity(scope, source.request_identity)

    assert loaded_source == source

    account = ChannelAccountIdentity.register(
        scope=scope,
        channel="makro",
        label="Makro Store A",
        channel_account_id="store-a",
    )
    accounts = RecordChannelAccountRepository(gateway)
    accounts.add(account)
    loaded_account = accounts.get(scope, account.channel_account_id)

    assert loaded_account == account
    persisted_account_row = gateway.get_one(
        "commerce_channel_accounts",
        {"workspace_id": scope.workspace_id, "channel_account_id": "store-a"},
    )
    assert persisted_account_row is not None
    assert set(persisted_account_row) == {
        "workspace_id",
        "channel_account_id",
        "channel",
        "label",
        "status",
        "created_at",
        "updated_at",
    }

    listing = ChannelListing.draft(
        scope=scope,
        product_id=product.product_id,
        variant_id=size_variant.variant_id,
        channel="makro",
        channel_account_id=account.channel_account_id,
    )
    listings = RecordChannelListingRepository(gateway)
    listings.add(listing)

    assert listings.get(scope, listing.listing_id) == listing
    assert listings.find_for_variant_account(
        scope,
        variant_id=size_variant.variant_id,
        channel="makro",
        channel_account_id=account.channel_account_id,
    ) == (listing,)


def _table_block(sql: str, table: str) -> str:
    pattern = rf"create table if not exists public\.{re.escape(table)}\s*\((.*?)\n\);"
    match = re.search(pattern, sql, flags=re.IGNORECASE | re.DOTALL)
    assert match is not None, f"missing CREATE TABLE for {table}"
    return match.group(1).casefold()


def test_commerce_migration_reuses_existing_tenant_boundary_and_denies_desktop_access() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", sql.casefold())
    tables = (
        "commerce_workspaces",
        "commerce_products",
        "commerce_product_variants",
        "commerce_source_products",
        "commerce_channel_accounts",
        "commerce_channel_listings",
    )

    assert "references public.listing_monitor_tenants(id)" in normalized
    assert "select id from public.listing_monitor_tenants" in normalized

    for table in tables:
        assert f"create table if not exists public.{table}" in normalized
        assert f"alter table public.{table} enable row level security" in normalized
        assert f"revoke all on table public.{table} from anon, authenticated" in normalized
        assert f"grant select, insert, update, delete on table public.{table} to service_role" in normalized


def test_commerce_migration_keeps_credentials_out_of_server_channel_identity() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    block = _table_block(sql, "commerce_channel_accounts")

    for forbidden in (
        "profile_key",
        "password",
        "cookie",
        "access_token",
        "refresh_token",
        "api_key",
        "oauth_secret",
    ):
        assert forbidden not in block


def test_commerce_migration_enforces_mechanical_identity_boundaries() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", sql.casefold())

    assert "extensions.digest(request_identity, 'sha256')" in normalized
    assert (
        "foreign key (workspace_id, variant_id, product_id) references "
        "public.commerce_product_variants(workspace_id, variant_id, product_id)"
    ) in normalized
    assert (
        "foreign key (workspace_id, channel_account_id, channel) references "
        "public.commerce_channel_accounts(workspace_id, channel_account_id, channel)"
    ) in normalized
    assert "where is_default" in normalized
    assert "status <> 'active' or btrim(external_listing_id) <> ''" in normalized
