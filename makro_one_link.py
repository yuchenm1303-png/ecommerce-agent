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

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness, is_cdp_ready
from app.live_schema import write_live_schema
from app.makro.direct_visual_hold import is_listing_attribute_field
from app.makro.domain import MakroDomainAdapter
from app.makro.listing import MAKRO_HOST, MAKRO_SINGLE_LISTING_ROUTE
from app.makro.listing_creation import (
    MAKRO_NEW_LISTING_URL,
    is_vertical_step,
    run_listing_creation,
)
from app.providers.registry import (
    ProviderConfig,
    ProviderConfigurationError,
    SUPPORTED_PROVIDERS,
    build_semantic_provider,
    default_api_key_env,
    validate_provider_config,
)
from app.source_capture import DEFAULT_SOURCE_CDP_PORT, SourceAccessBlocked, capture_product_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "只给一个供应商商品链接：自动完成 Makro Step 1 Vertical、Step 2 Brand，"
            "动态扫描 Step 3 schema，再复用现有 Resolver/Executor 自动填写。永不 Send to QC。"
        )
    )
    parser.add_argument("--product-url", required=True)

    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS, default="openai-compatible")
    parser.add_argument("--model", default="qwen3.7-plus")
    parser.add_argument("--fact-model", default="qwen3.7-max")
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--structured-mode", choices=("auto", "prompt_only", "json_object"), default="json_object")
    thinking = parser.add_mutually_exclusive_group()
    thinking.add_argument("--enable-thinking", dest="enable_thinking", action="store_true")
    thinking.add_argument("--disable-thinking", dest="enable_thinking", action="store_false")
    parser.set_defaults(enable_thinking=False)
    parser.add_argument("--request-timeout-seconds", type=float, default=120.0)

    parser.add_argument("--vertical", default="", help="诊断覆盖；默认由供应商资料 + Makro live candidates 自动确定。")
    parser.add_argument("--brand", default="", help="诊断覆盖；默认只从供应商资料确定，未知时拒绝编造。")

    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)

    parser.add_argument("--source-profile-dir", default="browser_profiles/source-edge")
    parser.add_argument("--source-cdp-port", type=int, default=DEFAULT_SOURCE_CDP_PORT)
    parser.add_argument("--source-wait-ms", type=int, default=1800)
    parser.add_argument("--source-scroll-wait-ms", type=int, default=180)
    parser.add_argument("--source-max-scroll-steps", type=int, default=120)
    parser.add_argument("--source-max-visible-text-chars", type=int, default=120_000)
    parser.add_argument("--source-use-current-page", action="store_true")
    parser.add_argument("--source-cache-dir", default="logs/source-cache")
    parser.add_argument("--source-cache-ttl-seconds", type=int, default=900)
    parser.add_argument("--refresh-source", action="store_true")

    parser.add_argument("--image-batch-size", type=int, default=3)
    parser.add_argument("--image-concurrency", type=int, default=4)
    parser.add_argument("--local-batch-size", type=int, default=12)
    parser.add_argument("--local-concurrency", type=int, default=4)
    parser.add_argument("--web-enrich", choices=("auto", "off"), default="auto")
    parser.add_argument("--web-search-model", default="qwen3.7-max")
    parser.add_argument("--web-base-url", default="")
    parser.add_argument("--web-batch-size", type=int, default=5)
    parser.add_argument("--web-concurrency", type=int, default=3)
    parser.add_argument("--semantic-cache-dir", default="logs/semantic-cache")
    parser.add_argument("--no-semantic-cache", action="store_true")

    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="只完成 Step 1/2 + live schema + Resolver；不执行 Step 3 浏览器写入。",
    )
    parser.add_argument(
        "--allow-section-save",
        action="store_true",
        help="真实 Step 3 执行必需安全开关；允许现有 executor 逐 section Save。",
    )
    parser.add_argument("--include-review-candidates", action="store_true")
    parser.add_argument(
        "--upload-source-photos",
        action="store_true",
        help="把 Resolver 捕获的前 5 张 source product images 交给现有 Product Photos uploader。默认关闭。",
    )
    parser.add_argument("--upload-image", action="append", default=[])
    parser.add_argument("--output-dir", default="logs/makro-one-link")
    return parser


def _provider_config(args: argparse.Namespace) -> ProviderConfig:
    api_key_env = args.api_key_env or default_api_key_env(args.provider)
    thinking = None if args.provider == "openai" else args.enable_thinking
    return validate_provider_config(
        ProviderConfig(
            provider=args.provider,
            model=args.model,
            api_key_env=api_key_env,
            base_url=args.base_url,
            structured_mode=args.structured_mode,
            request_timeout_seconds=args.request_timeout_seconds,
            enable_thinking=thinking,
        )
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


def _scan_and_write_live_schema(
    adapter: MakroDomainAdapter,
    target: Path,
    *,
    wait_ms: int,
    max_scroll_steps: int,
) -> tuple[Path, dict[str, Any]]:
    sections, controls, scan = adapter.scan_sections(
        include_values=False,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    all_fields = adapter.build_semantic_fields(controls)
    fields = [field for field in all_fields if is_listing_attribute_field(field)]
    if not fields:
        raise RuntimeError("Step 3 live schema scan returned zero listing attribute fields")
    path = write_live_schema(fields, target)
    return path, {
        "listing_attribute_fields": len(fields),
        "semantic_fields_before_filter": len(all_fields),
        "sections": [item.get("title") for item in sections],
        "scan": scan,
    }


def _append_option(command: list[str], name: str, value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if text:
        command.extend([name, text])


def _run(command: list[str], label: str) -> None:
    print(f"\n===== {label} =====", flush=True)
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")


def _single_run_dir(root: Path, prefix: str) -> Path:
    matches = sorted(path for path in root.glob(f"{prefix}*") if path.is_dir())
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {prefix}* directory under {root}, got {len(matches)}")
    return matches[0]


def _resolver_command(args: argparse.Namespace, live_schema: Path, resolver_root: Path) -> list[str]:
    script = Path(__file__).with_name("makro_resolve_ai.py")
    command = [
        sys.executable,
        str(script),
        "--provider",
        args.provider,
        "--model",
        args.model,
        "--fact-model",
        args.fact_model,
        "--structured-mode",
        args.structured_mode,
        "--request-timeout-seconds",
        str(args.request_timeout_seconds),
        "--live-schema",
        str(live_schema),
        "--product-url",
        args.product_url,
        "--source-profile-dir",
        args.source_profile_dir,
        "--source-cdp-port",
        str(args.source_cdp_port),
        "--source-wait-ms",
        str(args.source_wait_ms),
        "--source-scroll-wait-ms",
        str(args.source_scroll_wait_ms),
        "--source-max-scroll-steps",
        str(args.source_max_scroll_steps),
        "--source-max-visible-text-chars",
        str(args.source_max_visible_text_chars),
        "--source-cache-dir",
        args.source_cache_dir,
        "--source-cache-ttl-seconds",
        str(args.source_cache_ttl_seconds),
        "--image-batch-size",
        str(args.image_batch_size),
        "--image-concurrency",
        str(args.image_concurrency),
        "--local-batch-size",
        str(args.local_batch_size),
        "--local-concurrency",
        str(args.local_concurrency),
        "--web-enrich",
        args.web_enrich,
        "--web-search-model",
        args.web_search_model,
        "--web-batch-size",
        str(args.web_batch_size),
        "--web-concurrency",
        str(args.web_concurrency),
        "--semantic-cache-dir",
        args.semantic_cache_dir,
        "--output-dir",
        str(resolver_root),
    ]
    _append_option(command, "--api-key-env", args.api_key_env)
    _append_option(command, "--base-url", args.base_url)
    _append_option(command, "--web-base-url", args.web_base_url)
    if args.provider == "openai-compatible":
        command.append("--enable-thinking" if args.enable_thinking else "--disable-thinking")
    if args.no_semantic_cache:
        command.append("--no-semantic-cache")
    return command


def _executor_command(
    args: argparse.Namespace,
    *,
    live_schema: Path,
    creation_vertical: str,
    resolver_manifest: dict[str, Any],
    executor_root: Path,
) -> list[str]:
    outputs = resolver_manifest.get("outputs") or {}
    decision_packet = str(outputs.get("final_decisions") or "")
    snapshot = str(outputs.get("primary_source_snapshot") or "")
    product_images = [str(item) for item in outputs.get("primary_source_product_images") or [] if str(item)]
    screenshot = str(outputs.get("primary_source_screenshot") or "")
    evidence_images = product_images or ([screenshot] if screenshot else [])
    if not decision_packet or not snapshot or not evidence_images:
        raise RuntimeError("Resolver manifest is missing final decisions / source snapshot / evidence images")

    product_url = str(resolver_manifest.get("primary_product_url") or args.product_url)
    script = Path(__file__).with_name("makro_execute_listing.py")
    command = [
        sys.executable,
        str(script),
        "--decision-packet",
        decision_packet,
        "--live-schema",
        str(live_schema),
        "--product-url",
        product_url,
        "--supplier-snapshot",
        snapshot,
        "--expected-vertical",
        creation_vertical,
        "--all-step3",
        "--allow-section-save",
        "--profile-dir",
        args.profile_dir,
        "--cdp-port",
        str(args.cdp_port),
        "--scroll-wait-ms",
        str(args.scroll_wait_ms),
        "--max-scroll-steps",
        str(args.max_scroll_steps),
        "--output-dir",
        str(executor_root),
    ]
    for image in evidence_images:
        command.extend(["--image", image])
    uploads = list(args.upload_image)
    if args.upload_source_photos:
        uploads.extend(product_images[:5])
    seen: set[str] = set()
    for image in uploads:
        normalized = os.path.normcase(os.path.abspath(str(image)))
        if normalized in seen:
            continue
        seen.add(normalized)
        command.extend(["--upload-image", str(image)])
    if args.include_review_candidates:
        command.append("--include-review-candidates")
    return command


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
