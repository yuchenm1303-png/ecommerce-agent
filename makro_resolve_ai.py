"""Resolve the current Makro product schema without opening Makro.

Production model:
1) one compact whole-product multimodal AI fill over all local evidence;
2) only for unresolved fields that request research, at most one sourced web pass.

AI owns product semantics. Python owns provenance, seller business locks, schema
identity, hard marketplace constraints and browser safety downstream.
"""

from __future__ import annotations

import argparse
import json
import os
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
from app.web_enrichment import (
    WebEnrichmentResult,
    run_web_enrichment,
    write_enriched_ai_decision_packet,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "以当前 Makro live schema 为唯一目标：先用一次整商品 AI 填本地能确定的字段，"
            "再按需用一次有来源联网搜索补 unresolved 字段。全程不打开或修改 Makro。"
        )
    )
    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS, default="openai-compatible")
    parser.add_argument("--model", default="qwen3.6-plus", help="本地商品解析模型；默认 qwen3.6-plus。")
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--base-url", default="")
    parser.add_argument(
        "--structured-mode",
        choices=("auto", "prompt_only", "json_object"),
        default="json_object",
        help="本地 Qwen3.6 默认使用原生 JSON mode；需要兼容旧服务时可显式改为 prompt_only。",
    )
    thinking = parser.add_mutually_exclusive_group()
    thinking.add_argument("--enable-thinking", dest="enable_thinking", action="store_true")
    thinking.add_argument("--disable-thinking", dest="enable_thinking", action="store_false")
    parser.set_defaults(enable_thinking=False)

    parser.add_argument("--qa", required=True, help="客户 QA/商品上下文文件")
    parser.add_argument(
        "--live-schema",
        required=True,
        help="read-only Makro planner 导出的 live-schema.json；AI 只回答这里的字段。",
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
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=12000,
        help="仅 prompt_only 模式使用；JSON mode 按官方建议不设置 max_tokens，避免截断 JSON。",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=120.0,
        help="每个本地/联网 AI 阶段的真实 wall-clock deadline。",
    )
    parser.add_argument("--max-text-chars", type=int, default=5000)
    parser.add_argument("--overlap-chars", type=int, default=250)
    parser.add_argument(
        "--max-repair-attempts",
        type=int,
        choices=(0, 1),
        default=0,
        help=(
            "默认 0：结构输出失败直接停止，不再把整商品和图片自动重发一遍。"
            "仅诊断时可显式设为 1。网络/API/timeout 永不 semantic repair。"
        ),
    )

    parser.add_argument(
        "--web-enrich",
        choices=("auto", "off"),
        default="auto",
        help=(
            "auto: 对第一遍仍 unresolved 的非经营字段追加最多一次带来源联网研究；off: 完全不联网。"
        ),
    )
    parser.add_argument("--web-search-model", default="", help="联网阶段模型；默认复用 --model。")
    parser.add_argument(
        "--web-base-url",
        default="",
        help="Responses API base URL；默认复用 --base-url。",
    )

    parser.add_argument(
        "--semantic-cache-dir",
        default="logs/semantic-cache",
        help="本地整商品决策和联网补全共用 content-addressed cache 根目录。",
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


def _decision_summary(decisions: list[Any]) -> dict[str, int]:
    counts = {READY: 0, REVIEW: 0, CONFLICT: 0, MISSING: 0, BUSINESS_LOCKED: 0}
    for decision in decisions:
        counts[decision.status] = counts.get(decision.status, 0) + 1
    return counts


def _search_requests(decisions: list[Any], fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_by_id = {field_id(item): item for item in fields}
    output: list[dict[str, Any]] = []
    for decision in decisions:
        if decision.status not in {MISSING, REVIEW, CONFLICT}:
            continue
        item = field_by_id.get(decision.field_id, {})
        output.append(
            {
                "field_id": decision.field_id,
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
            model=args.web_search_model.strip() or config.model,
            api_key=api_key,
            base_url=args.web_base_url.strip() or config.base_url,
            request_timeout_seconds=args.request_timeout_seconds,
        ),
        "available",
    )


def _empty_web_result(packet: Any, reason: str = "") -> WebEnrichmentResult:
    return WebEnrichmentResult(packet=packet, warning=reason)


def _set_progress(provider: Any, prefix: str) -> None:
    setter = getattr(provider, "set_progress_callback", None)
    if callable(setter):
        setter(lambda message: print(f"[{prefix}] {message}", flush=True))


def main() -> int:
    args = build_parser().parse_args()
    if args.max_text_chars < 500:
        raise SystemExit("--max-text-chars 不能小于 500")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_text_chars:
        raise SystemExit("--overlap-chars 必须 >=0 且小于 --max-text-chars")

    customer_catalog = load_question_catalog(args.qa)
    live_fields = load_live_schema(args.live_schema)
    spec = _input_spec(args, _read_supplemental_text(args))
    product_context = build_ai_product_context(customer_catalog, spec)
    grounding = build_grounding_catalog(
        image_paths=args.image,
        supplier_snapshots=args.supplier_snapshot,
        official_snapshots=args.official_snapshot,
        supplemental_text=product_context.text,
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
    _set_progress(provider, "LOCAL")

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
        f"structured_mode={provider_config.structured_mode}, thinking={provider_config.enable_thinking}, "
        f"wall_deadline={provider_config.request_timeout_seconds:.0f}s, "
        f"live_fields={len(live_fields)}, citation_sources={len(grounding.sources)}",
        flush=True,
    )
    print("execution_model=1 local whole-product fill + optional 1 sourced web fill", flush=True)
    print(f"automatic_full_product_repair={args.max_repair_attempts}", flush=True)

    local_result = run_ai_resolution(
        provider,
        live_fields,
        grounding,
        expected_identity=product_context.trusted_inputs.expected_identity,
        cache_dir=cache_dir,
        cache_namespace=_cache_namespace(provider_config),
        max_repair_attempts=args.max_repair_attempts,
    )
    local_summary = _decision_summary(local_result.packet.decisions)
    search_requests = _search_requests(local_result.packet.decisions, live_fields)
    search_path = output_dir / "search-requests.json"
    search_path.write_text(
        json.dumps(search_requests, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    web_provider, web_availability = _dashscope_web_provider(args, provider_config)
    if search_requests and web_provider is not None:
        _set_progress(web_provider, "WEB")
        print(
            f"web_enrichment=START targets={len(search_requests)} model={web_provider.model}",
            flush=True,
        )
        web_result = run_web_enrichment(
            web_provider,
            local_result.packet,
            live_fields,
            grounding,
            cache_dir=cache_dir,
        )
        print(
            f"web_enrichment=DONE calls={web_result.model_calls} cache_hit={web_result.cache_hit} "
            f"targets={web_result.target_field_count} elapsed={web_result.elapsed_seconds:.3f}s",
            flush=True,
        )
        if web_result.warning:
            print(f"web_enrichment_warning={web_result.warning}", flush=True)
    else:
        reason = "no unresolved field requested web research" if not search_requests else web_availability
        web_result = _empty_web_result(local_result.packet, reason)
        print(f"web_enrichment=SKIP reason={reason}", flush=True)

    if web_result.searched:
        write_ai_decision_packet(local_result.packet, output_dir / "ai-decisions.local.json")

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

    final_summary = _decision_summary(web_result.packet.decisions)
    total_model_calls = local_result.model_calls + web_result.model_calls
    total_elapsed = local_result.elapsed_seconds + web_result.elapsed_seconds
    run_manifest = output_dir / "run-manifest.json"
    run_manifest.write_text(
        json.dumps(
            {
                "mode": "browser_free_ai_first_product_resolution",
                "execution_model": "one_local_whole_product_fill_plus_optional_one_sourced_web_fill",
                "qa_source": str(Path(args.qa).resolve()),
                "live_schema": str(Path(args.live_schema).resolve()),
                "live_field_count": len(live_fields),
                "provider_adapter": provider.name,
                "provider_config": provider_config.as_safe_dict(),
                "grounded_source_count": len(grounding.sources),
                "customer_context_chars": len(product_context.text),
                "local_ai": {
                    "model_calls": local_result.model_calls,
                    "cache_hit": local_result.cache_hit,
                    "repair_attempts": local_result.repair_attempts,
                    "elapsed_seconds": round(local_result.elapsed_seconds, 3),
                    "decision_summary": local_summary,
                },
                "web_enrichment": {
                    "mode": args.web_enrich,
                    "availability": web_availability,
                    "requested_fields": len(search_requests),
                    "searched": web_result.searched,
                    "model_calls": web_result.model_calls,
                    "cache_hit": web_result.cache_hit,
                    "target_field_count": web_result.target_field_count,
                    "returned_source_count": len(web_result.web_sources),
                    "elapsed_seconds": round(web_result.elapsed_seconds, 3),
                    "warning": web_result.warning,
                },
                "total_model_calls": total_model_calls,
                "total_ai_elapsed_seconds": round(total_elapsed, 3),
                "final_decision_summary": final_summary,
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
        f"ready={final_summary[READY]}, review={final_summary[REVIEW]}, "
        f"conflict={final_summary[CONFLICT]}, missing={final_summary[MISSING]}, "
        f"business_locked={final_summary[BUSINESS_LOCKED]}"
    )
    print(
        f"local_calls={local_result.model_calls}, web_calls={web_result.model_calls}, "
        f"total_calls={total_model_calls}, local_cache={local_result.cache_hit}, "
        f"web_cache={web_result.cache_hit}, total_ai_elapsed={total_elapsed:.1f}s"
    )
    print(f"AI decisions: {packet_path.resolve()}")
    print(f"Search requests: {search_path.resolve()}")
    print(f"Web sources: {web_sources_path.resolve()}")
    print(f"Source manifest: {source_manifest_path.resolve()}")
    print(f"Run manifest: {run_manifest.resolve()}")
    print("没有打开 Makro；没有填写字段；没有 Save；没有 Send to QC。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
