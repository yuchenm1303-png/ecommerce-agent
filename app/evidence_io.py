from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evidence_contract import EvidencePacket


def evidence_packet_to_dict(packet: EvidencePacket) -> dict[str, Any]:
    return {
        "extractor": packet.extractor,
        "product_identity": {
            "sku": packet.identity.sku,
            "model_number": packet.identity.model_number,
            "brand": packet.identity.brand,
        },
        "facts": [
            {
                "key": fact.key,
                "aliases": list(fact.aliases),
                "value": list(fact.value) if isinstance(fact.value, tuple) else fact.value,
                "source_type": fact.source_type,
                "source_reference": fact.source_reference,
                "confidence": fact.confidence,
                "evidence_text": fact.evidence_text,
                "note": fact.note,
            }
            for fact in packet.facts
        ],
        "warnings": list(packet.warnings),
    }


def write_evidence_packet(packet: EvidencePacket, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(evidence_packet_to_dict(packet), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target
