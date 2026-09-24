from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .ai_decisions import (
    BUSINESS_LOCKED,
    CONFLICT,
    MISSING,
    READY,
    REVIEW,
    field_id,
    write_ai_decision_packet,
)
from .best_effort_inference import run_best_effort_inference
from .business_fields import generate_listing_sku
from .compact_evidence import build_compact_evidence, write_compact_evidence
from .image_evidence import run_image_evidence, write_image_observations
from .listing_images import (
    LISTING_IMAGE_POLICY_VERSION,
    select_listing_images,
    write_listing_image_selection,
)
from .live_schema import load_live_schema
from .product_facts import run_product_facts
from .product_input import acquire_product_input
from .providers.dashscope_web_search import DashScopeWebSearchProvider
from .providers.registry import (
    ProviderConfig,
    ProviderConfigurationError,
    build_semantic_provider,
    default_api_key_env,
    validate_provider_config,
)
from .semantic_grounding import GroundedSource, GroundingCatalog, build_grounding_catalog
from .source_snapshot import SourceAccessBlocked
from .web_enrichment import (
    WebEnrichmentResult,
    build_product_fingerprint,
    run_web_enrichment,
    write_enriched_ai_decision_packet,
)


SUPPLIER_EXECUTION_MODEL = (
    "product_url_capture_then_compact_product_facts_then_unresolved_web_fill_"
    "then_text_only_best_effort_inference"
)
PRODUCT_PACK_EXECUTION_MODEL = (
    "customer_product_pack_then_compact_product_facts_then_unresolved_web_fill_"
    "then_text_only_best_effort_inference"
)


def provider_config(args: Any) -> ProviderConfig:
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


def cache_namespace(config: ProviderConfig) -> str:
    safe = config.as_safe_dict()
    safe.pop("request_timeout_seconds", None)
    safe.pop("sdk_max_retries", None)
    return json.dumps(safe, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def decision_summary(decisions: list[Any]) -> dict[str, int]:
    counts = {READY: 0, REVIEW: 0, CONFLICT: 0, MISSING: 0, BUSINESS_LOCKED: 0}
    for decision in decisions:
        counts[decision.status] = counts.get(decision.status, 0) + 1
    return counts


def search_requests(decisions: list[Any], fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_by_id = {field_id(item): item for item in fields}
    output: list[dict[str, Any]] = []
    for decision in decisions:
        if decision.status != MISSING:
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


def dashscope_web_provider(
    args: Any,
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


def empty_web_result(packet: Any, warning: str = "") -> WebEnrichmentResult:
    return WebEnrichmentResult(packet=packet, warnings=[warning] if warning else [])


def set_progress(provider: Any, prefix: str) -> None:
    setter = getattr(provider, "set_progress_callback", None)
    if callable(setter):
        setter(lambda message: print(f"[{prefix}] {message}", flush=True))


def _customer_catalog(
    snapshots: tuple[Path, ...],
    *,
    max_text_chars: int,
    overlap_chars: int,
) -> GroundingCatalog:
    if not snapshots:
        return GroundingCatalog([])
    temporary = build_grounding_catalog(
        official_snapshots=[str(path) for path in snapshots],
        max_text_chars=max_text_chars,
        overlap_chars=overlap_chars,
    )
    sources: list[GroundedSource] = []
    for source in temporary.sources:
        identifier = source.source_id
        if identifier.startswith("official:"):
            identifier = "customer-file:" + identifier[len("official:") :]
        sources.append(
            GroundedSource(
                source_id=identifier,
                source_type="customer_file",
                kind=source.kind,
                origin=source.origin,
                content=source.content,
                image_path=source.image_path,
                sha256=source.sha256,
            )
        )
    return GroundingCatalog(sources)


def _build_grounding(
    *,
    image_paths: list[str],
    supplier_snapshots: list[str],
    customer_snapshots: tuple[Path, ...],
    official_snapshots: list[str],
    max_text_chars: int,
    overlap_chars: int,
) -> GroundingCatalog:
    regular = build_grounding_catalog(
        image_paths=image_paths,
        supplier_snapshots=supplier_snapshots,
        official_snapshots=official_snapshots,
        max_text_chars=max_text_chars,
        overlap_chars=overlap_chars,
    )
    customer = _customer_catalog(
        customer_snapshots,
        max_text_chars=max_text_chars,
        overlap_chars=overlap_chars,
    )
    return GroundingCatalog([*regular.sources, *customer.sources])


def _capture_info(acquired: Any, product_images: list[str], listing_images: list[str]) -> dict[str, Any]:
    snapshot = acquired.snapshot
    return {
        "requested": True,
        "input_mode": acquired.mode,
        "final_url": acquired.product_reference_url,
        "snapshot": str(acquired.snapshot_path.resolve()),
        "screenshot": str(acquired.screenshot_path.resolve()) if acquired.screenshot_path else "",
        "product_pack_manifest": (
            str(acquired.pack_manifest_path.resolve()) if acquired.pack_manifest_path else ""
        ),
        "customer_snapshot_count": len(acquired.customer_snapshot_paths),
        "table_rows": len(snapshot.table_rows),
        "visible_text_chars": len(snapshot.visible_text),
        "json_ld_items": len(snapshot.json_ld),
        "embedded_data_items": len(snapshot.embedded_data),
        "raw_embedded_chars": sum(len(item) for item in snapshot.embedded_data),
        "raw_visible_text_chars": len(snapshot.visible_text),
        "product_image_urls": len(getattr(snapshot, "image_urls", [])),
        "product_images_downloaded": len(product_images),
        "product_images": [str(Path(path).resolve()) for path in product_images],
        "listing_image_policy_version": LISTING_IMAGE_POLICY_VERSION,
        "listing_images_selected": len(listing_images),
        "listing_images": [str(Path(path).resolve()) for path in listing_images],
        "source_cache_hit": acquired.source_cache_hit,
        "source_edge": (
            "new" if acquired.source_edge_launched else "reused"
            if acquired.mode == "supplier_url"
            else "not_used"
        ),
        "warnings": list(acquired.warnings),
    }


def run_resolver(args: Any) -> int:
    started = time.monotonic()
    if args.max_text_chars < 500:
        raise SystemExit("--max-text-chars 不能小于 500")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_text_chars:
        raise SystemExit("--overlap-chars 必须 >=0 且小于 --max-text-chars")
    if args.source_cache_ttl_seconds < 0:
        raise SystemExit("--source-cache-ttl-seconds 不能为负数")
    if not 1 <= args.image_batch_size <= 8:
        raise SystemExit("--image-batch-size must be in 1..8")
    if not 1 <= args.image_concurrency <= 12:
        raise SystemExit("--image-concurrency must be in 1..12")
    if not 1 <= args.local_batch_size <= 32:
        raise SystemExit("--local-batch-size must be in 1..32")
    if not 1 <= args.local_concurrency <= 12:
        raise SystemExit("--local-concurrency must be in 1..12")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / f"resolve-ai-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("===== PRIMARY PRODUCT SOURCE CAPTURE =====", flush=True)
    try:
        acquired = acquire_product_input(
            output_dir=output_dir / "primary-source",
            product_url=str(args.product_url or ""),
            product_files=list(args.product_file or []),
            product_pack_manifest=str(args.product_pack_manifest or "") or None,
            source_profile_dir=args.source_profile_dir,
            source_cdp_port=args.source_cdp_port,
            source_wait_ms=args.source_wait_ms,
            source_scroll_wait_ms=args.source_scroll_wait_ms,
            source_max_scroll_steps=args.source_max_scroll_steps,
            source_max_visible_text_chars=args.source_max_visible_text_chars,
            source_use_current_page=args.source_use_current_page,
            source_cache_dir=args.source_cache_dir,
            source_cache_ttl_seconds=args.source_cache_ttl_seconds,
            refresh_source=args.refresh_source,
        )
    except SourceAccessBlocked as exc:
        print(str(exc), flush=True)
        print(
            f"source Edge 保持打开在 127.0.0.1:{args.source_cdp_port}；"
            "人工完成合法验证后加 --source-use-current-page 重试。",
            flush=True,
        )
        return 2

    product_images = [str(path) for path in acquired.evidence_image_paths]
    listing_image_selection = select_listing_images(product_images)
    listing_images = [str(path) for path in listing_image_selection.selected]
    listing_image_selection_path = write_listing_image_selection(
        listing_image_selection,
        output_dir / "listing-image-selection.json",
    )

    fallback_visual = (
        [str(acquired.screenshot_path)]
        if acquired.screenshot_path is not None and acquired.screenshot_path.is_file()
        else []
    )
    image_paths = [*(product_images or fallback_visual), *list(args.image)]
    supplier_snapshots = [
        *[str(path) for path in acquired.supplier_snapshot_paths],
        *list(args.supplier_snapshot),
    ]
    product_reference_url = acquired.product_reference_url
    generated_sku = generate_listing_sku(product_reference_url)
    capture_info = _capture_info(acquired, product_images, listing_images)
    capture_info["listing_images_rejected"] = listing_image_selection.rejected_count
    capture_info["semantic_images_used"] = len(image_paths)

    print(
        f"product_input={acquired.mode} cache_hit={capture_info['source_cache_hit']} "
        f"table_rows={capture_info['table_rows']} "
        f"visible_text_chars={capture_info['visible_text_chars']} "
        f"customer_snapshots={capture_info['customer_snapshot_count']} "
        f"product_images={capture_info['product_images_downloaded']}",
        flush=True,
    )
    print(
        f"listing_images=SELECTED {len(listing_images)}/{len(product_images)} "
        f"rejected={listing_image_selection.rejected_count} "
        f"policy=v{LISTING_IMAGE_POLICY_VERSION}",
        flush=True,
    )

    live_fields = load_live_schema(args.live_schema)
    grounding = _build_grounding(
        image_paths=image_paths,
        supplier_snapshots=supplier_snapshots,
        customer_snapshots=acquired.customer_snapshot_paths,
        official_snapshots=list(args.official_snapshot),
        max_text_chars=args.max_text_chars,
        overlap_chars=args.overlap_chars,
    )
    if not grounding.sources:
        raise SystemExit("商品输入没有形成可供 AI 使用的证据。")

    try:
        main_provider_config = provider_config(args)
        provider = build_semantic_provider(main_provider_config)
        fact_provider_config = replace(
            main_provider_config,
            model=args.fact_model or main_provider_config.model,
        )
        validate_provider_config(fact_provider_config)
        fact_provider = build_semantic_provider(fact_provider_config)
    except ProviderConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    source_manifest_path = output_dir / "source-manifest.json"
    source_manifest_path.write_text(
        json.dumps(grounding.as_manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    cache_dir = None if args.no_semantic_cache else Path(args.semantic_cache_dir)
    namespace = cache_namespace(main_provider_config)
    fact_namespace = cache_namespace(fact_provider_config)
    execution_model = (
        SUPPLIER_EXECUTION_MODEL
        if acquired.mode == "supplier_url"
        else PRODUCT_PACK_EXECUTION_MODEL
    )
    research_url = product_reference_url if acquired.mode == "supplier_url" else ""

    print("===== DIRECT PRODUCT RESOLUTION =====", flush=True)
    print(
        f"provider={main_provider_config.provider}, model={main_provider_config.model}, "
        f"fact_model={fact_provider_config.model}, "
        f"live_fields={len(live_fields)}, citation_sources={len(grounding.sources)}",
        flush=True,
    )
    print(f"product_url={product_reference_url}", flush=True)
    print(f"generated_listing_sku={generated_sku}", flush=True)
    print(f"execution_model={execution_model}", flush=True)

    set_progress(provider, "IMAGE")
    image_result = run_image_evidence(
        provider,
        grounding.sources,
        batch_size=args.image_batch_size,
        concurrency=args.image_concurrency,
        cache_dir=cache_dir,
        cache_namespace=namespace,
    )
    image_observations_path = write_image_observations(
        image_result.observations,
        output_dir / "image-observations.json",
    )
    print(
        f"image_evidence=DONE images={len(image_result.observations)} "
        f"batches={image_result.batch_count} calls={image_result.model_calls} "
        f"cache_hits={image_result.cache_hits} failed_batches={image_result.failed_batches} "
        f"elapsed={image_result.elapsed_seconds:.3f}s",
        flush=True,
    )

    compact_evidence = build_compact_evidence(grounding, image_result.observations)
    compact_evidence_path = write_compact_evidence(
        compact_evidence,
        output_dir / "compact-evidence.json",
    )
    print(
        f"compact_evidence=DONE chars={compact_evidence.chars} "
        f"text_sources={compact_evidence.text_source_count} "
        f"images={compact_evidence.image_count} facts={compact_evidence.image_fact_count}",
        flush=True,
    )

    set_progress(fact_provider, "LOCAL")
    fact_result = run_product_facts(
        fact_provider,
        live_fields,
        grounding,
        compact_evidence,
        product_url=research_url,
        batch_size=args.local_batch_size,
        concurrency=args.local_concurrency,
        cache_dir=cache_dir,
        cache_namespace=fact_namespace,
    )
    local_packet = fact_result.packet
    local_packet_path = write_ai_decision_packet(
        local_packet,
        output_dir / "ai-decisions.local.json",
    )
    local_summary = decision_summary(local_packet.decisions)
    unresolved = search_requests(local_packet.decisions, live_fields)
    search_path = output_dir / "search-requests.json"
    search_path.write_text(json.dumps(unresolved, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"product_facts=DONE facts={fact_result.fact_count} batches={fact_result.batch_count} "
        f"calls={fact_result.model_calls} cache_hits={fact_result.cache_hits} "
        f"failed_batches={fact_result.failed_batches} cache_hit={fact_result.cache_hit} "
        f"failed={fact_result.failed} elapsed={fact_result.elapsed_seconds:.3f}s "
        f"blanks_for_web={len(unresolved)}",
        flush=True,
    )

    web_provider, web_availability = dashscope_web_provider(args, main_provider_config)
    if unresolved and web_provider is not None:
        set_progress(web_provider, "WEB")
        print(f"web_fill=START targets={len(unresolved)} model={web_provider.model}", flush=True)
        web_result = run_web_enrichment(
            web_provider,
            local_packet,
            live_fields,
            grounding,
            product_url=research_url,
            batch_size=args.web_batch_size,
            concurrency=args.web_concurrency,
            cache_dir=cache_dir,
            compact_evidence=compact_evidence,
        )
        print(
            f"web_fill=DONE batches={web_result.search_batch_count} "
            f"calls={web_result.search_model_calls} cache_hits={web_result.search_cache_hits} "
            f"failed_batches={web_result.search_failed_batches} queries={web_result.reported_query_count} "
            f"inspected_sources={web_result.inspected_source_count} "
            f"source_facts={web_result.researched_fact_count} evidence={len(web_result.evidence)} "
            f"sources={len(web_result.web_sources)} elapsed={web_result.search_elapsed_seconds:.3f}s",
            flush=True,
        )
        for warning in web_result.warnings:
            print(f"web_warning={warning}", flush=True)
    else:
        reason = "no unresolved non-business fields" if not unresolved else web_availability
        web_result = empty_web_result(local_packet, reason)
        print(f"web_fill=SKIP reason={reason}", flush=True)

    web_packet_path = write_enriched_ai_decision_packet(
        web_result.packet,
        web_result.web_sources,
        output_dir / "ai-decisions.web.json",
    )
    product_fingerprint = build_product_fingerprint(
        web_result.packet,
        live_fields,
        grounding,
        compact_evidence=compact_evidence,
    )
    set_progress(fact_provider, "INFERENCE")
    inference_result = run_best_effort_inference(
        fact_provider,
        web_result.packet,
        live_fields,
        grounding,
        product_fingerprint=product_fingerprint,
        web_sources=web_result.web_sources,
        web_evidence=web_result.evidence,
        cache_dir=cache_dir,
        cache_namespace=fact_namespace,
    )
    print(
        f"best_effort_inference=DONE targets={inference_result.target_count} "
        f"ready={inference_result.ready_count} missing={inference_result.missing_count} "
        f"calls={inference_result.model_calls} cache_hit={inference_result.cache_hit} "
        f"failed={inference_result.failed} elapsed={inference_result.elapsed_seconds:.3f}s",
        flush=True,
    )
    if inference_result.warning:
        print(f"inference_warning={inference_result.warning}", flush=True)

    final_sources = [*web_result.web_sources]
    if inference_result.target_count and not inference_result.failed:
        final_sources.append(inference_result.inference_source)
    packet_path = write_enriched_ai_decision_packet(
        inference_result.packet,
        final_sources,
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

    final_summary = decision_summary(inference_result.packet.decisions)
    total_model_calls = (
        image_result.model_calls
        + fact_result.model_calls
        + web_result.search_model_calls
        + inference_result.model_calls
    )
    total_elapsed = time.monotonic() - started
    run_manifest = output_dir / "run-manifest.json"
    run_manifest.write_text(
        json.dumps(
            {
                "mode": (
                    "single_product_url_direct_ai_resolution"
                    if acquired.mode == "supplier_url"
                    else "customer_product_pack_direct_ai_resolution"
                ),
                "input_mode": acquired.mode,
                "execution_model": execution_model,
                "live_schema": str(Path(args.live_schema).resolve()),
                "live_field_count": len(live_fields),
                "provider_adapter": provider.name,
                "provider_config": main_provider_config.as_safe_dict(),
                "fact_provider_config": fact_provider_config.as_safe_dict(),
                "web_search_model": args.web_search_model,
                "primary_product_url": product_reference_url,
                "generated_listing_sku": generated_sku,
                "source_capture": capture_info,
                "grounded_source_count": len(grounding.sources),
                "image_evidence": {
                    "batch_size": args.image_batch_size,
                    "concurrency": args.image_concurrency,
                    "image_count": len(image_result.observations),
                    "batch_count": image_result.batch_count,
                    "model_calls": image_result.model_calls,
                    "cache_hits": image_result.cache_hits,
                    "failed_batches": image_result.failed_batches,
                    "elapsed_seconds": round(image_result.elapsed_seconds, 3),
                },
                "compact_evidence": {
                    "sha256": compact_evidence.sha256,
                    "chars": compact_evidence.chars,
                    "text_source_count": compact_evidence.text_source_count,
                    "image_count": compact_evidence.image_count,
                    "image_fact_count": compact_evidence.image_fact_count,
                },
                "product_facts": {
                    "batch_size": args.local_batch_size,
                    "concurrency": args.local_concurrency,
                    "batch_count": fact_result.batch_count,
                    "fact_count": fact_result.fact_count,
                    "model_calls": fact_result.model_calls,
                    "cache_hits": fact_result.cache_hits,
                    "failed_batches": fact_result.failed_batches,
                    "cache_hit": fact_result.cache_hit,
                    "failed": fact_result.failed,
                    "elapsed_seconds": round(fact_result.elapsed_seconds, 3),
                    "warning": fact_result.warning,
                    "decision_summary": local_summary,
                },
                "web_fill": {
                    "mode": args.web_enrich,
                    "availability": web_availability,
                    "requested_fields": len(unresolved),
                    "searched": web_result.searched,
                    "batch_size": args.web_batch_size,
                    "concurrency": args.web_concurrency,
                    "batch_count": web_result.search_batch_count,
                    "model_calls": web_result.search_model_calls,
                    "cache_hits": web_result.search_cache_hits,
                    "failed_batches": web_result.search_failed_batches,
                    "evidence_count": len(web_result.evidence),
                    "researched_fact_count": web_result.researched_fact_count,
                    "reported_query_count": web_result.reported_query_count,
                    "inspected_source_count": web_result.inspected_source_count,
                    "source_count": len(web_result.web_sources),
                    "elapsed_seconds": round(web_result.search_elapsed_seconds, 3),
                    "warnings": list(web_result.warnings),
                },
                "best_effort_inference": {
                    "requested_fields": inference_result.target_count,
                    "ready": inference_result.ready_count,
                    "missing": inference_result.missing_count,
                    "model_calls": inference_result.model_calls,
                    "cache_hit": inference_result.cache_hit,
                    "failed": inference_result.failed,
                    "elapsed_seconds": round(inference_result.elapsed_seconds, 3),
                    "warning": inference_result.warning,
                },
                "final_decision_summary": final_summary,
                "total_model_calls": total_model_calls,
                "wall_elapsed_seconds": round(total_elapsed, 3),
                "writes_performed": 0,
                "save_clicked": False,
                "send_to_qc_clicked": False,
                "outputs": {
                    "local_decisions": str(local_packet_path.resolve()),
                    "web_decisions": str(web_packet_path.resolve()),
                    "final_decisions": str(packet_path.resolve()),
                    "search_requests": str(search_path.resolve()),
                    "web_sources": str(web_sources_path.resolve()),
                    "web_evidence": str(web_evidence_path.resolve()),
                    "source_manifest": str(source_manifest_path.resolve()),
                    "primary_source_snapshot": str(acquired.snapshot_path.resolve()),
                    "primary_source_screenshot": (
                        str(acquired.screenshot_path.resolve()) if acquired.screenshot_path else ""
                    ),
                    "primary_source_product_images": [
                        str(Path(path).resolve()) for path in product_images
                    ],
                    "primary_source_listing_images": [
                        str(Path(path).resolve()) for path in listing_images
                    ],
                    "primary_source_listing_image_selection": str(
                        listing_image_selection_path.resolve()
                    ),
                    "product_pack_manifest": (
                        str(acquired.pack_manifest_path.resolve())
                        if acquired.pack_manifest_path
                        else ""
                    ),
                    "compact_evidence": str(compact_evidence_path.resolve()),
                    "image_observations": str(image_observations_path.resolve()),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n===== DIRECT RESOLUTION COMPLETE =====", flush=True)
    print(
        f"ready={final_summary[READY]}, review={final_summary[REVIEW]}, "
        f"conflict={final_summary[CONFLICT]}, missing={final_summary[MISSING]}, "
        f"business_locked={final_summary[BUSINESS_LOCKED]}",
        flush=True,
    )
    print(
        f"product_fact_calls={fact_result.model_calls}, "
        f"web_fill_calls={web_result.search_model_calls}, "
        f"inference_calls={inference_result.model_calls}, "
        f"total_calls={total_model_calls}, wall={total_elapsed:.3f}s",
        flush=True,
    )
    print(f"AI decisions: {packet_path}", flush=True)
    print(f"Run manifest: {run_manifest}", flush=True)
    print("没有打开或修改 Makro；没有填写字段；没有 Save；没有 Send to QC。", flush=True)
    return 0


__all__ = [
    "PRODUCT_PACK_EXECUTION_MODEL",
    "SUPPLIER_EXECUTION_MODEL",
    "cache_namespace",
    "dashscope_web_provider",
    "decision_summary",
    "empty_web_result",
    "provider_config",
    "run_resolver",
    "search_requests",
    "set_progress",
]
