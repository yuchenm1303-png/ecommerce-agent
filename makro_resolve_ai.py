"""Resolve the whole Makro product schema with AI in one browser-free pass.

The live Makro schema is the target contract. Customer QA, selected variant,
images and captured supplier/official pages are product evidence. The model is
the primary semantic resolver; Python only validates identity, citations,
business locks and the structural decision contract.

This command never opens Makro and never writes listing fields.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from app.ai_decisions import (
    BUSINESS_LOCKED,
    CONFLICT,
    MISSING,
    READY,
    REVIEW,
    field_id,
    run_ai_resolution,
    write_ai_decision_packet,
)
from app.live_schema import load_live_schema
from app.providers.registry import (
    ProviderConfig,
    ProviderConfigurationError,
    SUPPORTED_PROVIDERS,
    build_semantic_provider,
    default_api_key_env,
    validate_provider_config,
)
from app.qa_catalog import QuestionCatalog, load_question_catalog
from app.resolver_inputs import (
    ResolutionInputSpec,
    build_resolution_inputs,
    customer_context_for_resolution,
)
from app.semantic_grounding import build_grounding_catalog


_TRUSTED_CONTEXT_SOURCE_TYPES = {
    "structured",
    "customer_answer",
    "business",
    "config",
    "rule",
    "customer_file",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "以当前 Makro live schema 为目标，让多模态 AI 一次性综合客户资料、图片和网页快照，"
            "直接生成字段级 READY/REVIEW/CONFLICT/MISSING 决策；绝不打开或修改 Makro。"
        )
    )
    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS, default="openai-compatible")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--base-url", default="")
    parser.add_argument(
        "--structured-mode",
        choices=("prompt_only", "json_object"),
        default="prompt_only",
    )
    thinking = parser.add_mutually_exclusive_group()
    thinking.add_argument(
        "--enable-thinking",
        dest="enable_thinking",
        action="store_true",
        help="显式开启兼容模型 thinking。",
    )
    thinking.add_argument(
        "--disable-thinking",
        dest="enable_thinking",
        action="store_false",
        help="显式关闭兼容模型 thinking；Qwen Omni 默认关闭以降低 listing enrichment 延迟。",
    )
    parser.set_defaults(enable_thinking=None)

    parser.add_argument("--qa", required=True, help="客户 QA/商品上下文文件")
    parser.add_argument(
        "--live-schema",
        required=True,
        help="read-only Makro planner 导出的 live-schema.json；它是 AI 唯一目标字段 schema。",
    )
    parser.add_argument("--sku", default="")
    parser.add_argument("--expected-model", default="")
    parser.add_argument("--expected-brand", default="")
    parser.add_argument("--product-table", default=None)
    parser.add_argument("--facts-json", action="append", default=[])
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--supplier-snapshot", action="append", default=[])
    parser.add_argument("--official-snapshot", action="append", default=[])
    parser.add_argument("--supplemental-text", default="")
    parser.add_argument("--supplemental-text-file", default=None)
    parser.add_argument("--image-detail", choices=("auto", "low", "high"), default="auto")
    parser.add_argument("--max-output-tokens", type=int, default=12000)
    parser.add_argument("--request-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-text-chars", type=int, default=5000)
    parser.add_argument("--overlap-chars", type=int, default=250)
    parser.add_argument(
        "--max-repair-attempts",
        type=int,
        choices=(0, 1),
        default=1,
        help="仅用于 JSON/contract 结构失败；不会因字段缺失或低置信度反复重识图片。",
    )
    parser.add_argument(
        "--semantic-cache-dir",
        default="logs/semantic-cache",
        help="整商品 AI decision cache；相同 schema+sources+model 重跑为零模型调用。",
    )
    parser.add_argument("--no-semantic-cache", action="store_true")
    parser.add_argument("--output-dir", default="logs/ai-resolver")
    return parser


def _read_supplemental_text(args: argparse.Namespace) -> str:
    parts = [args.supplemental_text]
    if args.supplemental_text_file:
        parts.append(Path(args.supplemental_text_file).read_text(encoding="utf-8"))
    return "\n".join(part for part in parts if part and part.strip())


def _input_spec(args: argparse.Namespace, supplemental_text: str) -> ResolutionInputSpec:
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


def _provider_config(args: argparse.Namespace) -> ProviderConfig:
    api_key_env = args.api_key_env or default_api_key_env(args.provider)
    return validate_provider_config(
        ProviderConfig(
            provider=args.provider,
            model=args.model,
            api_key_env=api_key_env,
            base_url=args.base_url,
            image_detail=args.image_detail,
            max_output_tokens=args.max_output_tokens,
            structured_mode=args.structured_mode,
            request_timeout_seconds=args.request_timeout_seconds,
            enable_thinking=args.enable_thinking,
        )
    )


def _cache_namespace(config: ProviderConfig) -> str:
    safe = config.as_safe_dict()
    safe.pop("request_timeout_seconds", None)
    safe.pop("sdk_max_retries", None)
    return json.dumps(safe, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _format_value(value: object) -> str:
    if isinstance(value, tuple):
        return " | ".join(str(item) for item in value)
    return str(value)


def _trusted_product_context(
    customer_catalog: QuestionCatalog,
    spec: ResolutionInputSpec,
) -> tuple[str, Any]:
    """Build product context for AI without invoking old semantic product rules."""

    trusted_spec = replace(
        spec,
        supplier_snapshots=(),
        official_snapshots=(),
        evidence_packets=(),
        image_paths=(),
    )
    trusted = build_resolution_inputs(customer_catalog, trusted_spec)
    parts: list[str] = []
    canonical_context = customer_context_for_resolution(customer_catalog, trusted_spec)
    if canonical_context:
        parts.append("Customer/product context:\n" + canonical_context)

    evidence_lines: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for item in trusted.bundle.evidence:
        if item.source_type not in _TRUSTED_CONTEXT_SOURCE_TYPES:
            continue
        value = _format_value(item.value).strip()
        if not value:
            continue
        fingerprint = (item.key.strip(), value, item.source_type)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        evidence_lines.append(
            f"- {item.key}: {value} [source_type={item.source_type}; source={item.source_reference}]"
        )
    if evidence_lines:
        parts.append("Explicit customer/structured facts:\n" + "\n".join(evidence_lines))

    return "\n\n".join(parts).strip(), trusted


def _decision_summary(decisions: list[Any]) -> dict[str, int]:
    counts = {READY: 0, REVIEW: 0, CONFLICT: 0, MISSING: 0, BUSINESS_LOCKED: 0}
    for decision in decisions:
        counts[decision.status] = counts.get(decision.status, 0) + 1
    return counts


def _search_requests(decisions: list[Any], fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_by_id = {field_id(field): field for field in fields}
    output: list[dict[str, Any]] = []
    for decision in decisions:
        if decision.status not in {MISSING, REVIEW} or not decision.search_queries:
            continue
        field = field_by_id.get(decision.field_id, {})
        output.append(
            {
                "field_id": decision.field_id,
                "label": str(field.get("label") or field.get("attribute_key") or ""),
                "section_heading": str(field.get("section_heading") or ""),
                "status": decision.status,
                "reason": decision.reason,
                "queries": list(decision.search_queries),
            }
        )
    return output


def main() -> int:
    args = build_parser().parse_args()
    if args.max_text_chars < 500:
        raise SystemExit("--max-text-chars 不能小于 500")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_text_chars:
        raise SystemExit("--overlap-chars 必须 >=0 且小于 --max-text-chars")

    customer_catalog = load_question_catalog(args.qa)
    live_fields = load_live_schema(args.live_schema)
    supplemental_text = _read_supplemental_text(args)
    spec = _input_spec(args, supplemental_text)
    product_context, trusted_inputs = _trusted_product_context(customer_catalog, spec)

    grounding = build_grounding_catalog(
        image_paths=args.image,
        supplier_snapshots=args.supplier_snapshot,
        official_snapshots=args.official_snapshot,
        supplemental_text=product_context,
        max_text_chars=args.max_text_chars,
        overlap_chars=args.overlap_chars,
    )
    if not grounding.sources:
        raise SystemExit(
            "没有可供 AI 解析的商品资料。请至少提供客户上下文、图片、supplier snapshot 或 official snapshot。"
        )

    try:
        provider_config = _provider_config(args)
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

    cache_dir = None if args.no_semantic_cache else Path(args.semantic_cache_dir)
    print("===== AI-FIRST PRODUCT RESOLUTION =====", flush=True)
    print(
        f"provider={provider_config.provider}, model={provider_config.model}, "
        f"thinking={provider_config.enable_thinking}, live_fields={len(live_fields)}, "
        f"sources={len(grounding.sources)}, logical_sources={grounding.logical_source_count}",
        flush=True,
    )
    print("execution_model=one_multimodal_call_per_product_normal_path", flush=True)

    result = run_ai_resolution(
        provider,
        live_fields,
        grounding,
        expected_identity=trusted_inputs.expected_identity,
        cache_dir=cache_dir,
        cache_namespace=_cache_namespace(provider_config),
        max_repair_attempts=args.max_repair_attempts,
    )
    packet_path = write_ai_decision_packet(result.packet, output_dir / "ai-decisions.json")

    search_requests = _search_requests(result.packet.decisions, live_fields)
    search_path = output_dir / "search-requests.json"
    search_path.write_text(
        json.dumps(search_requests, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = _decision_summary(result.packet.decisions)
    run_manifest = output_dir / "run-manifest.json"
    run_manifest.write_text(
        json.dumps(
            {
                "mode": "browser_free_ai_first_product_resolution",
                "qa_source": str(Path(args.qa).resolve()),
                "live_schema": str(Path(args.live_schema).resolve()),
                "live_field_count": len(live_fields),
                "provider_adapter": provider.name,
                "provider_config": provider_config.as_safe_dict(),
                "grounded_source_count": len(grounding.sources),
                "grounded_logical_source_count": grounding.logical_source_count,
                "customer_context_chars": len(product_context),
                "model_calls": result.model_calls,
                "cache_hit": result.cache_hit,
                "repair_attempts": result.repair_attempts,
                "elapsed_seconds": round(result.elapsed_seconds, 3),
                "decision_summary": summary,
                "search_request_fields": len(search_requests),
                "writes_performed": 0,
                "save_clicked": False,
                "send_to_qc_clicked": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n===== AI-FIRST RESOLUTION COMPLETE =====")
    print(
        f"ready={summary[READY]}, review={summary[REVIEW]}, conflict={summary[CONFLICT]}, "
        f"missing={summary[MISSING]}, business_locked={summary[BUSINESS_LOCKED]}"
    )
    print(
        f"model_calls={result.model_calls}, cache_hit={result.cache_hit}, "
        f"repair_attempts={result.repair_attempts}, elapsed={result.elapsed_seconds:.1f}s"
    )
    print(f"AI decisions: {packet_path.resolve()}")
    print(f"Search requests: {search_path.resolve()}")
    print(f"Source manifest: {source_manifest_path.resolve()}")
    print(f"Run manifest: {run_manifest.resolve()}")
    print("没有打开 Makro；没有填写字段；没有 Save；没有 Send to QC。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
