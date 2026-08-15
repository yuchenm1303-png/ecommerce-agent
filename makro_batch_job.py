"""Prepare one batch listing on a dedicated Makro browser tab.

This is orchestration only. It reuses the canonical source snapshot, Product
Identity, live Makro taxonomy/brand selection, Step 3 schema scan, Resolver and
Fill Plan. Each Batch job may also carry its own customer supplemental files;
they remain evidence for that exact supplier URL rather than becoming a second
product input.

No Step 3 writes, Save, image upload, or Send to QC happen here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.browser_page_owner import page_target_id
from app.browser_session import EdgeHarness, is_cdp_ready
from app.makro.listing_creation import MAKRO_NEW_LISTING_URL, infer_listing_bootstrap
from app.makro.step1_entry import prepare_owned_step1_page
from app.product_input import acquire_product_input, product_input_manifest_payload
from app.providers.registry import ProviderConfigurationError, build_semantic_provider
from app.source_capture import SourceAccessBlocked
from makro_gui_workflow import (
    _advance_listing_to_step3,
    _listing_stage,
    _phase,
    _prepare_step3,
    _write_manifest,
    build_parser,
)
from makro_one_link import _provider_config
from makro_product_pack_workflow import _prepare_step3_pack

_BATCH_TARGET_ENV = "MAKRO_BATCH_TARGET_ID"
_STEP1_TRANSIENT_ATTEMPTS = 3
_STEP1_TRANSIENT_BACKOFF_MS = 750


def _args():
    parser = build_parser()
    parser.add_argument(
        "--product-file",
        action="append",
        default=[],
        help="Customer supplemental product file for this Batch job. Repeat as needed.",
    )
    return parser.parse_args()


def _prepare_owned_step1_with_recovery(page):
    """Retry only transient Playwright navigation timeouts on the owned tab.

    ``prepare_owned_step1_page`` is already state-driven and idempotent: every
    invocation inspects the current Makro page before deciding the next action.
    A ``page.goto`` timeout therefore must not make the job fail immediately;
    Makro may already have reached Dashboard/Listings while Playwright was still
    waiting for DOMContentLoaded. Re-entering the state machine lets it continue
    from the page that actually exists. Unknown Step 2/3 ownership remains
    handled by the shared workflow state machine and is never navigated backward.
    """

    for attempt in range(1, _STEP1_TRANSIENT_ATTEMPTS + 1):
        try:
            prepare_owned_step1_page(page)
            return page
        except PlaywrightTimeoutError:
            stage = _listing_stage(page)
            if stage in {"step2", "step3"}:
                return page
            if attempt >= _STEP1_TRANSIENT_ATTEMPTS:
                raise
            print(
                "MAKRO_STEP1 RETRY "
                f"reason=navigation_timeout attempt={attempt}/{_STEP1_TRANSIENT_ATTEMPTS} "
                f"stage={stage} url={str(getattr(page, 'url', '') or '')}",
                flush=True,
            )
            try:
                page.wait_for_timeout(_STEP1_TRANSIENT_BACKOFF_MS)
            except Exception:
                pass
        except RuntimeError:
            # The owned tab may legitimately advance while the pre-Step1 helper
            # is waiting. Once this exact invocation moved the tab, the shared
            # state machine can reconcile Step 2/3 safely.
            if _listing_stage(page) not in {"step2", "step3"}:
                raise
            return page
    return page


def main() -> int:
    args = _args()
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
        "input_mode": "supplier_url",
        "batch_owned_tab": True,
        "status": "started",
        "product_url": args.product_url,
        "supplemental_product_files": len(args.product_file),
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

    def set_current_phase(name: str) -> None:
        nonlocal current
        current = name

    try:
        _phase("source", "START")
        acquired = acquire_product_input(
            output_dir=run_dir / "bootstrap-source",
            product_url=args.product_url,
            product_files=args.product_file,
            source_profile_dir=args.source_profile_dir,
            source_cdp_port=args.source_cdp_port,
            source_wait_ms=args.source_wait_ms,
            source_scroll_wait_ms=args.source_scroll_wait_ms,
            source_max_scroll_steps=args.source_max_scroll_steps,
            source_max_visible_text_chars=args.source_max_visible_text_chars,
            source_use_current_page=False,
            source_cache_dir=args.source_cache_dir,
            source_cache_ttl_seconds=args.source_cache_ttl_seconds,
            refresh_source=False,
        )
        if not acquired.source_cache_hit:
            raise RuntimeError(
                "Batch source cache miss. Refusing parallel Source Edge navigation; prefetch this job first."
            )
        hints = infer_listing_bootstrap(
            provider,
            acquired.snapshot,
            image_paths=acquired.evidence_image_paths,
        )
        manifest["product_input"] = product_input_manifest_payload(acquired)
        manifest["bootstrap_source"] = {
            "snapshot": str(acquired.snapshot_path.resolve()),
            "screenshot": str(acquired.screenshot_path.resolve()) if acquired.screenshot_path else "",
            "product_images": [str(path.resolve()) for path in acquired.evidence_image_paths],
            "listing_images": [str(path.resolve()) for path in acquired.listing_image_paths],
            "cache_hit": True,
            "customer_supplement": acquired.pack_manifest_path is not None,
        }
        manifest["listing_hints"] = hints.as_dict()
        manifest["status"] = "source_complete"
        _write_manifest(manifest_path, manifest)
        detail = "supplier evidence ready"
        if args.product_file:
            detail += f" + {len(args.product_file)} supplemental file(s)"
        _phase("source", "COMPLETE", detail)

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

            def prepare_owned_current_page():
                return _prepare_owned_step1_with_recovery(page)

            page, _vertical, _brand = _advance_listing_to_step3(
                page=page,
                prepare_step1=prepare_owned_current_page,
                provider=provider,
                hints=hints,
                manifest=manifest,
                manifest_path=manifest_path,
                allow_initial_later_stage=False,
                set_current=set_current_phase,
            )
            harness.page = page
            # Step 2 -> Step 3 may replace the Chromium target. Ownership must
            # follow the exact recovered Step 3 page before Resolver/planner starts.
            owned_target_id = page_target_id(page)
            manifest["makro_target_id"] = owned_target_id
            manifest["page_url"] = page.url
            _write_manifest(manifest_path, manifest)

            current = "step3"
            _phase("step3", "START")
            # Each batch job is its own OS process. Propagate this job's refreshed
            # Chromium target through the process environment so the nested
            # read-only planner binds the exact owned tab instead of applying the
            # Single-mode unique-tab rule to all concurrent Batch tabs.
            previous_target = os.environ.get(_BATCH_TARGET_ENV)
            os.environ[_BATCH_TARGET_ENV] = owned_target_id
            try:
                if acquired.pack_manifest_path is not None:
                    _prepare_step3_pack(
                        args,
                        run_dir=run_dir,
                        page=page,
                        manifest=manifest,
                        pack_manifest=acquired.pack_manifest_path,
                    )
                else:
                    _prepare_step3(args, run_dir=run_dir, page=page, manifest=manifest)
            finally:
                if previous_target is None:
                    os.environ.pop(_BATCH_TARGET_ENV, None)
                else:
                    os.environ[_BATCH_TARGET_ENV] = previous_target
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
