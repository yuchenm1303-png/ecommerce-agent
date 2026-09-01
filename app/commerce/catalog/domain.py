from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Mapping

from ..scope import CommerceScope


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _required(value: str, *, field_name: str, max_length: int = 500) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} is too long")
    return normalized


def _optional(value: str, *, max_length: int = 500) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if len(normalized) > max_length:
        raise ValueError("value is too long")
    return normalized


def _entity_id(value: str, *, field_name: str) -> str:
    return _required(value, field_name=field_name, max_length=200)


def _new_id() -> str:
    return uuid.uuid4().hex


class ProductStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class BindingMethod(str, Enum):
    """Auditable authority for attaching one supplier source to a product.

    Cross-source semantic equivalence is never inferred mechanically by this
    domain model.  Callers must state who/what established the binding.
    """

    CREATED_NEW_PRODUCT = "created_new_product"
    SAME_SOURCE_IDENTITY = "same_source_identity"
    AI_SEMANTIC_MATCH = "ai_semantic_match"
    USER_CONFIRMED = "user_confirmed"
    IMPORTED = "imported"


@dataclass(frozen=True, slots=True)
class ProductVariant:
    variant_id: str
    product_id: str
    scope: CommerceScope
    sku: str = ""
    option_values: tuple[tuple[str, str], ...] = ()
    is_default: bool = False
    created_at: datetime = field(default_factory=_now_utc)

    def __post_init__(self) -> None:
        object.__setattr__(self, "variant_id", _entity_id(self.variant_id, field_name="variant_id"))
        object.__setattr__(self, "product_id", _entity_id(self.product_id, field_name="product_id"))
        object.__setattr__(self, "sku", _optional(self.sku, max_length=160))

        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_name, raw_value in self.option_values:
            name = _required(raw_name, field_name="variant option name", max_length=120)
            value = _required(raw_value, field_name="variant option value", max_length=240)
            key = name.casefold()
            if key in seen:
                raise ValueError(f"duplicate variant option: {name}")
            seen.add(key)
            normalized.append((name, value))
        normalized.sort(key=lambda item: item[0].casefold())
        object.__setattr__(self, "option_values", tuple(normalized))

    @property
    def option_signature(self) -> tuple[tuple[str, str], ...]:
        return tuple((name.casefold(), value.casefold()) for name, value in self.option_values)

    @classmethod
    def new_default(cls, *, product_id: str, scope: CommerceScope) -> "ProductVariant":
        return cls(
            variant_id=_new_id(),
            product_id=product_id,
            scope=scope,
            is_default=True,
        )

    @classmethod
    def new(
        cls,
        *,
        product_id: str,
        scope: CommerceScope,
        sku: str = "",
        option_values: Mapping[str, str] | Iterable[tuple[str, str]] = (),
    ) -> "ProductVariant":
        values = tuple(option_values.items()) if isinstance(option_values, Mapping) else tuple(option_values)
        return cls(
            variant_id=_new_id(),
            product_id=product_id,
            scope=scope,
            sku=sku,
            option_values=values,
            is_default=False,
        )


@dataclass(slots=True)
class Product:
    product_id: str
    scope: CommerceScope
    display_name: str
    product_type_en: str = ""
    brand: str = ""
    status: ProductStatus = ProductStatus.ACTIVE
    variants: list[ProductVariant] = field(default_factory=list)
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)

    def __post_init__(self) -> None:
        self.product_id = _entity_id(self.product_id, field_name="product_id")
        self.display_name = _required(self.display_name, field_name="display_name", max_length=500)
        self.product_type_en = _optional(self.product_type_en, max_length=240)
        self.brand = _optional(self.brand, max_length=200)
        self.status = ProductStatus(self.status)
        self.variants = list(self.variants)
        self._validate_variants()

    @classmethod
    def new(
        cls,
        *,
        scope: CommerceScope,
        display_name: str,
        product_type_en: str = "",
        brand: str = "",
    ) -> "Product":
        product_id = _new_id()
        return cls(
            product_id=product_id,
            scope=scope,
            display_name=display_name,
            product_type_en=product_type_en,
            brand=brand,
            variants=[ProductVariant.new_default(product_id=product_id, scope=scope)],
        )

    @property
    def default_variant(self) -> ProductVariant:
        for variant in self.variants:
            if variant.is_default:
                return variant
        raise RuntimeError("product does not have a default variant")

    def add_variant(
        self,
        *,
        sku: str = "",
        option_values: Mapping[str, str] | Iterable[tuple[str, str]],
    ) -> ProductVariant:
        variant = ProductVariant.new(
            product_id=self.product_id,
            scope=self.scope,
            sku=sku,
            option_values=option_values,
        )
        if not variant.option_values:
            raise ValueError("non-default variant must define at least one option")
        if any(existing.option_signature == variant.option_signature for existing in self.variants):
            raise ValueError("variant options already exist for this product")
        self.variants.append(variant)
        self.updated_at = _now_utc()
        return variant

    def archive(self) -> None:
        if self.status is not ProductStatus.ARCHIVED:
            self.status = ProductStatus.ARCHIVED
            self.updated_at = _now_utc()

    def _validate_variants(self) -> None:
        if not self.variants:
            raise ValueError("product must contain at least one variant")
        default_count = 0
        signatures: set[tuple[tuple[str, str], ...]] = set()
        ids: set[str] = set()
        for variant in self.variants:
            if variant.product_id != self.product_id:
                raise ValueError("variant belongs to another product")
            if variant.scope != self.scope:
                raise ValueError("variant belongs to another commerce scope")
            if variant.variant_id in ids:
                raise ValueError("duplicate variant_id")
            ids.add(variant.variant_id)
            if variant.is_default:
                default_count += 1
            else:
                if not variant.option_values:
                    raise ValueError("non-default variant must define at least one option")
                if variant.option_signature in signatures:
                    raise ValueError("duplicate variant option signature")
                signatures.add(variant.option_signature)
        if default_count != 1:
            raise ValueError("product must contain exactly one default variant")


@dataclass(frozen=True, slots=True)
class SourceProduct:
    source_product_id: str
    scope: CommerceScope
    product_id: str
    variant_id: str
    supplier_url: str
    request_identity: str
    binding_method: BindingMethod
    source_system: str = ""
    external_product_id: str = ""
    created_at: datetime = field(default_factory=_now_utc)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_product_id",
            _entity_id(self.source_product_id, field_name="source_product_id"),
        )
        object.__setattr__(self, "product_id", _entity_id(self.product_id, field_name="product_id"))
        object.__setattr__(self, "variant_id", _entity_id(self.variant_id, field_name="variant_id"))
        object.__setattr__(
            self,
            "supplier_url",
            _required(self.supplier_url, field_name="supplier_url", max_length=4000),
        )
        object.__setattr__(
            self,
            "request_identity",
            _required(self.request_identity, field_name="request_identity", max_length=4000),
        )
        object.__setattr__(self, "binding_method", BindingMethod(self.binding_method))
        object.__setattr__(self, "source_system", _optional(self.source_system, max_length=120))
        object.__setattr__(
            self,
            "external_product_id",
            _optional(self.external_product_id, max_length=240),
        )

    @classmethod
    def bind(
        cls,
        *,
        scope: CommerceScope,
        product_id: str,
        variant_id: str,
        supplier_url: str,
        request_identity: str,
        binding_method: BindingMethod,
        source_system: str = "",
        external_product_id: str = "",
    ) -> "SourceProduct":
        return cls(
            source_product_id=_new_id(),
            scope=scope,
            product_id=product_id,
            variant_id=variant_id,
            supplier_url=supplier_url,
            request_identity=request_identity,
            binding_method=binding_method,
            source_system=source_system,
            external_product_id=external_product_id,
        )


__all__ = [
    "BindingMethod",
    "Product",
    "ProductStatus",
    "ProductVariant",
    "SourceProduct",
]
