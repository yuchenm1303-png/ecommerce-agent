"""One-shot, browser-free product resolution with a pluggable multimodal AI provider.

Production semantic extraction is source-first: each logical evidence source is
shown to the model once against the complete pending question set. Text chunks
remain separate citation units but never multiply API calls. Validated source
results are content-addressed and cached, so an interrupted retry does not
re-run completed image recognition.

The provider is replaceable; the trust boundary is not. Every candidate fact
still passes grounded evidence, identity, business-field, confidence and
conflict checks before the Answer Resolver can mark a field eligible.

This command never opens Makro and never writes listing fields.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.evidence_contract import bundle_from_evidence_packet
from app.evidence_io import write_evidence_packet
from app.evidence_pipeline import merge_bundles
from app.evidence_validation import is_business_question, validate_evidence_packet
from app.live_schema import augment_catalog_with_live_fields, load_live_schema
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
from app.semantic_grounding import build_grounding_catalog
from app.semantic_sources import (
    build_semantic_pending_catalog,
    run_grounded_semantic_sources,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "从客户 QA + Makro live schema + 图片 + 已捕获网页 + 结构化资料生成可审计答案报告；"
            "每个逻辑证据源最多一次正常 AI 调用，支持严格验证后的内容缓存；"
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
    parser.add_argument(
        "--live-schema",
        default=None,
        help="read-only planner 导出的 live-schema.json；用于追加客户 QA 中没有的非经营 Makro 字段。",
    )
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
    parser.add_argument(
        "--image-detail",
        choices=("auto", "low", "high"),
        default="auto",
        help="默认 auto：兼容接口不发送 vendor-specific detail 字段；确认服务商支持时可选 low/high。",
    )
    parser.add_argument("--max-output-tokens", type=int, default=12000)
    parser.add_argument("--max-text-chars", type=int, default=3000)
    parser.add_argument("--overlap-chars", type=int, default=250)
    parser.add_argument("--auto-fill-min-confidence", type=float, default=0.85)
    parser.add_argument("--ai-auto-fill-min-confidence", type=float, default=0.92)
    parser.add_argument(
        "--max-source-repair-attempts",
        type=int,
        choices=(0, 1),
        default=1,
        help=(
            "仅当一个 source 返回的候选 fact 全部被严格验证拒绝时允许的额外修复请求次数；"
            "默认最多 1 次。只要已有部分 fact 合法，就直接保留合法项，不重识整个 source。"
        ),
    )
    parser.add_argument(
        "--fail-on-source-error",
        action="store_true",
        help="任一逻辑证据源失败就终止；默认保留其他已验证 source，并把失败 source 记入报告。",
    )
    parser.add_argument(
        "--semantic-cache-dir",
        default="logs/semantic-cache",
        help="严格验证后的 per-source 内容缓存目录；相同 model/schema/source 重跑无需再次调用 AI。",
    )
    parser.add_argument(
        "--no-semantic-cache",
        action="store_true",
        help="禁用 per-source 内容缓存；主要用于诊断。",
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


def _print_source_progress(event: dict[str, Any]) -> None:
    kind = str(event.get("kind") or "")
    source_id = str(event.get("source_id") or "")
    index = int(event.get("index") or 0)
    total = int(event.get("total") or 0)
    prefix = f"[semantic {index}/{total}] {source_id}"
    name = event.get("event")
    if name == "source_start":
        chunks = int(event.get("chunk_count") or 0)
        print(f"{prefix} START kind={kind or '?'} chunks={chunks}", flush=True)
    elif name == "source_cache_hit":
        print(f"{prefix} CACHE HIT", flush=True)
    elif name == "source_complete":
        elapsed = float(event.get("elapsed_seconds") or 0.0)
        print(
            f"{prefix} DONE facts={int(event.get('facts') or 0)} "
            f"rejected={int(event.get('rejected_facts') or 0)} "
            f"model_calls={int(event.get('model_calls') or 0)} "
            f"elapsed={elapsed:.1f}s",
            flush=True,
        )
    elif name == "source_failed":
        elapsed = float(event.get("elapsed_seconds") or 0.0)
        print(f"{prefix} FAILED elapsed={elapsed:.1f}s: {event.get('error')}", flush=True)


def main() -> int:
    args = build_parser().parse_args()
    _validate_threshold("auto-fill-min-confidence", args.auto_fill_min_confidence)
    _validate_threshold("ai-auto-fill-min-confidence", args.ai_auto_fill_min_confidence)
    if args.max_text_chars < 500:
        raise SystemExit("--max-text-chars 不能小于 500")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_text_chars:
        raise SystemExit("--overlap-chars 必须 >=0 且小于 --max-text-chars")

    base_catalog = load_question_catalog(args.qa)
    catalog = base_catalog
    live_schema_warnings: list[str] = []
    if args.live_schema:
        live_fields = load_live_schema(args.live_schema)
        catalog, live_schema_warnings = augment_catalog_with_live_fields(
            base_catalog,
            live_fields,
            business_locked=is_business_question,
        )

    supplemental_text = _read_supplemental_text(args)
    base_inputs = build_resolution_inputs(
        catalog,
        _base_input_spec(args, supplemental_text),
    )

    grounded_text = base_inputs.bundle.supplemental_text
    grounding = build_grounding_catalog(
        image_paths=args.image,
        supplier_snapshots=args.supplier_snapshot,
        official_snapshots=args.official_snapshot,
        supplemental_text=grounded_text,
        max_text_chars=args.max_text_chars,
        overlap_chars=args.overlap_chars,
    )
    if not grounding.sources:
        raise SystemExit(
            "没有可供语义抽取的 grounded source。请至少提供 --image、"
            "--supplier-snapshot、--official-snapshot、QA 商品上下文或 supplemental text。"
        )

    provider_config = _provider_config(args)
    try:
        provider = build_semantic_provider(provider_config)
    except ProviderConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / f"resolve-ai-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    source_manifest_path = output_dir / "source-manifest.json"
    source_manifest_path.write_text(
        json.dumps(grounding.as_manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    pending = build_semantic_pending_catalog(catalog)
    cache_dir = None if args.no_semantic_cache else Path(args.semantic_cache_dir)
    cache_namespace = json.dumps(
        provider_config.as_safe_dict(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )

    print("===== GROUNDED AI SOURCE-FIRST RESOLUTION =====", flush=True)
    print(
        f"provider={provider_config.provider}, model={provider_config.model}, "
        f"pending_questions={len(pending.questions)}, logical_sources={grounding.logical_source_count}, "
        f"citation_chunks={len(grounding.sources)}",
        flush=True,
    )
    print(f"output_dir={output_dir.resolve()}", flush=True)
    print(
        "cache=disabled" if cache_dir is None else f"cache={cache_dir.resolve()}",
        flush=True,
    )

    semantic = run_grounded_semantic_sources(
        provider,
        catalog,
        grounding,
        expected_identity=base_inputs.expected_identity,
        continue_on_source_error=not args.fail_on_source_error,
        max_repair_attempts=args.max_source_repair_attempts,
        cache_dir=cache_dir,
        cache_namespace=cache_namespace,
        progress=_print_source_progress,
    )

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

    packet_path = write_evidence_packet(
        semantic_packet,
        output_dir / "validated-semantic-evidence.json",
    )
    source_report_path = output_dir / "semantic-sources.json"
    source_report_path.write_text(
        json.dumps(
            {
                "execution_model": "one_call_per_logical_source_normal_path",
                "provider_adapter": provider.name,
                "provider_config": provider_config.as_safe_dict(),
                "pending_question_count": len(pending.questions),
                "citation_source_count": len(grounding.sources),
                "logical_source_count": semantic.total_sources,
                "completed_sources": semantic.completed_sources,
                "failed_sources": semantic.failed_sources,
                "model_calls": semantic.model_calls,
                "cache_hits": semantic.cache_hits,
                "elapsed_seconds": round(semantic.elapsed_seconds, 3),
                "partial": semantic.partial,
                "failures": [
                    {
                        "source_id": item.source_id,
                        "source_references": list(item.source_references),
                        "error": item.error,
                    }
                    for item in semantic.failures
                ],
                "source_stats": [item.as_dict() for item in semantic.source_stats],
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
                "mode": "browser_free_grounded_source_first_ai_resolution",
                "qa_source": str(Path(args.qa).resolve()),
                "live_schema": str(Path(args.live_schema).resolve()) if args.live_schema else None,
                "base_question_count": len(base_catalog.questions),
                "effective_question_count": len(catalog.questions),
                "semantic_pending_question_count": len(pending.questions),
                "live_extra_question_count": len(catalog.questions) - len(base_catalog.questions),
                "live_schema_warning_count": len(live_schema_warnings),
                "live_schema_warnings": live_schema_warnings,
                "identity_guard": {
                    "sku": identity.sku,
                    "model_number": identity.model_number,
                    "brand": identity.brand,
                },
                "provider_adapter": provider.name,
                "provider_config": provider_config.as_safe_dict(),
                "grounded_citation_source_count": len(grounding.sources),
                "grounded_logical_source_count": grounding.logical_source_count,
                "customer_context_chars": len(grounded_text),
                "semantic_fact_count": len(semantic_packet.facts),
                "semantic_partial": semantic.partial,
                "semantic_failed_sources": semantic.failed_sources,
                "semantic_model_calls": semantic.model_calls,
                "semantic_cache_hits": semantic.cache_hits,
                "semantic_elapsed_seconds": round(semantic.elapsed_seconds, 3),
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

    print("\n===== GROUNDED AI RESOLUTION COMPLETE =====")
    print(
        f"questions={summary['total']} (base={len(base_catalog.questions)}, "
        f"live_extra={len(catalog.questions) - len(base_catalog.questions)}), "
        f"resolved={summary['resolved']}, needs_review={summary['needs_review']}, "
        f"conflict={summary['conflict']}, missing={summary['missing']}"
    )
    print(
        f"eligible_for_autofill={summary['eligible_for_autofill']}, "
        f"blocked={summary['blocked']}"
    )
    print(
        f"semantic_facts={len(semantic_packet.facts)}, sources={semantic.completed_sources}/{semantic.total_sources}, "
        f"failed_sources={semantic.failed_sources}, model_calls={semantic.model_calls}, "
        f"cache_hits={semantic.cache_hits}, elapsed={semantic.elapsed_seconds:.1f}s"
    )
    print(
        f"review_queue={review_summary['total']} "
        f"(conflict={review_summary['conflict']}, "
        f"needs_review={review_summary['needs_review']}, missing={review_summary['missing']})"
    )
    print(f"Semantic evidence: {packet_path.resolve()}")
    print(f"Source manifest: {source_manifest_path.resolve()}")
    print(f"Source report: {source_report_path.resolve()}")
    print(f"Resolution JSON: {resolution_json.resolve()}")
    print(f"Resolution XLSX: {resolution_xlsx.resolve()}")
    print(f"Review JSON: {review_json.resolve()}")
    print(f"Review XLSX: {review_xlsx.resolve()}")
    print(f"Run manifest: {run_manifest.resolve()}")
    print("没有打开 Makro；没有填写字段；没有 Save；没有 Send to QC。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
