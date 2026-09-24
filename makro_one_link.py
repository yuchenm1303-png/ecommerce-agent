"""Run the Makro single-listing workflow from one supplier product URL.

Order of work:
1) capture the exact supplier page;
2) AI resolves a grounded physical Product Identity, then derives category-search hints;
3) automate Makro Step 1 (Vertical) and Step 2 (Brand) through the same canonical
   selectors and Step 2 -> Step 3 ownership transition used by GUI/Batch;
4) scan the just-created Step 3 live schema;
5) run the existing one-link Resolver against that exact schema;
6) optionally run the existing production Step 3 executor.

The command never clicks Send to QC and never closes/restarts the long-lived
Makro Edge session.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from playwright.sync_api import sync_playwright

from app.browser_session import EdgeHarness, is_cdp_ready
from app.makro.domain import MakroDomainAdapter
from app.makro.listing import parse_makro_listing_url
from app.makro.listing_creation import (
    MAKRO_NEW_LISTING_URL,
    ListingCreationResult,
    infer_listing_bootstrap,
)
from app.makro.requested_vertical import requested_vertical_scope
from app.makro.step1_entry import prepare_single_step1_page
from app.makro.step3_transition import dismiss_joyride_overlay, select_brand_to_product_info
from app.makro.vertical_selection import select_vertical
from app.providers.registry import ProviderConfigurationError, build_semantic_provider
from app.providers.usage_telemetry import ensure_usage_journal
from app.source_capture import SourceAccessBlocked, capture_product_source
from app.source_snapshot import SourceSnapshot
from app.workflow_cli import build_one_link_parser, provider_config as _provider_config
from app.workflow_runtime import (
    build_executor_command,
    build_resolver_command,
    run_command as _run,
    scan_and_write_live_schema as _scan_and_write_live_schema,
    single_run_dir as _single_run_dir,
)


# Compatibility names retained for existing callers/tests. Shared parser/config
# and mechanical runtime ownership now live under app/ rather than this CLI.
build_parser = build_one_link_parser


def _resolver_command(args: Any, live_schema: Path, resolver_root: Path) -> list[str]:
    return build_resolver_command(
        args,
        live_schema,
        resolver_root,
        script_path=Path(__file__).with_name("makro_resolve_ai.py"),
    )


def _executor_command(
    args: Any,
    *,
    live_schema: Path,
    creation_vertical: str,
    resolver_manifest: dict[str, Any],
    executor_root: Path,
) -> list[str]:
    return build_executor_command(
        args,
        live_schema=live_schema,
        creation_vertical=creation_vertical,
        resolver_manifest=resolver_manifest,
        executor_root=executor_root,
        script_path=Path(__file__).with_name("makro_execute_listing.py"),
    )


def _prepare_step1_page(harness: EdgeHarness):
    """Compatibility wrapper around the shared Single/Batch Step 1 entry gate."""

    return prepare_single_step1_page(harness)


def _run_canonical_listing_creation(
    page: Any,
    provider: Any,
    snapshot: SourceSnapshot,
    *,
    image_paths: Iterable[str | Path] = (),
    vertical_override: str = "",
    brand_override: str = "",
) -> tuple[ListingCreationResult, Any]:
    """Create one draft through the exact same Step 1/2 contracts as GUI/Batch.

    Product Identity remains the shared bootstrap truth source. A diagnostic
    Vertical override is bound context-locally into the existing strict requested-
    Vertical contract; a diagnostic Brand override is forwarded to the same native
    Check Brand flow. Step 2 -> Step 3 may replace the Playwright Page, so callers
    receive the exact owned Step 3 page rather than assuming the original target
    survived.
    """

    hints = infer_listing_bootstrap(
        provider,
        snapshot,
        image_paths=image_paths,
    )
    dismiss_joyride_overlay(page)
    if str(vertical_override or "").strip():
        with requested_vertical_scope(vertical_override):
            vertical = select_vertical(page, provider, hints)
    else:
        vertical = select_vertical(page, provider, hints)

    brand, step3_page = select_brand_to_product_info(
        page,
        provider,
        hints,
        diagnostic_brand_override=brand_override,
    )
    target = parse_makro_listing_url(str(step3_page.url or ""))
    actual_vertical = str(target.vertical or "").strip()
    actual_brand = str(target.brand or "").strip()
    if not actual_vertical:
        raise RuntimeError("Makro Step 3 page has no canonical Vertical; refusing to continue")
    if not actual_brand:
        raise RuntimeError("Makro Step 3 page has no canonical Brand; refusing to continue")
    if str(vertical or "").strip() and actual_vertical.casefold() != str(vertical).strip().casefold():
        raise RuntimeError(
            "Makro Step 3 Vertical changed after canonical Step 1 selection: "
            f"selected={vertical!r}, actual={actual_vertical!r}"
        )
    if str(brand or "").strip() and actual_brand.casefold() != str(brand).strip().casefold():
        raise RuntimeError(
            "Makro Step 3 Brand changed after canonical Step 2 selection: "
            f"selected={brand!r}, actual={actual_brand!r}"
        )

    return (
        ListingCreationResult(
            vertical=actual_vertical,
            brand=actual_brand,
            page_url=str(step3_page.url or ""),
            hints=hints,
        ),
        step3_page,
    )


def main() -> int:
    args = build_parser().parse_args()
    if not args.prepare_only and not args.allow_section_save:
        raise SystemExit(
            "完整 Step 1→2→3 会真实 Save Step 3；请明确加 --allow-section-save，"
            "或用 --prepare-only 只跑到 Resolver。"
        )
    if args.source_cache_ttl_seconds <= 0:
        raise SystemExit("one-link 串联依赖 source byte cache 复用同一原始来源；TTL 必须 > 0。")
    if not is_cdp_ready(args.cdp_port):
        raise SystemExit(
            f"长期 Makro Edge CDP 127.0.0.1:{args.cdp_port} 不可达；不会自动启动/重启/关闭 Edge。"
        )

    try:
        provider_config = _provider_config(args)
        provider = build_semantic_provider(provider_config)
    except ProviderConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = Path(args.output_dir) / f"one-link-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    ensure_usage_journal(run_dir)
    manifest_path = run_dir / "run-manifest.json"
    manifest: dict[str, Any] = {
        "mode": "one_supplier_url_makro_step1_step2_step3",
        "product_url": args.product_url,
        "status": "started",
        "send_to_qc_clicked": False,
        "browser_closed": False,
    }

    print("===== ONE-LINK MAKRO LISTING =====", flush=True)
    print(f"product_url={args.product_url}", flush=True)
    print("Makro Edge 只连接现有 9222 会话；不会重启、关闭，也不会 Send to QC。", flush=True)

    try:
        print("\n===== SOURCE BOOTSTRAP CAPTURE =====", flush=True)
        captured = capture_product_source(
            args.product_url,
            output_dir=run_dir / "bootstrap-source",
            profile_dir=args.source_profile_dir,
            cdp_port=args.source_cdp_port,
            initial_wait_ms=args.source_wait_ms,
            scroll_wait_ms=args.source_scroll_wait_ms,
            max_scroll_steps=args.source_max_scroll_steps,
            max_visible_text_chars=args.source_max_visible_text_chars,
            use_current_page=args.source_use_current_page,
            cache_dir=args.source_cache_dir,
            cache_ttl_seconds=args.source_cache_ttl_seconds,
            force_refresh=args.refresh_source,
        )
        manifest["bootstrap_source"] = {
            "snapshot": str(captured.snapshot_path.resolve()),
            "screenshot": str(captured.screenshot_path.resolve()),
            "product_images": [str(path.resolve()) for path in captured.product_image_paths],
            "cache_hit": captured.cache_hit,
        }

        with sync_playwright() as playwright:
            harness = EdgeHarness(
                playwright,
                profile_dir=Path(args.profile_dir).resolve(),
                port=args.cdp_port,
                start_url=MAKRO_NEW_LISTING_URL,
            )
            if harness.launched_now:
                raise RuntimeError("Makro Edge unexpectedly entered launch path; aborted")
            page = prepare_single_step1_page(harness)
            creation, page = _run_canonical_listing_creation(
                page,
                provider,
                captured.snapshot,
                image_paths=captured.product_image_paths,
                vertical_override=args.vertical,
                brand_override=args.brand,
            )
            print(f"Step 1 vertical={creation.vertical}", flush=True)
            print(f"Step 2 brand={creation.brand}", flush=True)
            print(f"Step 3 page={creation.page_url}", flush=True)

            adapter = MakroDomainAdapter(page)
            adapter.assert_expected_vertical(creation.vertical)
            live_schema, scan_info = _scan_and_write_live_schema(
                adapter,
                run_dir / "live-schema.json",
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
            )
            manifest["listing_creation"] = creation.as_dict()
            manifest["live_schema"] = str(live_schema.resolve())
            manifest["live_schema_scan"] = scan_info
            manifest["status"] = "step1_step2_live_schema_complete"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            harness.detach()

        resolver_root = run_dir / "resolver"
        resolver_root.mkdir(parents=True, exist_ok=True)
        _run(_resolver_command(args, live_schema, resolver_root), "STEP 3 PRODUCT RESOLUTION")
        resolver_run = _single_run_dir(resolver_root, "resolve-ai-")
        resolver_manifest_path = resolver_run / "run-manifest.json"
        resolver_manifest = json.loads(resolver_manifest_path.read_text(encoding="utf-8"))
        manifest["resolver_manifest"] = str(resolver_manifest_path.resolve())
        manifest["resolver_summary"] = resolver_manifest.get("final_decision_summary")
        manifest["status"] = "resolver_complete"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        if args.prepare_only:
            print("\nPREPARE-ONLY COMPLETE：Step 1/2 已自动完成，Step 3 live schema + Resolver 已生成。", flush=True)
            print("没有执行 Step 3 字段写入/Save；没有 Send to QC。", flush=True)
            return 0

        executor_root = run_dir / "executor"
        executor_root.mkdir(parents=True, exist_ok=True)
        _run(
            _executor_command(
                args,
                live_schema=live_schema,
                creation_vertical=creation.vertical,
                resolver_manifest=resolver_manifest,
                executor_root=executor_root,
            ),
            "STEP 3 BROWSER EXECUTION",
        )
        manifest["executor_root"] = str(executor_root.resolve())
        manifest["status"] = "complete"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        print("\n===== ONE-LINK COMPLETE =====", flush=True)
        print(f"vertical={creation.vertical} | brand={creation.brand}", flush=True)
        print(f"live_schema={live_schema.resolve()}", flush=True)
        print(f"run_manifest={manifest_path.resolve()}", flush=True)
        print("Step 1→2→3 已串联；Send to QC=false；长期 Edge 保持打开。", flush=True)
        return 0
    except SourceAccessBlocked as exc:
        manifest["status"] = "source_access_blocked"
        manifest["error"] = str(exc)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(exc), flush=True)
        print("source Edge 保持打开；人工完成合法验证后用 --source-use-current-page 重试。", flush=True)
        return 2
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nONE-LINK FAILED: {exc}", flush=True)
        print("不会 Send to QC，也不会关闭/重启长期 Makro Edge；失败现场保留。", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
