from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .evidence_contract import EvidencePacket, ProductIdentity, bundle_from_evidence_packet
from .evidence_pipeline import (
    bundle_from_catalog_answers,
    bundle_from_facts_json,
    bundle_from_key_value_text,
    merge_bundles,
)
from .evidence_validation import validate_evidence_packet
from .qa_catalog import QuestionCatalog
from .source_bundle import ProductSourceBundle, bundle_from_product_table


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


def build_resolution_inputs(
    catalog: QuestionCatalog,
    spec: ResolutionInputSpec,
) -> ResolutionInputResult:
    """Load every explicit evidence source through one shared safety boundary.

    Both the offline report CLI and the future live Makro planner use this same
    function. That prevents the browser path from accidentally accepting looser
    evidence than the offline audit path.
    """

    bundles: list[ProductSourceBundle] = [
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
        bundles.append(
            bundle_from_product_table(
                spec.product_table,
                sku=spec.sku or None,
            )
        )

    for path in spec.facts_json:
        bundles.append(bundle_from_facts_json(path, sku=spec.sku))

    expected = spec.expected_identity
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
