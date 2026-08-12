from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.browser_session import cdp_endpoint, is_cdp_ready, select_listing_page
from app.makro.network_schema_probe import assert_safe_makro_listing_url
from app.makro.schema_api_harvest import (
    CATEGORY_TREE_PATH,
    STEP1_URL,
    build_registry,
    build_vertical_registry_entry,
    chunked,
    extract_vertical_catalog,
    fetch_many_json,
    fetch_partitioned_endpoint,
    sanitize_schema_payload,
    variant_definition_path,
    vertical_definition_path,
    vertical_definition_v2_path,
    write_registry,
    MakroSchemaApiHarvestError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "只读批量获取 Makro complete category tree + verticalDefinition/V2 + variant definition，"
            "构建全 Vertical schema registry。原 listing 标签页完全不动。"
        )
    )
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--variant-concurrency", type=int, default=4)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="仅采集排序后的前 N 个 Vertical；0=全量。首次建议 --limit 8。",
    )
    parser.add_argument(
        "--no-variants",
        action="store_true",
        help="跳过 fetch-variant-definition；仅用于诊断。",
    )
    parser.add_argument(
        "--output-dir",
        default="logs/makro-schema-api-harvest",
    )
    return parser


def _validate_args(args) -> None:
    if args.batch_size <= 0 or args.batch_size > 50:
        raise SystemExit("--batch-size 必须在 1..50。")
    if args.variant_concurrency <= 0 or args.variant_concurrency > 8:
        raise SystemExit("--variant-concurrency 必须在 1..8。")
    if args.limit < 0:
        raise SystemExit("--limit 不能为负数。")


def _capture_category_tree(page):
    with page.expect_response(
        lambda response: CATEGORY_TREE_PATH in response.url,
        timeout=45_000,
    ) as response_info:
        page.goto(STEP1_URL, wait_until="domcontentloaded", timeout=45_000)
    response = response_info.value
    if response.status != 200:
        raise MakroSchemaApiHarvestError(
            f"complete category tree HTTP {response.status}"
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise MakroSchemaApiHarvestError(
            f"complete category tree 不是 JSON: {type(exc).__name__}: {exc}"
        ) from exc
    return sanitize_schema_payload(payload)


def _fetch_variants(page, verticals, *, concurrency: int):
    by_vertical = {}
    failures = []
    # Keep each JS round bounded; the workers inside a round provide controlled
    # concurrency without extracting cookies/auth headers from the browser.
    for batch in chunked(verticals, 40):
        paths = [variant_definition_path(vertical) for vertical in batch]
        results = fetch_many_json(
            page,
            paths,
            concurrency=concurrency,
            timeout_ms=45_000,
        )
        for vertical, result in zip(batch, results):
            if result.get("ok"):
                by_vertical[vertical] = result.get("payload")
            else:
                failures.append(
                    {
                        "endpoint": "variantDefinition",
                        "vertical": vertical,
                        "status": result.get("status"),
                        "error": result.get("json_error") or "HTTP failure",
                    }
                )
    return by_vertical, failures


def main() -> int:
    args = build_parser().parse_args()
    _validate_args(args)
    if not is_cdp_ready(args.cdp_port):
        raise MakroSchemaApiHarvestError(
            f"Makro 长期 Edge CDP 127.0.0.1:{args.cdp_port} 不可达；harvester 不会启动/重启浏览器。"
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_dir) / f"harvest-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_endpoint(args.cdp_port))
        contexts = list(browser.contexts)
        if not contexts:
            raise MakroSchemaApiHarvestError("已连接 Makro Edge，但没有 browser context。")
        context = contexts[0]
        source_page = select_listing_page(context)
        source_url = source_page.url
        assert_safe_makro_listing_url(source_url)

        harvest_page = context.new_page()
        try:
            tree_payload = _capture_category_tree(harvest_page)
            full_catalog = extract_vertical_catalog(tree_payload)
            if len(full_catalog) < 100:
                raise MakroSchemaApiHarvestError(
                    f"complete category tree 只解析出 {len(full_catalog)} 个 Vertical；"
                    "与已观测的完整树规模不符，拒绝在可能的部分数据上继续。"
                )

            selected_catalog = full_catalog[: args.limit] if args.limit else full_catalog
            selected_verticals = [item["vertical"] for item in selected_catalog]
            catalog_by_vertical = {item["vertical"]: item for item in full_catalog}

            print("===== MAKRO SCHEMA API HARVEST =====")
            print(f"catalog_verticals={len(full_catalog)}")
            print(f"selected_verticals={len(selected_verticals)}")
            print(f"batch_size={args.batch_size}")
            print(f"include_variants={not args.no_variants}")

            definitions = fetch_partitioned_endpoint(
                harvest_page,
                selected_verticals,
                path_builder=vertical_definition_path,
                batch_size=args.batch_size,
                endpoint_name="verticalDefinition",
            )
            properties = fetch_partitioned_endpoint(
                harvest_page,
                selected_verticals,
                path_builder=vertical_definition_v2_path,
                batch_size=args.batch_size,
                endpoint_name="verticalDefinitionV2",
            )

            failures = [*definitions.failures, *properties.failures]
            batch_fallbacks = [
                *definitions.batch_fallbacks,
                *properties.batch_fallbacks,
            ]

            variant_by_vertical = {}
            if not args.no_variants:
                variant_by_vertical, variant_failures = _fetch_variants(
                    harvest_page,
                    selected_verticals,
                    concurrency=args.variant_concurrency,
                )
                failures.extend(variant_failures)

            entries = []
            for vertical in selected_verticals:
                definition = definitions.by_vertical.get(vertical)
                if definition is None:
                    failures.append(
                        {
                            "endpoint": "verticalDefinition",
                            "vertical": vertical,
                            "error": "no schema payload after batch + single fallback",
                        }
                    )
                    continue
                entry = build_vertical_registry_entry(
                    vertical=vertical,
                    catalog_item=catalog_by_vertical.get(vertical),
                    definition=definition,
                    definition_v2=properties.by_vertical.get(vertical),
                    variant_definition=variant_by_vertical.get(vertical),
                )
                if entry["attribute_count"] <= 0:
                    failures.append(
                        {
                            "endpoint": "verticalDefinition",
                            "vertical": vertical,
                            "error": "schema payload contained zero attributeName definitions",
                        }
                    )
                entries.append(entry)

            registry = build_registry(
                catalog=full_catalog,
                vertical_entries=entries,
                failures=failures,
                batch_fallbacks=batch_fallbacks,
                variants_included=not args.no_variants,
            )
            registry["run"] = {
                "mode": "pilot" if args.limit else "full",
                "limit": args.limit,
                "selected_vertical_count": len(selected_verticals),
                "batch_size": args.batch_size,
                "variant_concurrency": args.variant_concurrency,
            }
            registry["safety"].update(
                {
                    "original_listing_url": source_url,
                    "harvest_page_url": harvest_page.url,
                    "category_tree_captured_from_portal_request": True,
                    "schema_requests_method": "GET",
                    "schema_requests_same_origin_only": True,
                }
            )

            registry_path = write_registry(
                registry,
                run_dir / "makro-schema-api-registry.json",
            )
            report = {
                "registry": str(registry_path.resolve()),
                "stats": registry["stats"],
                "run": registry["run"],
                "failure_count": len(failures),
                "failures": failures,
                "batch_fallback_count": len(batch_fallbacks),
                "batch_fallbacks": batch_fallbacks,
                "safety": registry["safety"],
            }
            report_path = run_dir / "harvest-report.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            stats = registry["stats"]
            print(f"harvested_verticals={stats['harvested_vertical_count']}")
            print(f"unique_attributes={stats['unique_attribute_count']}")
            print(f"attribute_occurrences={stats['attribute_occurrence_count']}")
            print(f"variant_attribute_occurrences={stats['variant_attribute_occurrence_count']}")
            print(f"field_families={stats['field_family_count']}")
            print(f"failures={len(failures)}")
            print(f"batch_fallbacks={len(batch_fallbacks)}")
            print(f"registry={registry_path.resolve()}")
            print(f"report={report_path.resolve()}")
            print("safety=original Step3 untouched; GET only; writes=0; Save=False; Send to QC=False")

            # Pilot is an acceptance gate, not a best-effort scrape. If the
            # primary field-schema endpoint did not yield attributes for every
            # selected Vertical, report the artifacts but fail the command.
            bad_primary = [
                failure for failure in failures
                if failure.get("endpoint") == "verticalDefinition"
            ]
            if bad_primary:
                raise MakroSchemaApiHarvestError(
                    f"verticalDefinition pilot/full harvest 有 {len(bad_primary)} 个主 schema 失败；"
                    f"已写诊断报告 {report_path.resolve()}"
                )
        finally:
            harvest_page.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
