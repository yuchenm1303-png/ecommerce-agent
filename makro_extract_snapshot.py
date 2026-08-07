"""Convert a deterministic source snapshot into a strict EvidencePacket.

No model is called here. Only explicit table/JSON-LD key-value pairs whose keys
exactly match current QA questions are emitted. Free prose is intentionally left
for the later model extractor.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.evidence_validation import validate_evidence_packet
from app.qa_catalog import load_question_catalog
from app.snapshot_evidence import extract_snapshot_evidence
from app.source_snapshot import source_snapshot_from_json
from app.evidence_contract import ProductIdentity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从供应商/官网 snapshot 中提取 exact QA key/value 证据，不做 AI 猜测。"
    )
    parser.add_argument("--qa", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument(
        "--source-type",
        choices=("supplier_web", "official_web"),
        default="supplier_web",
    )
    parser.add_argument("--confidence", type=float, default=0.88)
    parser.add_argument("--expected-sku", default="")
    parser.add_argument("--expected-model", default="")
    parser.add_argument("--expected-brand", default="")
    parser.add_argument("--output-dir", default="logs/source-evidence")
    return parser


def _packet_dict(packet) -> dict:
    return {
        "schema_version": 1,
        "extractor": packet.extractor,
        "product_identity": {
            "sku": packet.identity.sku,
            "model_number": packet.identity.model_number,
            "brand": packet.identity.brand,
        },
        "facts": [
            {
                "key": fact.key,
                "value": list(fact.value) if isinstance(fact.value, tuple) else fact.value,
                "source_type": fact.source_type,
                "source_reference": fact.source_reference,
                "confidence": fact.confidence,
                "evidence_text": fact.evidence_text,
                "aliases": list(fact.aliases),
                "note": fact.note,
            }
            for fact in packet.facts
        ],
        "warnings": packet.warnings,
    }


def main() -> int:
    args = build_parser().parse_args()
    if not 0.0 <= args.confidence <= 1.0:
        raise SystemExit("--confidence 必须在 0..1。")

    catalog = load_question_catalog(args.qa)
    snapshot = source_snapshot_from_json(args.snapshot)
    extracted = extract_snapshot_evidence(
        snapshot,
        catalog,
        source_type=args.source_type,
        confidence=args.confidence,
    )
    expected = ProductIdentity(
        sku=args.expected_sku,
        model_number=args.expected_model,
        brand=args.expected_brand,
    )
    validated = validate_evidence_packet(
        extracted.packet,
        catalog,
        expected_identity=expected,
    )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / f"snapshot-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "evidence-packet.json"
    target.write_text(
        json.dumps(_packet_dict(validated.packet), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("===== SOURCE SNAPSHOT EXTRACTION =====")
    print(f"snapshot={Path(args.snapshot).resolve()}")
    print(f"matched_facts={validated.normalized_fact_count}")
    print(f"ignored_source_rows={extracted.ignored_rows}")
    print(f"warnings={len(validated.warnings)}")
    print(f"evidence_packet={target.resolve()}")
    print("只抽取明确 key/value；没有解析自由文本，没有调用 AI。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
