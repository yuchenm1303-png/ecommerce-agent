from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .evidence_contract import (
    EvidencePacket,
    ProductIdentity,
    assert_identity_compatible,
)
from .qa_catalog import QuestionCatalog


@dataclass(slots=True, frozen=True)
class ExtractionRequest:
    catalog: QuestionCatalog
    expected_identity: ProductIdentity = ProductIdentity()
    image_paths: tuple[str, ...] = ()
    product_url: str | None = None
    supplemental_text: str = ""

    @property
    def questions(self) -> tuple[str, ...]:
        return tuple(item.question for item in self.catalog.questions)


class EvidenceExtractor(Protocol):
    name: str

    def extract(self, request: ExtractionRequest) -> EvidencePacket:
        """Return only source-grounded facts conforming to EvidencePacket."""
        ...


class JsonPacketExtractor:
    """Adapter for a previously produced strict evidence packet JSON file."""

    name = "json-packet"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def extract(self, request: ExtractionRequest) -> EvidencePacket:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        packet = EvidencePacket.from_mapping(payload)
        assert_identity_compatible(request.expected_identity, packet.identity)
        return packet


@dataclass(slots=True)
class CompositeExtractionResult:
    packets: list[EvidencePacket] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def fact_count(self) -> int:
        return sum(len(packet.facts) for packet in self.packets)


def run_extractors(
    request: ExtractionRequest,
    extractors: list[EvidenceExtractor],
) -> CompositeExtractionResult:
    """Run independent extractors and keep their provenance separate.

    Extractors are not allowed to reconcile disagreements here. Conflicts remain
    visible as separate evidence and are resolved/blocked later by the resolver.
    """

    output = CompositeExtractionResult()
    for extractor in extractors:
        packet = extractor.extract(request)
        assert_identity_compatible(request.expected_identity, packet.identity)
        output.packets.append(packet)
        output.warnings.extend(f"{extractor.name}: {warning}" for warning in packet.warnings)
    return output
