"""Prepare one Batch listing with one exclusive Makro browser transport phase.

Source/Product Identity and Resolver work remain independently parallel across
jobs. Only real Makro browser control is serialized through the shared CDP
transport lane: Step 1, Step 2 and the Step 3 schema capture run on the owned tab,
then the Playwright transport is released before Resolver/Fill Plan computation.

No Step 3 writes, Save, image upload, or Send to QC happen here.
"""

from __future__ import annotations

import traceback
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.batch_step3_prepare import (
    capture_batch_step3_schema,
    complete_batch_step3_from_schema,
)
from app.browser_page_owner import page_target_id
from app.browser_session import EdgeHarness, is_cdp_ready
from app.cdp_automation_health import (
    looks_like_cdp_transport_failure,
    mark_cdp_poisoned,
    poison_matches_current_generation,
)
from app.cdp_transport_lane import exclusive_cdp_transport_lane
from app.listing_content_policy import current_listing_intent
from app.makro.listing_creation import MAKRO_NEW_LISTING_URL, infer_listing_bootstrap
from app.makro.step1_entry import prepare_owned_step1_page
from app.product_input import acquire_product_input, product_input_manifest_payload
from app.providers.registry import ProviderConfigurationError, build_semantic_provider
from app.source_capture import SourceAccessBlocked
from app.workflow_cli import build_staged_workflow_parser, provider_config
from makro_gui_workflow import (
    _advance_listing_to_step3,
    _listing_stage,
    _phase,
    _write_manifest,
)

_STEP1_TRANSIENT_ATTEMPTS = 3
_STEP1_TRANSIENT_BACKOFF_MS = 750


def _args():
    parser = build_staged_workflow_parser()
    parser.add_argument(
        "--product-file",
        action="append",
        default=[],
        help="Customer supplemental product file for this Batch job. Repeat as needed.",
    )
    parser.add_argument(
        "--listing-intent",
        default="",
        help=(
            "Customer-entered intent for this exact Batch row. It is independent semantic context "
            "for Product Identity/Vertical reconciliation, not permission to contradict supplier facts."
        ),
    )
    return parser.parse_args()


def _prepare_owned_step1_with_recovery(page):
    """Retry only transient Playwright navigation timeouts on the owned tab."""

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
        provider = build_semantic_provider(provider_config(args))
    except ProviderConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    run_dir = Path(args.output_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "run-manifest.json"
    listing_intent = " ".join(str(args.listing_intent or current_listing_intent() or "").split())[:2000]
    manifest: dict[str, object] = {
        "mode": "full",
        "input_mode": "supplier_url",
        "batch_owned_tab": True,
        "status": "started",
        "product_url": args.product_url,
        "supplemental_product_files": len(args.product_file),
        "listing_intent_present": bool(listing_intent),
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
            listing_intent=listing_intent,
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
        if listing_intent:
            detail += " + listing intent"
        _phase("source", "COMPLETE", detail)

        current = "step1"
        live_schema: Path | None = None
        vertical = ""
        page_url = ""

        with exclusive_cdp_transport_lane(args.cdp_port):
            # One worker may discover that the current browser generation is
            # poisoned while sibling workers are already queued for the lane.
            # They must not repeat the same 25s x 3 attach against that generation.
            if poison_matches_current_generation(args.cdp_port):
                raise RuntimeError(
                    "Makro Browser automation generation 已标记失效；"
                    "该 Batch worker 不再尝试 attach，等待 GUI 在 Batch 空闲边界安全恢复。"
                )

            with sync_playwright() as playwright:
                harness: EdgeHarness | None = None
                try:
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
                    owned_target_id = page_target_id(page)
                    manifest["makro_target_id"] = owned_target_id

                    current = "step3"
                    _phase("step3", "START")
                    live_schema, vertical, _brand, page_url = capture_batch_step3_schema(
                        args,
                        run_dir=run_dir,
                        page=page,
                        manifest=manifest,
                    )
                    manifest["status"] = "step3_schema_captured"
                    _write_manifest(manifest_path, manifest)
                finally:
                    if harness is not None:
                        harness.detach()

        if live_schema is None or not vertical:
            raise RuntimeError("Batch Step 3 browser phase did not produce a captured live schema")

        print(
            "BATCH_CDP_BROWSER_PHASE COMPLETE "
            f"port={args.cdp_port} target={manifest.get('makro_target_id', '')} "
            "transport_released_before_resolver=True",
            flush=True,
        )

        complete_batch_step3_from_schema(
            args,
            run_dir=run_dir,
            live_schema=live_schema,
            vertical=vertical,
            manifest=manifest,
            pack_manifest=acquired.pack_manifest_path,
        )
        manifest["page_url"] = page_url
        manifest["status"] = "prepare_complete"
        _write_manifest(manifest_path, manifest)
        _phase("step3", "COMPLETE", "captured schema + current Resolver + Fill Plan")

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
        traceback.print_exc()
        return 2
    except Exception as exc:
        if looks_like_cdp_transport_failure(exc):
            mark_cdp_poisoned(args.cdp_port, reason=str(exc))
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        _write_manifest(manifest_path, manifest)
        _phase(current, "FAILED", str(exc))
        traceback.print_exc()
        print(f"BATCH JOB FAILED: {exc}", flush=True)
        print("现场保留；不会 Send to QC，也不会关闭/重启长期 Makro Edge。", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
