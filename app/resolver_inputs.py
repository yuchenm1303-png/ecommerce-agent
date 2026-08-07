from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .evidence_contract import EvidencePacket, ProductIdentity, bundle_from_evidence_packet
from .evidence_pipeline import (
    bundle_from_catalog_answers,
    bundle_from_facts_json,
    bundle_from_key_value_text,
    merge_bundles,
)
from .evidence_validation import validate_evidence_packet
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
    sku: str = ""
    expected_model: str = ""
    expected_brand: str = ""
    product_table: str | None = None
    facts_json: tuple[str, ...] = ()
    evidence_packets: tuple[str, ...] = ()
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
    evidence_packet_files: list[str] = field(default_factory=list)


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


def build_resolution_inputs(
    catalog: QuestionCatalog,
    spec: ResolutionInputSpec,
) -> ResolutionInputResult:
    """Load every explicit evidence source through one shared safety boundary.

    Both the offline report CLI and the live Makro planner use this same function.
    External image/web/AI packets are validated only after the expected product
    identity has been derived from explicit trusted inputs whenever possible.
    """

    trusted_bundles: list[ProductSourceBundle] = [
        bundle_from_catalog_answers(
            catalog,
            sku=spec.sku,
            image_paths=spec.image_paths,
            product_url=spec.product_url,
            supplemental_text=spec.supplemental_text,
        )
    ]
    warnings: list[str] = []

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
    bundles = list(trusted_bundles)

    packet_files: list[str] = []
    for path in spec.evidence_packets:
        packet_path = Path(path)
        payload = json.loads(packet_path.read_text(encoding="utf-8"))
        packet = EvidencePacket.from_mapping(payload)
        validated = validate_evidence_packet(
            packet,
            catalog,
            expected_identity=expected,
        )
        warnings.extend(
            f"{packet_path.name}: {warning}" for warning in validated.warnings
        )
        bundles.append(
            bundle_from_evidence_packet(
                validated.packet,
                expected_identity=expected,
            )
        )
        packet_files.append(str(packet_path.resolve()))

    text_parts = [spec.supplemental_text]
    if spec.supplemental_text_file:
        text_parts.append(
            Path(spec.supplemental_text_file).read_text(encoding="utf-8")
        )
    explicit_text = "\n".join(part for part in text_parts if part.strip())
    if explicit_text:
        bundles.append(
            bundle_from_key_value_text(
                explicit_text,
                source_reference=spec.supplemental_text_file or "--supplemental-text",
            )
        )

    return ResolutionInputResult(
        bundle=merge_bundles(*bundles),
        expected_identity=expected,
        warnings=warnings,
        evidence_packet_files=packet_files,
    )
