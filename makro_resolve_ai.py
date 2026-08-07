"""One-shot, browser-free product resolution with a pluggable multimodal AI provider.

The provider is replaceable; the trust boundary is not. Every provider returns
untrusted candidate JSON which must pass the same grounded evidence, identity,
business-field, confidence and conflict checks before the Answer Resolver can
mark a field eligible for autofill.

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
from app.providers.registry import (
    ProviderConfig,
    ProviderConfigurationError,
    SUPPORTED_PROVIDERS,
    build_semantic_provider,
    default_api_key_env,
)
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
            "AI provider 可替换，但统一经过 EvidencePacket/Resolver 安全门；"
            "绝不打开或修改 Makro。"
        )
    )
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        default="openai-compatible",
        help="AI provider；默认 openai-compatible，原生 OpenAI 可选 openai。",
    )
    parser.add_argument("--model", required=True, help="当前服务商的多模态/视觉模型名")
    parser.add_argument(
        "--api-key-env",
        default=None,
        help="保存 API key 的环境变量名；默认 openai=>OPENAI_API_KEY，openai-compatible=>AI_API_KEY。",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="OpenAI-compatible API 根地址；provider=openai-compatible 时必填。",
    )
    parser.add_argument(
        "--structured-mode",
        choices=("prompt_only", "json_object"),
        default="prompt_only",
        help=(
            "兼容接口的 JSON 输出模式。prompt_only 兼容面最广；"
            "服务商明确支持 response_format=json_object 时可选 json_object。"
        ),
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
    parser.add_argument("--output-dir", default="logs/ai-resolver")
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


def _provider_config(args: argparse.Namespace) -> ProviderConfig:
    api_key_env = args.api_key_env or default_api_key_env(args.provider)
    return ProviderConfig(
        provider=args.provider,
        model=args.model,
        api_key_env=api_key_env,
        base_url=args.base_url,
        image_detail=args.image_detail,
        max_output_tokens=args.max_output_tokens,
        structured_mode=args.structured_mode,
    )


def main() -> int:
    args = build_parser().parse_args()
    _validate_threshold("auto-fill-min-confidence", args.auto_fill_min_confidence)
    _validate_threshold("ai-auto-fill-min-confidence", args.ai_auto_fill_min_confidence)
    if args.batch_size < 1:
        raise SystemExit("--batch-size 不能小于 1")
    if args.max_text_chars < 500:
        raise SystemExit("--max-text-chars 不能小于 500")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_text_chars:
        raise SystemExit("--overlap-chars 必须 >=0 且小于 --max-text-chars")

    catalog = load_question_catalog(args.qa)
    supplemental_text = _read_supplemental_text(args)

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

    provider_config = _provider_config(args)
    try:
        provider = build_semantic_provider(provider_config)
    except ProviderConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    semantic = run_grounded_semantic_batches(
        provider,
        catalog,
        grounding,
        expected_identity=base_inputs.expected_identity,
        batch_size=args.batch_size,
        continue_on_batch_error=not args.fail_on_batch_error,
    )

    # Defense in depth: every batch already passed grounded validation. Validate
    # the merged packet again before it becomes resolver evidence.
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
    output_dir = Path(args.output_dir) / f"resolve-ai-{stamp}"
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
                "provider_adapter": provider.name,
                "provider_config": provider_config.as_safe_dict(),
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
                "mode": "browser_free_grounded_pluggable_ai_resolution",
                "qa_source": str(Path(args.qa).resolve()),
                "identity_guard": {
                    "sku": identity.sku,
                    "model_number": identity.model_number,
                    "brand": identity.brand,
                },
                "provider_adapter": provider.name,
                "provider_config": provider_config.as_safe_dict(),
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

    print("===== GROUNDED AI RESOLUTION COMPLETE =====")
    print(
        f"provider={provider_config.provider}, model={provider_config.model}, "
        f"api_key_env={provider_config.api_key_env}"
    )
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
