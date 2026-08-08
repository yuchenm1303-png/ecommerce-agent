from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .evidence_contract import EvidencePacket, ProductIdentity, bundle_from_evidence_packet
from .evidence_pipeline import (
    add_fact,
    bundle_from_catalog_answers,
    bundle_from_facts_json,
    bundle_from_key_value_text,
    merge_bundles,
)
from .evidence_validation import validate_evidence_packet
from .qa_catalog import QuestionCatalog
from .semantic_extraction import validate_grounded_semantic_packet
from .semantic_grounding import GroundingCatalog, build_grounding_catalog
from .snapshot_evidence import extract_snapshot_evidence
from .source_bundle import ProductSourceBundle, bundle_from_product_table, normalize_key
from .source_snapshot import source_snapshot_from_json


_TRUSTED_IDENTITY_SOURCE_TYPES = {
    "structured",
    "customer_answer",
    "business",
    "config",
    "rule",
}
_GROUNDED_SOURCE_PREFIXES = (
    "image:",
    "supplier:",
    "official:",
    "customer-text:",
)


@dataclass(slots=True, frozen=True)
class ResolutionInputSpec:
    sku: str = ""
    expected_model: str = ""
    expected_brand: str = ""
    product_table: str | None = None
    facts_json: tuple[str, ...] = ()
    evidence_packets: tuple[str, ...] = ()
    supplier_snapshots: tuple[str, ...] = ()
    official_snapshots: tuple[str, ...] = ()
    supplemental_text: str = ""
    supplemental_text_file: str | None = None
    image_paths: tuple[str, ...] = ()
    product_url: str | None = None
    grounding_max_text_chars: int = 3000
    grounding_overlap_chars: int = 250

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
    source_snapshot_files: list[str] = field(default_factory=list)
    grounded_packet_files: list[str] = field(default_factory=list)


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


def _append_validated_packet(
    *,
    packet: EvidencePacket,
    catalog: QuestionCatalog,
    expected: ProductIdentity,
    bundles: list[ProductSourceBundle],
    warnings: list[str],
    warning_prefix: str,
) -> None:
    validated = validate_evidence_packet(
        packet,
        catalog,
        expected_identity=expected,
    )
    warnings.extend(
        f"{warning_prefix}: {warning}" for warning in validated.warnings
    )
    bundles.append(
        bundle_from_evidence_packet(
            validated.packet,
            expected_identity=expected,
        )
    )


def _append_snapshot_files(
    *,
    paths: tuple[str, ...],
    source_type: str,
    confidence: float,
    catalog: QuestionCatalog,
    expected: ProductIdentity,
    bundles: list[ProductSourceBundle],
    warnings: list[str],
    snapshot_files: list[str],
) -> None:
    for path in paths:
        snapshot_path = Path(path)
        snapshot = source_snapshot_from_json(snapshot_path)
        extracted = extract_snapshot_evidence(
            snapshot,
            catalog,
            source_type=source_type,
            confidence=confidence,
        )
        _append_validated_packet(
            packet=extracted.packet,
            catalog=catalog,
            expected=expected,
            bundles=bundles,
            warnings=warnings,
            warning_prefix=snapshot_path.name,
        )
        if extracted.ignored_rows:
            warnings.append(
                f"{snapshot_path.name}: ignored_source_rows={extracted.ignored_rows}"
            )
        snapshot_files.append(str(snapshot_path.resolve()))


def customer_context_for_resolution(
    catalog: QuestionCatalog,
    spec: ResolutionInputSpec,
) -> str:
    """Return the exact customer text source shared by resolver and grounding."""

    parts = [catalog.preamble_text, spec.supplemental_text]
    if spec.supplemental_text_file:
        parts.append(Path(spec.supplemental_text_file).read_text(encoding="utf-8"))
    return "\n".join(part for part in parts if part and part.strip()).strip()


def _packet_requires_grounding(packet: EvidencePacket) -> bool:
    return any(
        str(fact.source_reference or "").startswith(_GROUNDED_SOURCE_PREFIXES)
        for fact in packet.facts
    )


def _grounding_for_spec(
    spec: ResolutionInputSpec,
    customer_context: str,
) -> GroundingCatalog:
    return build_grounding_catalog(
        image_paths=spec.image_paths,
        supplier_snapshots=spec.supplier_snapshots,
        official_snapshots=spec.official_snapshots,
        supplemental_text=customer_context,
        max_text_chars=spec.grounding_max_text_chars,
        overlap_chars=spec.grounding_overlap_chars,
    )


def build_resolution_inputs(
    catalog: QuestionCatalog,
    spec: ResolutionInputSpec,
) -> ResolutionInputResult:
    """Load every explicit evidence source through one shared safety boundary.

    Customer workbook preamble is retained as source context. Explicit ``sku``
    is seller-controlled input, so it becomes structured business evidence for
    the live SKU field as well as an identity guard. Evidence packets containing
    grounded source ids are rebound to the exact current source universe before
    they are allowed into a browser Fill Plan.
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
            note="explicit seller-controlled SKU supplied to the resolver command",
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
    bundles = list(trusted_bundles)

    if customer_context:
        bundles.append(
            bundle_from_key_value_text(
                customer_context,
                source_reference=f"{Path(catalog.source_path).name}:customer-context",
                source_type="customer_file",
            )
        )
        warnings.append(f"customer_context_chars={len(customer_context)}")

    packet_files: list[str] = []
    grounded_packet_files: list[str] = []
    grounding: GroundingCatalog | None = None
    for path in spec.evidence_packets:
        packet_path = Path(path)
        payload = json.loads(packet_path.read_text(encoding="utf-8"))
        packet = EvidencePacket.from_mapping(payload)
        if _packet_requires_grounding(packet):
            if grounding is None:
                grounding = _grounding_for_spec(spec, customer_context)
            packet = validate_grounded_semantic_packet(
                packet,
                catalog,
                grounding,
                expected_identity=expected,
            )
            grounded_packet_files.append(str(packet_path.resolve()))
            warnings.append(
                f"{packet_path.name}: grounded packet rebound to current source universe"
            )
        _append_validated_packet(
            packet=packet,
            catalog=catalog,
            expected=expected,
            bundles=bundles,
            warnings=warnings,
            warning_prefix=packet_path.name,
        )
        packet_files.append(str(packet_path.resolve()))

    snapshot_files: list[str] = []
    _append_snapshot_files(
        paths=spec.supplier_snapshots,
        source_type="supplier_web",
        confidence=0.88,
        catalog=catalog,
        expected=expected,
        bundles=bundles,
        warnings=warnings,
        snapshot_files=snapshot_files,
    )
    _append_snapshot_files(
        paths=spec.official_snapshots,
        source_type="official_web",
        confidence=0.92,
        catalog=catalog,
        expected=expected,
        bundles=bundles,
        warnings=warnings,
        snapshot_files=snapshot_files,
    )

    return ResolutionInputResult(
        bundle=merge_bundles(*bundles),
        expected_identity=expected,
        warnings=warnings,
        evidence_packet_files=packet_files,
        source_snapshot_files=snapshot_files,
        grounded_packet_files=grounded_packet_files,
    )