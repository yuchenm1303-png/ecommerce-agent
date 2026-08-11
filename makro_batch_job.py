"""Prepare one batch listing on a dedicated Makro browser tab.

This is orchestration only. It reuses the canonical source snapshot, Product
Identity, live Makro taxonomy/brand selection, Step 3 schema scan, Resolver and
Fill Plan. The only batch-specific behavior is deterministic page ownership:
each job creates one new Makro tab and records its Chromium target id.

No Step 3 writes, Save, image upload, or Send to QC happen here.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.browser_page_owner import page_target_id
from app.browser_session import EdgeHarness, is_cdp_ready
from app.makro.listing_creation import (
    MAKRO_NEW_LISTING_URL,
    infer_listing_bootstrap,
    is_vertical_step,
)
from app.makro.step3_transition import (
    dismiss_joyride_overlay,
    select_brand_to_product_info,
)
from app.makro.vertical_selection import select_vertical
from app.providers.registry import ProviderConfigurationError, build_semantic_provider
from app.source_capture import SourceAccessBlocked, capture_product_source
from makro_gui_workflow import _phase, _prepare_step3, _write_manifest, build_parser
from makro_one_link import _provider_config


def _prepare_owned_step1_page(page) -> None:
    page.set_default_timeout(15_000)
    page.goto(MAKRO_NEW_LISTING_URL, wait_until="domcontentloaded", timeout=45_000)
    elapsed = 0
    while elapsed < 20_000:
        if is_vertical_step(page):
            return
        if page.locator('input[type="password"]').count() > 0:
            raise RuntimeError("Makro 登录状态无效；请先在长期 Edge 中人工登录，再重试。")
        page.wait_for_timeout(500)
        elapsed += 500
    raise RuntimeError("Batch owned Makro tab did not reach Step 1 / Select Vertical")


def main() -> int:
    args = build_parser().parse_args()
    if args.mode != "full":
        raise SystemExit("makro_batch_job.py only accepts --mode full")
    if args.allow_section_save or args.upload_source_photos or args.upload_image:
        raise SystemExit("batch preparation is read-only; Save/images belong to explicit execution")
    if args.vertical or args.brand:
        raise SystemExit("batch normal workflow does not accept diagnostic vertical/brand overrides")
    if not is_cdp_ready(args.cdp_port):
        raise SystemExit(
            f"长期 Makro Edge CDP 127.0.0.1:{args.cdp_port} 不可达；不会自动启动/重启/关闭 Edge。"
        )

    try:
        provider = build_semantic_provider(_provider_config(args))
    except ProviderConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    run_dir = Path(args.output_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "run-manifest.json"
    manifest: dict[str, object] = {
        "mode": "full",
        "batch_owned_tab": True,
        "status": "started",
        "product_url": args.product_url,
        "vertical": "",
        "brand": "",
        "makro_target_id": "",
        "writes_performed": 0,
        "save_clicked": False,
        "send_to_qc_clicked": False,
        "browser_closed": False,
    }
    _write_manifest(manifest_path, manifest)
    current = "source"

    try:
        _phase("source", "START")
        captured = capture_product_source(
            args.product_url,
            output_dir=run_dir / "bootstrap-source",
            profile_dir=args.source_profile_dir,
            cdp_port=args.source_cdp_port,
            initial_wait_ms=args.source_wait_ms,
            scroll_wait_ms=args.source_scroll_wait_ms,
            max_scroll_steps=args.source_max_scroll_steps,
            max_visible_text_chars=args.source_max_visible_text_chars,
            use_current_page=False,
            cache_dir=args.source_cache_dir,
            cache_ttl_seconds=args.source_cache_ttl_seconds,
            force_refresh=False,
        )
        if not captured.cache_hit:
            raise RuntimeError(
                "Batch source cache miss. Refusing parallel Source Edge navigation; prefetch this job first."
            )
        hints = infer_listing_bootstrap(
            provider,
            captured.snapshot,
            image_paths=captured.product_image_paths,
        )
        manifest["bootstrap_source"] = {
            "snapshot": str(captured.snapshot_path.resolve()),
            "screenshot": str(captured.screenshot_path.resolve()),
            "product_images": [str(path.resolve()) for path in captured.product_image_paths],
            "cache_hit": True,
        }
        manifest["listing_hints"] = hints.as_dict()
        manifest["status"] = "source_complete"
        _write_manifest(manifest_path, manifest)
        _phase("source", "COMPLETE", "supplier evidence ready")

        with sync_playwright() as playwright:
            harness = EdgeHarness(
                playwright,
                profile_dir=Path(args.profile_dir).resolve(),
                port=args.cdp_port,
                start_url=MAKRO_NEW_LISTING_URL,
            )
            if harness.launched_now or harness.context is None:
                raise RuntimeError("Makro Edge unexpectedly entered launch path; aborted")

            page = harness.context.new_page()
            harness.page = page
            target_id = page_target_id(page)
            manifest["makro_target_id"] = target_id
            _write_manifest(manifest_path, manifest)

            current = "step1"
            _phase("step1", "START")
            _prepare_owned_step1_page(page)
            dismiss_joyride_overlay(page)
            vertical = select_vertical(page, provider, hints)
            manifest["vertical"] = vertical
            manifest["page_url"] = page.url
            manifest["status"] = "step1_complete"
            _write_manifest(manifest_path, manifest)
            _phase("step1", "COMPLETE", vertical)

            current = "step2"
            _phase("step2", "START")
            brand, page = select_brand_to_product_info(page, provider, hints)
            harness.page = page
            # The portal normally stays in the same tab, but if Create New
            # Listing replaces the Chromium target, ownership must follow the
            # recovered Step 3 Page rather than preserving a stale Step 1 id.
            manifest["makro_target_id"] = page_target_id(page)
            manifest["brand"] = brand
            manifest["page_url"] = page.url
            manifest["status"] = "step2_complete"
            _write_manifest(manifest_path, manifest)
            _phase("step2", "COMPLETE", brand)

            current = "step3"
            _phase("step3", "START")
            _prepare_step3(args, run_dir=run_dir, page=page, manifest=manifest)
            manifest["page_url"] = page.url
            manifest["status"] = "prepare_complete"
            _write_manifest(manifest_path, manifest)
            _phase("step3", "COMPLETE", "current Resolver + Fill Plan")
            harness.detach()

        print(
            "BATCH JOB COMPLETE writes=0 save=False send_to_qc=False "
            f"target={manifest.get('makro_target_id', '')}",
            flush=True,
        )
        return 0
    except SourceAccessBlocked as exc:
        manifest["status"] = "source_access_blocked"
        manifest["error"] = str(exc)
        _write_manifest(manifest_path, manifest)
        _phase(current, "FAILED", str(exc))
        return 2
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        _write_manifest(manifest_path, manifest)
        _phase(current, "FAILED", str(exc))
        print(f"BATCH JOB FAILED: {exc}", flush=True)
        print("现场保留；不会 Send to QC，也不会关闭/重启长期 Makro Edge。", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
