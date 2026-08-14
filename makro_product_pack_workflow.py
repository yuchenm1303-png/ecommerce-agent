"""Full Makro preparation workflow for customer-supplied product packs.

This is an input adapter, not a second listing engine. It reuses the canonical
Step 1/2 state machine, live-schema scanner, Resolver, Fill Plan and owned-tab
contract from the existing GUI workflow. The only difference is how product
evidence is acquired before those shared stages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.browser_session import EdgeHarness, is_cdp_ready
from app.makro.domain import MakroDomainAdapter
from app.product_input import acquire_product_input, product_input_manifest_payload
from app.providers.registry import ProviderConfigurationError, build_semantic_provider
from app.source_capture import SourceAccessBlocked
from makro_gui_workflow import (
    _advance_listing_to_step3,
    _create_fresh_owned_page,
    _phase,
    _plan_command,
    _write_manifest,
)
from makro_one_link import (
    _provider_config,
    _resolver_command,
    _run,
    _scan_and_write_live_schema,
    _single_run_dir,
    build_parser as build_one_link_parser,
)
from app.makro.listing_creation import infer_listing_bootstrap


def build_parser():
    parser = build_one_link_parser()
    parser.description = "GUI full listing preparation from customer documents/tables/images."
    # The inherited one-link parser historically required --product-url. Product
    # packs use a generated local product reference instead, set after intake.
    for action in parser._actions:
        if getattr(action, "dest", "") == "product_url":
            action.required = False
            action.default = ""
            break
    parser.add_argument("--mode", choices=("full",), default="full")
    parser.add_argument(
        "--product-file",
        action="append",
        default=[],
        help="Customer product document/table/image. Repeat for multiple files.",
    )
    return parser


def _resolver_pair_for_pack(
    args: Any,
    *,
    run_dir: Path,
    live_schema: Path,
    pack_manifest: Path,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    cold_root = run_dir / "02-cold-resolver"
    hot_root = run_dir / "03-hot-resolver"
    cold_root.mkdir(parents=True, exist_ok=True)
    hot_root.mkdir(parents=True, exist_ok=True)

    cold_command = _resolver_command(args, live_schema, cold_root)
    cold_command.extend(["--product-pack-manifest", str(pack_manifest)])
    _run(cold_command, "STEP 3 CURRENT RESOLVER · COLD")
    cold_run = _single_run_dir(cold_root, "resolve-ai-")
    cold_manifest_path = cold_run / "run-manifest.json"
    cold_manifest = json.loads(cold_manifest_path.read_text(encoding="utf-8"))

    hot_command = _resolver_command(args, live_schema, hot_root)
    hot_command.extend(["--product-pack-manifest", str(pack_manifest)])
    _run(hot_command, "STEP 3 CURRENT RESOLVER · HOT/CACHE")
    hot_run = _single_run_dir(hot_root, "resolve-ai-")
    hot_manifest_path = hot_run / "run-manifest.json"
    hot_manifest = json.loads(hot_manifest_path.read_text(encoding="utf-8"))
    return cold_manifest_path, cold_manifest, hot_manifest_path, hot_manifest


def _prepare_step3_pack(
    args: Any,
    *,
    run_dir: Path,
    page: Any,
    manifest: dict[str, Any],
    pack_manifest: Path,
) -> None:
    from makro_gui_workflow import _target_values

    vertical, brand = _target_values(page)
    adapter = MakroDomainAdapter(page)
    adapter.assert_expected_vertical(vertical)

    schema_root = run_dir / "01-live-schema" / "live-scan-current"
    schema_root.mkdir(parents=True, exist_ok=True)
    live_schema, scan_info = _scan_and_write_live_schema(
        adapter,
        schema_root / "live-schema.json",
        wait_ms=args.scroll_wait_ms,
        max_scroll_steps=args.max_scroll_steps,
    )
    manifest.update(
        {
            "vertical": vertical,
            "brand": brand,
            "live_schema": str(live_schema.resolve()),
            "live_schema_scan": scan_info,
        }
    )

    cold_path, _cold_manifest, hot_path, hot_manifest = _resolver_pair_for_pack(
        args,
        run_dir=run_dir,
        live_schema=live_schema,
        pack_manifest=pack_manifest,
    )
    manifest["cold_resolver_manifest"] = str(cold_path.resolve())
    manifest["resolver_manifest"] = str(hot_path.resolve())
    manifest["resolver_summary"] = hot_manifest.get("final_decision_summary")

    plan_root = run_dir / "04-fill-plan"
    plan_root.mkdir(parents=True, exist_ok=True)
    _run(
        _plan_command(
            args,
            live_schema=live_schema,
            vertical=vertical,
            resolver_manifest=hot_manifest,
            output_root=plan_root,
            makro_target_id=str(manifest.get("makro_target_id") or ""),
        ),
        "STEP 3 CURRENT READ-ONLY FILL PLAN",
    )
    plan_run = _single_run_dir(plan_root, "plan-")
    fill_plan = plan_run / "fill-plan.json"
    fill_plan_manifest = plan_run / "manifest.json"
    plan_payload = json.loads(fill_plan.read_text(encoding="utf-8"))
    manifest["fill_plan"] = str(fill_plan.resolve())
    manifest["fill_plan_manifest"] = str(fill_plan_manifest.resolve())
    manifest["fill_plan_summary"] = plan_payload.get("summary") or {}


def main() -> int:
    args = build_parser().parse_args()
    if not args.product_file:
        raise SystemExit("客户资料包模式至少需要一个 --product-file。")
    if args.allow_section_save:
        raise SystemExit("GUI staged acceptance does not accept --allow-section-save")
    if args.upload_source_photos or args.upload_image:
        raise SystemExit("GUI staged acceptance does not upload listing photos; use explicit real execution")
    if args.vertical or args.brand:
        raise SystemExit("GUI normal workflow does not accept diagnostic --vertical/--brand overrides")
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
    manifest: dict[str, Any] = {
        "mode": "full",
        "input_mode": "customer_product_pack",
        "status": "started",
        "product_url": "",
        "vertical": "",
        "brand": "",
        "makro_target_id": "",
        "ownership_mode": "",
        "writes_performed": 0,
        "save_clicked": False,
        "send_to_qc_clicked": False,
        "browser_closed": False,
    }
    _write_manifest(manifest_path, manifest)

    current = "source"
    page: Any | None = None

    def set_current_phase(name: str) -> None:
        nonlocal current
        current = name

    try:
        current = "source"
        _phase("source", "START", "customer product pack intake")
        acquired = acquire_product_input(
            output_dir=run_dir / "bootstrap-source",
            product_files=args.product_file,
        )
        if acquired.pack_manifest_path is None:
            raise RuntimeError("product pack intake did not produce a manifest")
        args.product_url = acquired.product_reference_url
        hints = infer_listing_bootstrap(
            provider,
            acquired.snapshot,
            image_paths=acquired.evidence_image_paths,
        )
        manifest["product_url"] = acquired.product_reference_url
        manifest["product_input"] = product_input_manifest_payload(acquired)
        manifest["bootstrap_source"] = {
            "snapshot": str(acquired.snapshot_path.resolve()),
            "screenshot": "",
            "product_images": [str(path.resolve()) for path in acquired.evidence_image_paths],
            "listing_images": [str(path.resolve()) for path in acquired.listing_image_paths],
            "cache_hit": False,
        }
        manifest["listing_hints"] = hints.as_dict()
        manifest["status"] = "source_complete"
        _write_manifest(manifest_path, manifest)
        _phase(
            "source",
            "COMPLETE",
            f"customer evidence ready files={len(acquired.customer_snapshot_paths)} images={len(acquired.evidence_image_paths)}",
        )

        with sync_playwright() as playwright:
            harness = EdgeHarness(
                playwright,
                profile_dir=Path(args.profile_dir).resolve(),
                port=args.cdp_port,
                start_url="https://seller.makro.co.za/",
            )
            if harness.launched_now:
                raise RuntimeError("Makro Edge unexpectedly entered launch path; aborted")

            page, owned_target_id = _create_fresh_owned_page(harness)
            manifest["makro_target_id"] = owned_target_id
            manifest["ownership_mode"] = "fresh_dedicated_tab"
            manifest["owned_page_start_url"] = str(page.url or "")
            _write_manifest(manifest_path, manifest)

            from app.makro.step1_entry import prepare_owned_step1_page

            def prepare_step1():
                assert page is not None
                prepare_owned_step1_page(page)
                return page

            page, _vertical, _brand = _advance_listing_to_step3(
                page=page,
                prepare_step1=prepare_step1,
                provider=provider,
                hints=hints,
                manifest=manifest,
                manifest_path=manifest_path,
                allow_initial_later_stage=False,
                set_current=set_current_phase,
            )
            harness.page = page

            current = "step3"
            _phase("step3", "START")
            _prepare_step3_pack(
                args,
                run_dir=run_dir,
                page=page,
                manifest=manifest,
                pack_manifest=acquired.pack_manifest_path,
            )
            harness.detach()
            manifest["status"] = "prepare_complete"
            _write_manifest(manifest_path, manifest)
            _phase("step3", "COMPLETE", "current Resolver + Fill Plan")

        print(
            "GUI WORKFLOW COMPLETE mode=full input=customer_product_pack writes=0 save=False send_to_qc=False",
            flush=True,
        )
        return 0
    except SourceAccessBlocked as exc:
        manifest["status"] = "source_access_blocked"
        manifest["failed_phase"] = current
        manifest["error"] = str(exc)
        _write_manifest(manifest_path, manifest)
        _phase(current, "FAILED", str(exc))
        return 2
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failed_phase"] = current
        try:
            failed_page_url = str(getattr(page, "url", "") or "").strip()
        except Exception:
            failed_page_url = ""
        if failed_page_url:
            manifest["failed_page_url"] = failed_page_url
        manifest["error"] = str(exc)
        _write_manifest(manifest_path, manifest)
        _phase(current, "FAILED", str(exc))
        print(f"GUI WORKFLOW FAILED: {exc}", flush=True)
        print("现场保留；不会 Send to QC，也不会关闭/重启长期 Makro Edge。", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
