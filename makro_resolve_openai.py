"""One-shot, browser-free product resolution using grounded OpenAI extraction.

Pipeline:
1. load the complete customer QA catalog;
2. load trusted seller/product inputs and derive the expected product identity;
3. build content-bound source ids for product images and captured supplier/official pages;
4. ask OpenAI for small batches of candidate facts;
5. reject any candidate that fails exact QA/source/evidence/business/identity grounding;
6. merge only validated semantic evidence with deterministic evidence;
7. write resolution and review-queue reports.

This command never opens Makro and never writes listing fields.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.evidence_contract import bundle_from_evidence_packet
from app.evidence_io import write_evidence_packet
from app.evidence_pipeline import merge_bundles
from app.evidence_validation import validate_evidence_packet
from app.providers.openai_semantic import OpenAISemanticProvider
from app.qa_catalog import load_question_catalog
from app.resolution_engine import ResolutionPolicy, resolve_catalog, summarize_resolution
from app.resolution_report import write_resolution_json, write_resolution_xlsx
from app.resolver_inputs import ResolutionInputSpec, build_resolution_inputs
from app.review_queue import (
    build_review_queue,
    summarize_review_queue,
    write_review_queue_json,
    write_review_queue_xlsx,
)
from app.semantic_batching import run_grounded_semantic_batches
from app.semantic_grounding import build_grounding_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "从客户 QA + 图片 + 已捕获网页 + 结构化资料生成可审计答案报告；"
            "调用 OpenAI，但绝不打开或修改 Makro。"
        )
    )
    parser.add_argument("--qa", required=True, help="客户 Question/Answer .xlsx/.xlsm/.csv")
    parser.add_argument("--sku", default="")
    parser.add_argument("--expected-model", default="")
    parser.add_argument("--expected-brand", default="")
    parser.add_argument("--product-table", default=None, help="可信结构化商品/经营数据表")
    parser.add_argument("--facts-json", action="append", default=[], help="可信人工/确定性 facts JSON")
    parser.add_argument("--image", action="append", default=[], help="商品图片，可重复")
    parser.add_argument("--supplier-snapshot", action="append", default=[], help="供应商网页 snapshot JSON，可重复")
    parser.add_argument("--official-snapshot", action="append", default=[], help="官方网页 snapshot JSON，可重复")
    parser.add_argument("--supplemental-text", default="")
    parser.add_argument("--supplemental-text-file", default=None)
    parser.add_argument("--openai-model", default="gpt-5.6")
    parser.add_argument("--image-detail", choices=("auto", "low", "high"), default="high")
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--max-output-tokens", type=int, default=12000)
    parser.add_argument("--max-text-chars", type=int, default=3000)
    parser.add_argument("--overlap-chars", type=int, default=250)
    parser.add_argument("--auto-fill-min-confidence", type=float, default=0.85)
    parser.add_argument("--ai-auto-fill-min-confidence", type=float, default=0.92)
    parser.add_argument(
        "--fail-on-batch-error",
        action="store_true",
        help="任一语义批次验证失败就终止；默认继续其他批次并把失败问题留在 review queue。",
    )
    parser.add_argument("--output-dir", default="logs/openai-resolver")
    return parser


def _read_supplemental_text(args: argparse.Namespace) -> str:
    parts = [args.supplemental_text]
    if args.supplemental_text_file:
        parts.append(Path(args.supplemental_text_file).read_text(encoding="utf-8"))
    return "\n".join(part for part in parts if part.strip())


def _base_input_spec(args: argparse.Namespace, supplemental_text: str) -> ResolutionInputSpec:
    return ResolutionInputSpec(
        sku=args.sku,
        expected_model=args.expected_model,
        expected_brand=args.expected_brand,
        product_table=args.product_table,
        facts_json=tuple(args.facts_json),
        supplier_snapshots=tuple(args.supplier_snapshot),
        official_snapshots=tuple(args.official_snapshot),
        supplemental_text=supplemental_text,
        image_paths=tuple(args.image),
    )


def _validate_threshold(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise SystemExit(f"--{name} 必须在 0..1")


def main() -> int:
    args = build_parser().parse_args()
    _validate_threshold("auto-fill-min-confidence", args.auto_fill_min_confidence)
    _validate_threshold("ai-auto-fill-min-confidence", args.ai_auto_fill_min_confidence)

    catalog = load_question_catalog(args.qa)
    supplemental_text = _read_supplemental_text(args)

    # Deterministic/trusted sources are loaded first. build_resolution_inputs
    # derives identity only from trusted source classes before it validates any
    # supplier/official snapshot evidence.
    base_inputs = build_resolution_inputs(
        catalog,
        _base_input_spec(args, supplemental_text),
    )

    grounding = build_grounding_catalog(
        image_paths=args.image,
        supplier_snapshots=args.supplier_snapshot,
        official_snapshots=args.official_snapshot,
        supplemental_text=supplemental_text,
        max_text_chars=args.max_text_chars,
        overlap_chars=args.overlap_chars,
    )
    if not grounding.sources:
        raise SystemExit(
            "没有可供语义抽取的 grounded source。请至少提供 --image、"
            "--supplier-snapshot、--official-snapshot 或 supplemental text。"
        )

    provider = OpenAISemanticProvider(
        model=args.openai_model,
        image_detail=args.image_detail,
        max_output_tokens=args.max_output_tokens,
    )
    semantic = run_grounded_semantic_batches(
        provider,
        catalog,
        grounding,
        expected_identity=base_inputs.expected_identity,
        batch_size=args.batch_size,
        continue_on_batch_error=not args.fail_on_batch_error,
    )

    # Defense in depth: each batch already passed grounded validation. Validate
    # the merged packet once more against the complete QA before bundle creation.
    semantic_packet = validate_evidence_packet(
        semantic.packet,
        catalog,
        expected_identity=base_inputs.expected_identity,
    ).packet
    semantic_bundle = bundle_from_evidence_packet(
        semantic_packet,
        expected_identity=base_inputs.expected_identity,
    )
    combined_bundle = merge_bundles(base_inputs.bundle, semantic_bundle)

    policy = ResolutionPolicy(
        auto_fill_min_confidence=args.auto_fill_min_confidence,
        ai_auto_fill_min_confidence=args.ai_auto_fill_min_confidence,
    )
    records = resolve_catalog(catalog, combined_bundle, policy=policy)
    summary = summarize_resolution(records)
    review_items = build_review_queue(records)
    review_summary = summarize_review_queue(review_items)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / f"resolve-openai-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    packet_path = write_evidence_packet(
        semantic_packet,
        output_dir / "validated-semantic-evidence.json",
    )
    source_manifest_path = output_dir / "source-manifest.json"
    source_manifest_path.write_text(
        json.dumps(grounding.as_manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    batch_path = output_dir / "semantic-batches.json"
    batch_path.write_text(
        json.dumps(
            {
                "provider": provider.name,
                "model": args.openai_model,
                "batch_size": args.batch_size,
                "completed_batches": semantic.completed_batches,
                "failed_batches": semantic.failed_batches,
                "partial": semantic.partial,
                "failures": [
                    {
                        "batch_id": item.batch_id,
                        "question_numbers": list(item.question_numbers),
                        "error": item.error,
                    }
                    for item in semantic.failures
                ],
                "warnings": semantic.warnings,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    resolution_json = write_resolution_json(records, output_dir / "resolution.json")
    resolution_xlsx = write_resolution_xlsx(records, output_dir / "resolution.xlsx")
    review_json = write_review_queue_json(review_items, output_dir / "review-queue.json")
    review_xlsx = write_review_queue_xlsx(review_items, output_dir / "review-queue.xlsx")

    run_manifest = output_dir / "run-manifest.json"
    identity = base_inputs.expected_identity
    run_manifest.write_text(
        json.dumps(
            {
                "mode": "browser_free_grounded_openai_resolution",
                "qa_source": str(Path(args.qa).resolve()),
                "identity_guard": {
                    "sku": identity.sku,
                    "model_number": identity.model_number,
                    "brand": identity.brand,
                },
                "provider": provider.name,
                "model": args.openai_model,
                "image_detail": args.image_detail,
                "grounded_source_count": len(grounding.sources),
                "semantic_fact_count": len(semantic_packet.facts),
                "semantic_partial": semantic.partial,
                "semantic_failed_batches": semantic.failed_batches,
                "deterministic_warnings": base_inputs.warnings,
                "resolution_summary": summary,
                "review_summary": review_summary,
                "makro_browser_opened": False,
                "writes_performed": 0,
                "save_clicked": False,
                "send_to_qc_clicked": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("===== GROUNDED OPENAI RESOLUTION COMPLETE =====")
    print(
        f"questions={summary['total']}, resolved={summary['resolved']}, "
        f"needs_review={summary['needs_review']}, conflict={summary['conflict']}, "
        f"missing={summary['missing']}"
    )
    print(
        f"eligible_for_autofill={summary['eligible_for_autofill']}, "
        f"blocked={summary['blocked']}"
    )
    print(
        f"semantic_facts={len(semantic_packet.facts)}, "
        f"completed_batches={semantic.completed_batches}, failed_batches={semantic.failed_batches}"
    )
    print(
        f"review_queue={review_summary['total']} "
        f"(conflict={review_summary['conflict']}, "
        f"needs_review={review_summary['needs_review']}, missing={review_summary['missing']})"
    )
    print(f"Semantic evidence: {packet_path.resolve()}")
    print(f"Source manifest: {source_manifest_path.resolve()}")
    print(f"Batch report: {batch_path.resolve()}")
    print(f"Resolution JSON: {resolution_json.resolve()}")
    print(f"Resolution XLSX: {resolution_xlsx.resolve()}")
    print(f"Review JSON: {review_json.resolve()}")
    print(f"Review XLSX: {review_xlsx.resolve()}")
    print(f"Run manifest: {run_manifest.resolve()}")
    print("没有打开 Makro；没有填写字段；没有 Save；没有 Send to QC。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
