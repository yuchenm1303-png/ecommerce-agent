"""Validate a semantic EvidencePacket against the exact current source universe.

The packet is untrusted until every grounded citation is reproduced from the
current QA customer context, images and captured snapshots. This command never
opens or modifies Makro.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.evidence_contract import ProductIdentity
from app.qa_catalog import load_question_catalog
from app.resolver_inputs import ResolutionInputSpec, customer_context_for_resolution
from app.semantic_extraction import validate_grounded_semantic_packet
from app.semantic_grounding import build_grounding_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="校验模型输出是否真正引用本次 QA/图片/网页证据；不修改 Makro。"
    )
    parser.add_argument("--qa", required=True)
    parser.add_argument("--packet", required=True, help="模型返回的 EvidencePacket JSON")
    parser.add_argument("--sku", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--brand", default="")
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--supplier-snapshot", action="append", default=[])
    parser.add_argument("--official-snapshot", action="append", default=[])
    parser.add_argument("--supplemental-text", default="")
    parser.add_argument("--supplemental-text-file", default=None)
    parser.add_argument("--max-text-chars", type=int, default=3000)
    parser.add_argument("--overlap-chars", type=int, default=250)
    parser.add_argument("--output-dir", default="logs/semantic-extraction")
    return parser


def _packet_dict(packet) -> dict[str, object]:
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
        "warnings": list(packet.warnings),
    }


def main() -> int:
    args = build_parser().parse_args()
    catalog = load_question_catalog(args.qa)
    spec = ResolutionInputSpec(
        sku=args.sku,
        expected_model=args.model,
        expected_brand=args.brand,
        supplier_snapshots=tuple(args.supplier_snapshot),
        official_snapshots=tuple(args.official_snapshot),
        supplemental_text=args.supplemental_text,
        supplemental_text_file=args.supplemental_text_file,
        image_paths=tuple(args.image),
        grounding_max_text_chars=args.max_text_chars,
        grounding_overlap_chars=args.overlap_chars,
    )
    customer_context = customer_context_for_resolution(catalog, spec)
    grounding = build_grounding_catalog(
        image_paths=spec.image_paths,
        supplier_snapshots=spec.supplier_snapshots,
        official_snapshots=spec.official_snapshots,
        supplemental_text=customer_context,
        max_text_chars=spec.grounding_max_text_chars,
        overlap_chars=spec.grounding_overlap_chars,
    )
    raw = json.loads(Path(args.packet).read_text(encoding="utf-8"))
    packet = validate_grounded_semantic_packet(
        raw,
        catalog,
        grounding,
        expected_identity=ProductIdentity(
            sku=args.sku,
            model_number=args.model,
            brand=args.brand,
        ),
    )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / f"validated-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    validated_path = output_dir / "validated-evidence-packet.json"
    manifest_path = output_dir / "source-manifest.json"
    validated_path.write_text(
        json.dumps(_packet_dict(packet), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(grounding.as_manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("===== SEMANTIC EVIDENCE VALIDATED =====")
    print(f"facts={len(packet.facts)}")
    print(f"grounded_sources={len(grounding.sources)}")
    print(f"customer_context_chars={len(customer_context)}")
    print(f"validated_packet={validated_path.resolve()}")
    print(f"manifest={manifest_path.resolve()}")
    print("校验通过仅代表证据边界通过；后续仍要经过 Resolver 冲突/置信度/字段约束。")
    print("未打开 Makro；未填写、Save 或 Send to QC。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
