"""Run the Makro single-listing workflow from one supplier product URL.

Order of work:
1) capture the exact supplier page;
2) AI resolves a grounded physical Product Identity, then derives category-search hints;
3) automate Makro Step 1 (Vertical) and Step 2 (Brand), verifying both against
   the live UI / resulting listing URL;
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
from typing import Any

from playwright.sync_api import sync_playwright

from app.browser_session import EdgeHarness, is_cdp_ready
from app.makro.domain import MakroDomainAdapter
from app.makro.listing import MAKRO_HOST, MAKRO_SINGLE_LISTING_ROUTE
from app.makro.listing_creation import (
    MAKRO_NEW_LISTING_URL,
    is_vertical_step,
    run_listing_creation,
)
from app.providers.registry import ProviderConfigurationError, build_semantic_provider
from app.providers.usage_telemetry import ensure_usage_journal
from app.source_capture import SourceAccessBlocked, capture_product_source
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


def _is_listing_url(url: str) -> bool:
    return MAKRO_HOST in str(url or "") and MAKRO_SINGLE_LISTING_ROUTE in str(url or "")


def _prepare_step1_page(harness: EdgeHarness):
    if harness.context is None:
        raise RuntimeError("Makro Edge context is unavailable")
    listing_pages = [page for page in harness.context.pages if _is_listing_url(page.url)]
    if len(listing_pages) > 1:
        raise RuntimeError(
            f"检测到 {len(listing_pages)} 个 Add a Single Listing 标签页；拒绝猜目标。请只保留一个。"
        )
    if listing_pages:
        page = listing_pages[0]
        page.set_default_timeout(15_000)
        page.wait_for_timeout(500)
        if is_vertical_step(page):
            return page
        raise RuntimeError(
            "当前唯一 Add Listing 标签页已经不是 Step 1。为避免覆盖已有 draft，程序不会自动离开它；"
            "请先处理/关闭该 draft，再从一个干净的 Add Listing 开始。"
        )

    page = harness.ensure_page()
    page.set_default_timeout(15_000)
    page.goto(MAKRO_NEW_LISTING_URL, wait_until="domcontentloaded", timeout=45_000)
    deadline_ms = 20_000
    elapsed = 0
    while elapsed < deadline_ms:
        if is_vertical_step(page):
            return page
        if page.locator('input[type="password"]').count() > 0:
            raise RuntimeError("Makro 登录状态无效；请先在长期 Edge 中人工登录，再重试。")
        page.wait_for_timeout(500)
        elapsed += 500
    raise RuntimeError("自动进入 Add a Single Listing 后没有出现 Step 1 / Select Vertical。")


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
            page = _prepare_step1_page(harness)
            creation = run_listing_creation(
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
