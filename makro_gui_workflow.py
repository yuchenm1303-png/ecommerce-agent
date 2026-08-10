"""GUI-facing staged Makro workflow using the current one-link backend.

This module is orchestration only. Product understanding, Step 1/2 selection,
Resolver decisions, Fill Plan gating, and Step 3 execution remain owned by the
canonical backend modules/scripts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import sync_playwright

from app.browser_session import EdgeHarness, is_cdp_ready
from app.makro.domain import MakroDomainAdapter
from app.makro.listing import parse_makro_listing_url
from app.makro.listing_creation import (
    MAKRO_NEW_LISTING_URL,
    infer_listing_bootstrap,
    is_brand_step,
    is_product_info_step,
    select_brand,
    select_vertical,
)
from app.providers.registry import (
    ProviderConfigurationError,
    build_semantic_provider,
)
from app.source_capture import SourceAccessBlocked, capture_product_source
from makro_one_link import (
    _prepare_step1_page,
    _provider_config,
    _resolver_command,
    _run,
    _scan_and_write_live_schema,
    _single_run_dir,
    build_parser as build_one_link_parser,
)

WORKFLOW_MODES = ("step1", "step2", "step3", "full")
_PHASE_KEY = {
    "source": "scan",
    "step1": "cold",
    "step2": "hot",
    "step3": "plan",
}


def build_parser():
    parser = build_one_link_parser()
    parser.description = "GUI staged acceptance runner backed by the current Makro one-link implementation."
    parser.add_argument("--mode", choices=WORKFLOW_MODES, required=True)
    return parser


def _phase(name: str, state: str, detail: str = "") -> None:
    key = _PHASE_KEY[name]
    suffix = f" detail={detail}" if detail else ""
    print(f"GUI_PHASE {key} {state}{suffix}", flush=True)


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _listing_page(
    harness: EdgeHarness,
    predicate: Callable[[Any], bool],
    label: str,
):
    if harness.context is None:
        raise RuntimeError("Makro Edge context is unavailable")
    candidates = []
    for page in harness.context.pages:
        try:
            if "seller.makro.co.za" not in str(page.url or ""):
                continue
            page.set_default_timeout(15_000)
            page.wait_for_timeout(250)
            if predicate(page):
                candidates.append(page)
        except Exception:
            continue
    if len(candidates) != 1:
        raise RuntimeError(f"{label} requires exactly one matching Makro tab; found {len(candidates)}")
    return candidates[0]


def _target_values(page: Any) -> tuple[str, str]:
    try:
        target = parse_makro_listing_url(page.url)
    except ValueError as exc:
        raise RuntimeError(
            "Current Makro Step 3 URL does not expose a verifiable vertical/brand"
        ) from exc
    vertical = str(target.vertical or "").strip()
    brand = str(target.brand or "").strip()
    if not vertical:
        raise RuntimeError("Current Makro Step 3 URL does not contain a vertical")
    return vertical, brand


def _plan_command(
    args: Any,
    *,
    live_schema: Path,
    vertical: str,
    resolver_manifest: dict[str, Any],
    output_root: Path,
) -> list[str]:
    outputs = resolver_manifest.get("outputs") or {}
    decision_packet = str(outputs.get("final_decisions") or "").strip()
    snapshot = str(outputs.get("primary_source_snapshot") or "").strip()
    screenshot = str(outputs.get("primary_source_screenshot") or "").strip()
    product_images = [
        str(value).strip()
        for value in outputs.get("primary_source_product_images") or []
        if str(value).strip()
    ]
    evidence_images = product_images or ([screenshot] if screenshot else [])
    product_url = str(resolver_manifest.get("primary_product_url") or args.product_url).strip()
    if not decision_packet or not snapshot or not evidence_images:
        raise RuntimeError(
            "Current Resolver manifest is missing final decisions / source snapshot / evidence images"
        )

    command = [
        sys.executable,
        str(Path(__file__).with_name("makro_plan_listing.py")),
        "--decision-packet",
        decision_packet,
        "--live-schema",
        str(live_schema),
        "--product-url",
        product_url,
        "--supplier-snapshot",
        snapshot,
        "--expected-vertical",
        vertical,
        "--profile-dir",
        args.profile_dir,
        "--cdp-port",
        str(args.cdp_port),
        "--scroll-wait-ms",
        str(args.scroll_wait_ms),
        "--max-scroll-steps",
        str(args.max_scroll_steps),
        "--output-dir",
        str(output_root),
    ]
    for image in evidence_images:
        command.extend(["--image", image])
    return command


def _run_resolver_pair(
    args: Any,
    *,
    run_dir: Path,
    live_schema: Path,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    cold_root = run_dir / "02-cold-resolver"
    hot_root = run_dir / "03-hot-resolver"
    cold_root.mkdir(parents=True, exist_ok=True)
    hot_root.mkdir(parents=True, exist_ok=True)

    cold_command = _resolver_command(args, live_schema, cold_root)
    _run(cold_command, "STEP 3 CURRENT RESOLVER · COLD")
    cold_run = _single_run_dir(cold_root, "resolve-ai-")
    cold_manifest_path = cold_run / "run-manifest.json"
    cold_manifest = json.loads(cold_manifest_path.read_text(encoding="utf-8"))

    hot_command = _resolver_command(args, live_schema, hot_root)
    _run(hot_command, "STEP 3 CURRENT RESOLVER · HOT/CACHE")
    hot_run = _single_run_dir(hot_root, "resolve-ai-")
    hot_manifest_path = hot_run / "run-manifest.json"
    hot_manifest = json.loads(hot_manifest_path.read_text(encoding="utf-8"))
    return cold_manifest_path, cold_manifest, hot_manifest_path, hot_manifest


def _prepare_step3(
    args: Any,
    *,
    run_dir: Path,
    page: Any,
    manifest: dict[str, Any],
) -> None:
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

    cold_path, cold_manifest, hot_path, hot_manifest = _run_resolver_pair(
        args,
        run_dir=run_dir,
        live_schema=live_schema,
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
    # GUI stage workflow never performs Step 3 writes. These inherited one-link
    # switches are rejected rather than silently changing the safety contract.
    if args.allow_section_save:
        raise SystemExit("GUI staged acceptance does not accept --allow-section-save")
    if args.upload_source_photos or args.upload_image:
        raise SystemExit("GUI staged acceptance does not upload listing photos; use explicit real execution")
    if args.vertical or args.brand:
        raise SystemExit("GUI normal workflow does not accept diagnostic --vertical/--brand overrides")
    if args.source_cache_ttl_seconds <= 0:
        raise SystemExit("source cache TTL must be > 0")
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
        "mode": args.mode,
        "status": "started",
        "product_url": args.product_url,
        "vertical": "",
        "brand": "",
        "writes_performed": 0,
        "save_clicked": False,
        "send_to_qc_clicked": False,
        "browser_closed": False,
    }
    _write_manifest(manifest_path, manifest)

    active = {
        "step1": {"source", "step1"},
        "step2": {"source", "step2"},
        "step3": {"source", "step3"},
        "full": {"source", "step1", "step2", "step3"},
    }[args.mode]
    current = "source"

    try:
        for phase_name in ("source", "step1", "step2", "step3"):
            if phase_name not in active:
                _phase(phase_name, "SKIPPED", "not requested")

        current = "source"
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
            use_current_page=args.source_use_current_page,
            cache_dir=args.source_cache_dir,
            cache_ttl_seconds=args.source_cache_ttl_seconds,
            force_refresh=args.refresh_source,
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
            "cache_hit": bool(captured.cache_hit),
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
            if harness.launched_now:
                raise RuntimeError("Makro Edge unexpectedly entered launch path; aborted")

            if args.mode == "full":
                current = "step1"
                _phase("step1", "START")
                page = _prepare_step1_page(harness)
                vertical = select_vertical(page, provider, hints)
                manifest["vertical"] = vertical
                manifest["page_url"] = page.url
                manifest["status"] = "step1_complete"
                _write_manifest(manifest_path, manifest)
                _phase("step1", "COMPLETE", vertical)

                current = "step2"
                _phase("step2", "START")
                brand = select_brand(page, provider, hints)
                if not is_product_info_step(page):
                    raise RuntimeError("Makro did not reach Step 3 after Step 2")
                manifest["brand"] = brand
                manifest["page_url"] = page.url
                manifest["status"] = "step2_complete"
                _write_manifest(manifest_path, manifest)
                _phase("step2", "COMPLETE", brand)

                current = "step3"
                _phase("step3", "START")
                _prepare_step3(args, run_dir=run_dir, page=page, manifest=manifest)
                harness.detach()
                manifest["status"] = "prepare_complete"
                _write_manifest(manifest_path, manifest)
                _phase("step3", "COMPLETE", "current Resolver + Fill Plan")
            elif args.mode == "step1":
                current = "step1"
                _phase("step1", "START")
                page = _prepare_step1_page(harness)
                vertical = select_vertical(page, provider, hints)
                manifest["vertical"] = vertical
                manifest["page_url"] = page.url
                manifest["status"] = "step1_complete"
                _write_manifest(manifest_path, manifest)
                harness.detach()
                _phase("step1", "COMPLETE", vertical)
            elif args.mode == "step2":
                current = "step2"
                _phase("step2", "START")
                page = _listing_page(harness, is_brand_step, "Step 2")
                brand = select_brand(page, provider, hints)
                if not is_product_info_step(page):
                    raise RuntimeError("Makro did not reach Step 3 after Step 2")
                vertical, actual_brand = _target_values(page)
                manifest["vertical"] = vertical
                manifest["brand"] = actual_brand or brand
                manifest["page_url"] = page.url
                manifest["status"] = "step2_complete"
                _write_manifest(manifest_path, manifest)
                harness.detach()
                _phase("step2", "COMPLETE", manifest["brand"])
            else:
                current = "step3"
                _phase("step3", "START")
                page = _listing_page(harness, is_product_info_step, "Step 3")
                _prepare_step3(args, run_dir=run_dir, page=page, manifest=manifest)
                harness.detach()
                manifest["status"] = "prepare_complete"
                _write_manifest(manifest_path, manifest)
                _phase("step3", "COMPLETE", "current Resolver + Fill Plan")

        print(
            f"GUI WORKFLOW COMPLETE mode={args.mode} writes=0 save=False send_to_qc=False",
            flush=True,
        )
        return 0
    except SourceAccessBlocked as exc:
        manifest["status"] = "source_access_blocked"
        manifest["error"] = str(exc)
        _write_manifest(manifest_path, manifest)
        _phase(current, "FAILED", str(exc))
        print(str(exc), flush=True)
        print("source Edge 保持打开；人工完成合法验证后用当前页选项重试。", flush=True)
        return 2
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        _write_manifest(manifest_path, manifest)
        _phase(current, "FAILED", str(exc))
        print(f"GUI WORKFLOW FAILED: {exc}", flush=True)
        print("现场保留；不会 Send to QC，也不会关闭/重启长期 Makro Edge。", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
