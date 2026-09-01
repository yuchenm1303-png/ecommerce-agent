from __future__ import annotations

from typing import Protocol

from ..scope import CommerceScope
from .domain import Product, SourceProduct


class ProductRepository(Protocol):
    """Persistence boundary for product aggregates."""

    def get(self, scope: CommerceScope, product_id: str) -> Product | None:
        ...

    def add(self, product: Product) -> None:
        ...

    def save(self, product: Product) -> None:
        ...


class SourceProductRepository(Protocol):
    """Persistence boundary for supplier identities and explicit bindings."""

    def get(self, scope: CommerceScope, source_product_id: str) -> SourceProduct | None:
        ...

    def get_by_request_identity(
        self,
        scope: CommerceScope,
        request_identity: str,
    ) -> SourceProduct | None:
        ...

    def add(self, source_product: SourceProduct) -> None:
        ...


__all__ = ["ProductRepository", "SourceProductRepository"]
