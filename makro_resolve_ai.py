"""Resolve the current Makro product schema without touching Makro.

Production pipeline:
1) optionally capture the exact supplier product URL + selected SKU context;
2) AI fills Makro fields directly from that exact local evidence in mechanical batches;
3) web-search only fields still unresolved and fill those blanks directly.

There is one Makro field table throughout. There is no intermediate Product Profile and no
Final Resolve model. Python only collects sources, batches work, preserves provenance,
locks seller-business fields, checks mechanical schema constraints and keeps browser writes off.
"""

from __future__ import annotations

import argparse
import json
import os
import time
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
    write_ai_decision_packet,
)
from app.field_mapping import run_field_mapping
from app.live_schema import load_live_schema
from app.product_context import build_ai_product_context
from app.providers.dashscope_web_search import DashScopeWebSearchProvider
from app.providers.registry import (
    ProviderConfig,
    ProviderConfigurationError,
    SUPPORTED_PROVIDERS,
    build_semantic_provider,
    default_api_key_env,
    validate_provider_config,
)
from app.qa_catalog import load_question_catalog
from app.resolver_inputs import ResolutionInputSpec
from app.semantic_grounding import build_grounding_catalog
from app.source_capture import DEFAULT_SOURCE_CDP_PORT, SourceAccessBlocked, capture_product_source
from app.web_enrichment import WebEnrichmentResult, run_web_enrichment, write_enriched_ai_decision_packet


EXECUTION_MODEL = "exact_product_source_then_parallel_local_fill_then_unresolved_web_fill"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "从确定商品资料直接填写 Makro live schema：可从商品链接自动采集当前页面，"
            "AI直接填字段，只对仍为空的字段联网补充。不会打开或修改 Makro。"
        )
    )
    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS, default="openai-compatible")
    parser.add_argument("--model", default="qwen3.7-plus", help="本地商品字段填写模型。")
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--base-url", default="")
    parser.add_argument(
        "--structured-mode",
        choices=("auto", "prompt_only", "json_object"),
        default="json_object",
        help="本地 Qwen 默认使用原生 JSON mode。",
    )
    thinking = parser.add_mutually_exclusive_group()
    thinking.add_argument("--enable-thinking", dest="enable_thinking", action="store_true")
    thinking.add_argument("--disable-thinking", dest="enable_thinking", action="store_false")
    parser.set_defaults(enable_thinking=False)

    parser.add_argument("--qa", required=True, help="Makro 问题模板/客户已有答案文件")
    parser.add_argument("--live-schema", required=True, help="read-only Makro planner 导出的 live-schema.json")
    parser.add_argument("--sku", default="")
    parser.add_argument("--expected-model", default="")
    parser.add_argument("--expected-brand", default="")

    parser.add_argument(
        "--product-url",
        default="",
        help="当前商品的供应商链接；提供后会用独立 source Edge 自动滚动采集文本、参数和整页图片。",
    )
    parser.add_argument("--source-profile-dir", default="browser_profiles/source-edge")
    parser.add_argument("--source-cdp-port", type=int, default=DEFAULT_SOURCE_CDP_PORT)
    parser.add_argument("--source-wait-ms", type=int, default=1800)
    parser.add_argument("--source-scroll-wait-ms", type=int, default=180)
    parser.add_argument("--source-max-scroll-steps", type=int, default=120)
    parser.add_argument("--source-max-visible-text-chars", type=int, default=120_000)
    parser.add_argument(
        "--source-use-current-page",
        action="store_true",
        help="source Edge 已人工完成合法登录/验证后，不重新导航，直接采集当前页。",
    )

    # Optional extra evidence remains supported, but is not required by the new primary flow.
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

    parser.add_argument("--field-batch-size", type=int, default=12)
    parser.add_argument("--field-concurrency", type=int, default=4)

    parser.add_argument("--web-enrich", choices=("auto", "off"), default="auto")
    parser.add_argument("--web-search-model", default="qwen3.7-max")
    parser.add_argument("--web-base-url", default="")
    parser.add_argument("--web-batch-size", type=int, default=5)
    parser.add_argument("--web-concurrency", type=int, default=3)

    parser.add_argument(
        "--semantic-cache-dir",
        default="logs/semantic-cache",
        help="本地字段 batch 和 Web Fill batch 的 content-addressed cache。",
    )
    parser.add_argument("--no-semantic-cache", action="store_true")
    parser.add_argument("--output-dir", default="logs/ai-resolver")
    return parser


def _read_supplemental_text(args: argparse.Namespace) -> str:
    parts = [args.supplemental_text]
    if args.supplemental_text_file:
        parts.append(Path(args.supplemental_text_file).read_text(encoding="utf-8"))
    return "\n".join(part for part in parts if part and part.strip())


def _input_spec(
    args: argparse.Namespace,
    supplemental_text: str,
    *,
    image_paths: list[str],
    supplier_snapshots: list[str],
    product_url: str,
) -> ResolutionInputSpec:
    return ResolutionInputSpec(
        sku=args.sku,
        expected_model=args.expected_model,
        expected_brand=args.expected_brand,
        product_table=args.product_table,
        facts_json=tuple(args.facts_json),
        supplier_snapshots=tuple(supplier_snapshots),
        official_snapshots=tuple(args.official_snapshot),
        supplemental_text=supplemental_text,
        image_paths=tuple(image_paths),
        product_url=product_url or None,
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


def _decision_summary(decisions: list[Any]) -> dict[str, int]:
    counts = {READY: 0, REVIEW: 0, CONFLICT: 0, MISSING: 0, BUSINESS_LOCKED: 0}
    for decision in decisions:
        counts[decision.status] = counts.get(decision.status, 0) + 1
    return counts


def _search_requests(decisions: list[Any], fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_by_id = {field_id(item): item for item in fields}
    output: list[dict[str, Any]] = []
    for decision in decisions:
        if decision.status not in {MISSING, REVIEW}:
            continue
        item = field_by_id.get(decision.field_id, {})
        output.append(
            {
                "field_id": decision.field_id,
                "attribute_key": str(item.get("attribute_key") or ""),
                "label": str(item.get("label") or item.get("attribute_key") or ""),
                "section_heading": str(item.get("section_heading") or ""),
                "status": decision.status,
                "reason": decision.reason,
                "queries": list(decision.search_queries),
            }
        )
    return output


def _dashscope_web_provider(
    args: argparse.Namespace,
    config: ProviderConfig,
) -> tuple[DashScopeWebSearchProvider | None, str]:
    if args.web_enrich == "off":
        return None, "disabled"
    if config.provider != "openai-compatible":
        return None, "current provider is not DashScope OpenAI-compatible"
    if "dashscope.aliyuncs.com" not in config.base_url.casefold():
        return None, "current compatible endpoint is not dashscope.aliyuncs.com"
    api_key = os.getenv(config.api_key_env, "").strip()
    if not api_key:
        return None, f"missing API key env {config.api_key_env}"
    return (
        DashScopeWebSearchProvider(
            model=args.web_search_model.strip(),
            api_key=api_key,
            base_url=args.web_base_url.strip() or config.base_url,
            request_timeout_seconds=args.request_timeout_seconds,
        ),
        "available",
    )


def _empty_web_result(packet: Any, warning: str = "") -> WebEnrichmentResult:
    return WebEnrichmentResult(packet=packet, warnings=[warning] if warning else [])


def _set_progress(provider: Any, prefix: str) -> None:
    setter = getattr(provider, "set_progress_callback", None)
    if callable(setter):
        setter(lambda message: print(f"[{prefix}] {message}", flush=True))


def main() -> int:
    started = time.monotonic()
    args = build_parser().parse_args()
    if args.max_text_chars < 500:
        raise SystemExit("--max-text-chars 不能小于 500")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_text_chars:
        raise SystemExit("--overlap-chars 必须 >=0 且小于 --max-text-chars")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / f"resolve-ai-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    image_paths = list(args.image)
    supplier_snapshots = list(args.supplier_snapshot)
    product_url = args.product_url.strip()
    capture_info: dict[str, Any] = {"requested": bool(product_url)}

    if product_url:
        print("===== PRIMARY PRODUCT SOURCE CAPTURE =====", flush=True)
        try:
            captured = capture_product_source(
                product_url,
                output_dir=output_dir / "primary-source",
                profile_dir=args.source_profile_dir,
                cdp_port=args.source_cdp_port,
                initial_wait_ms=args.source_wait_ms,
                scroll_wait_ms=args.source_scroll_wait_ms,
                max_scroll_steps=args.source_max_scroll_steps,
                max_visible_text_chars=args.source_max_visible_text_chars,
                use_current_page=args.source_use_current_page,
            )
        except SourceAccessBlocked as exc:
            print(str(exc), flush=True)
            print(
                f"source Edge 保持打开在 127.0.0.1:{args.source_cdp_port}；"
                "人工完成合法验证后加 --source-use-current-page 重试。",
                flush=True,
            )
            return 2
        supplier_snapshots.insert(0, str(captured.snapshot_path))
        image_paths.insert(0, str(captured.screenshot_path))
        product_url = captured.snapshot.final_url or product_url
        capture_info = {
            "requested": True,
            "final_url": product_url,
            "snapshot": str(captured.snapshot_path.resolve()),
            "screenshot": str(captured.screenshot_path.resolve()),
            "table_rows": len(captured.snapshot.table_rows),
            "visible_text_chars": len(captured.snapshot.visible_text),
            "source_edge": "new" if captured.launched_now else "reused",
        }
        print(
            f"captured exact product page: table_rows={capture_info['table_rows']}, "
            f"visible_text_chars={capture_info['visible_text_chars']}",
            flush=True,
        )

    customer_catalog = load_question_catalog(args.qa)
    live_fields = load_live_schema(args.live_schema)
    spec = _input_spec(
        args,
        _read_supplemental_text(args),
        image_paths=image_paths,
        supplier_snapshots=supplier_snapshots,
        product_url=product_url,
    )
    product_context = build_ai_product_context(customer_catalog, spec)
    grounding = build_grounding_catalog(
        image_paths=image_paths,
        supplier_snapshots=supplier_snapshots,
        official_snapshots=args.official_snapshot,
        supplemental_text=product_context.text,
        max_text_chars=args.max_text_chars,
        overlap_chars=args.overlap_chars,
    )
    if not grounding.sources:
        raise SystemExit("没有可供 AI 解析的商品资料。请提供 --product-url 或其他商品资料。")

    try:
        provider_config = _provider_config(args)
        provider = build_semantic_provider(provider_config)
    except ProviderConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    source_manifest_path = output_dir / "source-manifest.json"
    source_manifest_path.write_text(
        json.dumps(grounding.as_manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    cache_dir = None if args.no_semantic_cache else Path(args.semantic_cache_dir)
    namespace = _cache_namespace(provider_config)
    expected_identity = product_context.trusted_inputs.expected_identity

    print("===== DIRECT PRODUCT RESOLUTION =====", flush=True)
    print(
        f"provider={provider_config.provider}, model={provider_config.model}, "
        f"live_fields={len(live_fields)}, citation_sources={len(grounding.sources)}, "
        f"product_url={'yes' if product_url else 'no'}",
        flush=True,
    )
    print(f"execution_model={EXECUTION_MODEL}", flush=True)

    _set_progress(provider, "LOCAL")
    mapping_result = run_field_mapping(
        provider,
        live_fields,
        grounding,
        expected_identity=expected_identity,
        product_url=product_url,
        batch_size=args.field_batch_size,
        concurrency=args.field_concurrency,
        cache_dir=cache_dir,
        cache_namespace=namespace,
    )
    local_packet = mapping_result.packet
    local_packet_path = write_ai_decision_packet(local_packet, output_dir / "ai-decisions.local.json")
    local_summary = _decision_summary(local_packet.decisions)
    search_requests = _search_requests(local_packet.decisions, live_fields)
    search_path = output_dir / "search-requests.json"
    search_path.write_text(json.dumps(search_requests, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"local_fill=DONE batches={mapping_result.batch_count} calls={mapping_result.model_calls} "
        f"cache_hits={mapping_result.cache_hits} failed_batches={mapping_result.failed_batches} "
        f"elapsed={mapping_result.elapsed_seconds:.3f}s blanks_for_web={len(search_requests)}",
        flush=True,
    )

    web_provider, web_availability = _dashscope_web_provider(args, provider_config)
    if search_requests and web_provider is not None:
        _set_progress(web_provider, "WEB")
        print(f"web_fill=START targets={len(search_requests)} model={web_provider.model}", flush=True)
        web_result = run_web_enrichment(
            web_provider,
            local_packet,
            live_fields,
            grounding,
            product_url=product_url,
            batch_size=args.web_batch_size,
            concurrency=args.web_concurrency,
            cache_dir=cache_dir,
        )
        print(
            f"web_fill=DONE batches={web_result.search_batch_count} calls={web_result.search_model_calls} "
            f"cache_hits={web_result.search_cache_hits} failed_batches={web_result.search_failed_batches} "
            f"evidence={len(web_result.evidence)} sources={len(web_result.web_sources)} "
            f"elapsed={web_result.search_elapsed_seconds:.3f}s",
            flush=True,
        )
        for warning in web_result.warnings:
            print(f"web_warning={warning}", flush=True)
    else:
        reason = "no unresolved non-business fields" if not search_requests else web_availability
        web_result = _empty_web_result(local_packet, reason)
        print(f"web_fill=SKIP reason={reason}", flush=True)

    packet_path = write_enriched_ai_decision_packet(
        web_result.packet,
        web_result.web_sources,
        output_dir / "ai-decisions.json",
    )
    web_sources_path = output_dir / "web-search-sources.json"
    web_sources_path.write_text(
        json.dumps([source.as_dict() for source in web_result.web_sources], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    web_evidence_path = output_dir / "web-evidence.json"
    web_evidence_path.write_text(
        json.dumps([item.as_dict() for item in web_result.evidence], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    final_summary = _decision_summary(web_result.packet.decisions)
    total_model_calls = mapping_result.model_calls + web_result.search_model_calls
    total_elapsed = time.monotonic() - started
    run_manifest = output_dir / "run-manifest.json"
    run_manifest.write_text(
        json.dumps(
            {
                "mode": "exact_source_direct_ai_resolution",
                "execution_model": EXECUTION_MODEL,
                "qa_source": str(Path(args.qa).resolve()),
                "live_schema": str(Path(args.live_schema).resolve()),
                "live_field_count": len(live_fields),
                "provider_adapter": provider.name,
                "provider_config": provider_config.as_safe_dict(),
                "web_search_model": args.web_search_model,
                "primary_product_url": product_url,
                "source_capture": capture_info,
                "grounded_source_count": len(grounding.sources),
                "customer_context_chars": len(product_context.text),
                "local_fill": {
                    "batch_size": args.field_batch_size,
                    "concurrency": args.field_concurrency,
                    "batch_count": mapping_result.batch_count,
                    "model_calls": mapping_result.model_calls,
                    "cache_hits": mapping_result.cache_hits,
                    "failed_batches": mapping_result.failed_batches,
                    "elapsed_seconds": round(mapping_result.elapsed_seconds, 3),
                    "decision_summary": local_summary,
                },
                "web_fill": {
                    "mode": args.web_enrich,
                    "availability": web_availability,
                    "requested_fields": len(search_requests),
                    "searched": web_result.searched,
                    "batch_size": args.web_batch_size,
                    "concurrency": args.web_concurrency,
                    "batch_count": web_result.search_batch_count,
                    "model_calls": web_result.search_model_calls,
                    "cache_hits": web_result.search_cache_hits,
                    "failed_batches": web_result.search_failed_batches,
                    "evidence_count": len(web_result.evidence),
                    "source_count": len(web_result.web_sources),
                    "elapsed_seconds": round(web_result.search_elapsed_seconds, 3),
                    "warnings": list(web_result.warnings),
                },
                "final_decision_summary": final_summary,
                "total_model_calls": total_model_calls,
                "wall_elapsed_seconds": round(total_elapsed, 3),
                "writes_performed": 0,
                "save_clicked": False,
                "send_to_qc_clicked": False,
                "outputs": {
                    "local_decisions": str(local_packet_path.resolve()),
                    "final_decisions": str(packet_path.resolve()),
                    "search_requests": str(search_path.resolve()),
                    "web_sources": str(web_sources_path.resolve()),
                    "web_evidence": str(web_evidence_path.resolve()),
                    "source_manifest": str(source_manifest_path.resolve()),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n===== DIRECT RESOLUTION COMPLETE =====", flush=True)
    print(
        f"ready={final_summary[READY]}, review={final_summary[REVIEW]}, conflict={final_summary[CONFLICT]}, "
        f"missing={final_summary[MISSING]}, business_locked={final_summary[BUSINESS_LOCKED]}",
        flush=True,
    )
    print(
        f"local_fill_calls={mapping_result.model_calls}, web_fill_calls={web_result.search_model_calls}, "
        f"total_calls={total_model_calls}, wall={total_elapsed:.3f}s",
        flush=True,
    )
    print(f"AI decisions: {packet_path}", flush=True)
    print(f"Run manifest: {run_manifest}", flush=True)
    print("没有打开或修改 Makro；没有填写字段；没有 Save；没有 Send to QC。", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProviderConfigurationError as exc:
        raise SystemExit(str(exc)) from exc
