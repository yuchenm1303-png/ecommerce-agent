from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .evidence_contract import ProductIdentity
from .evidence_pipeline import (
    add_fact,
    bundle_from_catalog_answers,
    bundle_from_facts_json,
    merge_bundles,
)
from .qa_catalog import QuestionCatalog
from .source_bundle import ProductSourceBundle, bundle_from_product_table, normalize_key


_TRUSTED_IDENTITY_SOURCE_TYPES = {
    "structured",
    "customer_answer",
    "business",
    "config",
    "rule",
}


@dataclass(slots=True, frozen=True)
class ResolutionInputSpec:
    """Explicit seller/customer inputs shared by resolver, planner and executor.

    Supplier pages and images remain present on the spec because callers use the
    same object to build the AI source pack, but this module never interprets
    them into product attributes. Product semantics belong to the AI resolver.
    """

    sku: str = ""
    expected_model: str = ""
    expected_brand: str = ""
    product_table: str | None = None
    facts_json: tuple[str, ...] = ()
    supplier_snapshots: tuple[str, ...] = ()
    official_snapshots: tuple[str, ...] = ()
    supplemental_text: str = ""
    supplemental_text_file: str | None = None
    image_paths: tuple[str, ...] = ()
    product_url: str | None = None

    @property
    def expected_identity(self) -> ProductIdentity:
        return ProductIdentity(
            sku=self.sku,
            model_number=self.expected_model,
            brand=self.expected_brand,
        )


@dataclass(slots=True)
class ResolutionInputResult:
    bundle: ProductSourceBundle
    expected_identity: ProductIdentity
    warnings: list[str] = field(default_factory=list)


def _trusted_identity_value(
    bundle: ProductSourceBundle,
    keys: tuple[str, ...],
    *,
    identity_name: str,
) -> str:
    candidates = [
        item
        for item in bundle.candidates(keys)
        if item.source_type in _TRUSTED_IDENTITY_SOURCE_TYPES
        and isinstance(item.value, str)
        and item.value.strip()
    ]
    if not candidates:
        return ""

    canonical: dict[str, list[str]] = {}
    for item in candidates:
        canonical.setdefault(normalize_key(item.value), []).append(item.value.strip())
    if len(canonical) > 1:
        details = " | ".join(
            f"{item.source_type}:{item.source_reference}={item.value}"
            for item in candidates
        )
        raise ValueError(f"可信来源中的 {identity_name} 身份锚点互相冲突：{details}")

    candidates.sort(key=lambda item: (item.priority, -item.confidence, item.source_reference))
    return str(candidates[0].value).strip()


def _coalesce_identity_value(
    explicit: str,
    derived: str,
    *,
    identity_name: str,
) -> str:
    explicit = explicit.strip()
    derived = derived.strip()
    if explicit and derived and normalize_key(explicit) != normalize_key(derived):
        raise ValueError(
            f"显式 {identity_name}={explicit!r} 与可信资料推导值 {derived!r} 冲突。"
        )
    return explicit or derived


def _derive_expected_identity(
    spec: ResolutionInputSpec,
    trusted_bundle: ProductSourceBundle,
) -> ProductIdentity:
    derived_sku = trusted_bundle.sku or _trusted_identity_value(
        trusted_bundle,
        ("SKU", "SKU ID", "sku_id"),
        identity_name="SKU",
    )
    derived_model = _trusted_identity_value(
        trusted_bundle,
        ("Model Number", "Model", "model_number", "型号"),
        identity_name="Model Number",
    )
    derived_brand = _trusted_identity_value(
        trusted_bundle,
        ("Brand", "Brand Name", "brand_name", "品牌"),
        identity_name="Brand",
    )
    return ProductIdentity(
        sku=_coalesce_identity_value(spec.sku, derived_sku, identity_name="SKU"),
        model_number=_coalesce_identity_value(
            spec.expected_model,
            derived_model,
            identity_name="Model Number",
        ),
        brand=_coalesce_identity_value(
            spec.expected_brand,
            derived_brand,
            identity_name="Brand",
        ),
    )


def customer_context_for_resolution(
    catalog: QuestionCatalog,
    spec: ResolutionInputSpec,
) -> str:
    """Return the exact customer text source shared by AI resolver and rebind."""

    parts = [catalog.preamble_text, spec.supplemental_text]
    if spec.supplemental_text_file:
        parts.append(Path(spec.supplemental_text_file).read_text(encoding="utf-8"))
    return "\n".join(part for part in parts if part and part.strip()).strip()


def build_resolution_inputs(
    catalog: QuestionCatalog,
    spec: ResolutionInputSpec,
) -> ResolutionInputResult:
    """Load explicit seller/customer data without interpreting product semantics.

    Customer Answer cells, explicit SKU, product tables and manual facts remain
    trusted structured inputs. Supplier/official snapshots and images are not
    converted to local product facts; they are raw AI sources. Workbook preamble
    stays as one canonical text source and is not locally re-parsed into a second
    set of pseudo-facts.
    """

    customer_context = customer_context_for_resolution(catalog, spec)
    trusted_bundles: list[ProductSourceBundle] = [
        bundle_from_catalog_answers(
            catalog,
            sku=spec.sku,
            image_paths=spec.image_paths,
            product_url=spec.product_url,
            supplemental_text=customer_context,
        )
    ]
    warnings: list[str] = []

    if spec.sku.strip():
        explicit_sku = ProductSourceBundle(sku=spec.sku.strip())
        add_fact(
            explicit_sku,
            key="SKU",
            value=spec.sku.strip(),
            source_type="business",
            source_reference="runtime:--sku",
            confidence=1.0,
            evidence_text=f"SKU={spec.sku.strip()}",
            note="explicit seller-controlled SKU",
        )
        trusted_bundles.append(explicit_sku)

    if spec.product_table:
        trusted_bundles.append(
            bundle_from_product_table(
                spec.product_table,
                sku=spec.sku or None,
            )
        )

    for path in spec.facts_json:
        trusted_bundles.append(bundle_from_facts_json(path, sku=spec.sku))

    trusted_bundle = merge_bundles(*trusted_bundles)
    expected = _derive_expected_identity(spec, trusted_bundle)
    if customer_context:
        warnings.append(f"customer_context_chars={len(customer_context)}")

    return ResolutionInputResult(
        bundle=trusted_bundle,
        expected_identity=expected,
        warnings=warnings,
    )
