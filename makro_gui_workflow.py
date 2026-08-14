"""GUI-facing staged Makro workflow using the current one-link backend.

This module is orchestration only. Product understanding, Step 1/2 selection,
Resolver decisions, Fill Plan gating, and Step 3 execution remain owned by the
canonical backend modules/scripts.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import sync_playwright

from app.browser_page_owner import page_target_id
from app.browser_session import EdgeHarness, is_cdp_ready
from app.makro.domain import MakroDomainAdapter
from app.makro.listing import MAKRO_HOME_URL, parse_makro_listing_url
from app.makro.listing_creation import (
    MAKRO_NEW_LISTING_URL,
    infer_listing_bootstrap,
    is_brand_step,
    is_product_info_step,
)
from app.makro.step1_entry import prepare_owned_step1_page, prepare_single_step1_page
from app.makro.step3_transition import (
    dismiss_joyride_overlay,
    select_brand_to_product_info,
)
from app.makro.vertical_selection import select_vertical
from app.providers.registry import (
    ProviderConfigurationError,
    build_semantic_provider,
)
from app.source_capture import SourceAccessBlocked, capture_product_source
from app.workflow_diagnostics import (
    diag_current_exception,
    diag_event,
    ensure_diagnostics,
)
from makro_one_link import (
    _provider_config,
    _resolver_command,
    _run as _legacy_run,
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
    parser.add_argument(
        "--resume-current-url",
        default="",
        help=(
            "GUI immediate-retry token. Full mode may resume only the exact unique Step 2/3 "
            "page URL recorded by the immediately preceding failed run."
        ),
    )
    return parser


def _phase(name: str, state: str, detail: str = "") -> None:
    key = _PHASE_KEY[name]
    normalized = str(state or "INFO").upper()
    if normalized == "FAILED":
        diag_current_exception(name, ui_phase=key, detail=detail)
    else:
        diag_event(name, normalized, ui_phase=key, detail=detail)
    suffix = f" detail={detail}" if detail else ""
    print(f"GUI_PHASE {key} {state}{suffix}", flush=True)


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    ensure_diagnostics(
        path.parent,
        Path(sys.argv[0]).stem or "makro_gui_workflow",
        mode=str(payload.get("mode") or ""),
        input_mode=str(payload.get("input_mode") or "supplier_url"),
        product_url=str(payload.get("product_url") or ""),
    )
    diag_event(
        "manifest",
        "WRITE",
        path=str(path.resolve()),
        status=str(payload.get("status") or ""),
        vertical=str(payload.get("vertical") or ""),
        brand=str(payload.get("brand") or ""),
        makro_target_id=str(payload.get("makro_target_id") or ""),
        ownership_mode=str(payload.get("ownership_mode") or ""),
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _listing_page(
    harness: EdgeHarness,
    predicate: Callable[[Any], bool],
    label: str,
):
    if harness.context is None:
        raise RuntimeError("Makro Edge context is unavailable")
    pages = list(harness.context.pages)
    diag_event(
        "browser_page_selection",
        "START",
        label=label,
        browser_page_count=len(pages),
        page_urls=[str(getattr(item, "url", "") or "") for item in pages],
    )
    candidates = []
    inspection_errors = 0
    for page in pages:
        try:
            if "seller.makro.co.za" not in str(page.url or ""):
                continue
            page.set_default_timeout(15_000)
            page.wait_for_timeout(250)
            if predicate(page):
                candidates.append(page)
        except Exception:
            inspection_errors += 1
            continue
    diag_event(
        "browser_page_selection",
        "COMPLETE" if len(candidates) == 1 else "FAILED",
        label=label,
        matching_tabs=len(candidates),
        inspection_errors=inspection_errors,
        matching_urls=[str(getattr(item, "url", "") or "") for item in candidates],
    )
    if len(candidates) != 1:
        raise RuntimeError(f"{label} requires exactly one matching Makro tab; found {len(candidates)}")
    return candidates[0]


def _create_fresh_owned_page(harness: EdgeHarness) -> tuple[Any, str]:
    """Create one dedicated Makro tab owned by this fresh full run.

    A fresh supplier URL must never inherit whatever Step 2/3 draft happened to
    be open in the long-lived Edge. The new tab starts at the authenticated Makro
    home route, receives a Chromium target id immediately, and every later
    subprocess is required to bind back to that exact target.
    """

    if harness.context is None:
        raise RuntimeError("Makro Edge context is unavailable")
    diag_event(
        "owned_tab",
        "START",
        existing_page_count=len(harness.context.pages),
        start_url=MAKRO_HOME_URL,
    )
    page = harness.context.new_page()
    page.set_default_timeout(15_000)
    page.goto(MAKRO_HOME_URL, wait_until="commit", timeout=20_000)
    page.wait_for_timeout(250)
    target_id = page_target_id(page)
    harness.page = page
    diag_event(
        "owned_tab",
        "COMPLETE",
        page_url=str(page.url or ""),
        makro_target_id=target_id,
        browser_page_count=len(harness.context.pages),
    )
    return page, target_id


def _resume_current_page(harness: EdgeHarness, expected_url: str):
    """Return only the exact unique Step 2/3 page from the prior failed run.

    A fresh full run never calls this helper. The GUI supplies ``expected_url``
    only for an immediate retry of the same supplier URL after the preceding full
    run failed in Step 2 or Step 3. Exact URL uniqueness is the resume proof; any
    unrelated listing tabs may coexist and are never adopted.
    """

    if harness.context is None:
        raise RuntimeError("Makro Edge context is unavailable")
    wanted = str(expected_url or "").strip()
    if not wanted:
        raise RuntimeError("resume-current requires the prior failed page URL")

    diag_event(
        "resume_page",
        "START",
        expected_url=wanted,
        browser_page_count=len(harness.context.pages),
    )
    exact: list[Any] = []
    listing_urls: list[str] = []
    for page in harness.context.pages:
        url = str(getattr(page, "url", "") or "").strip()
        try:
            parse_makro_listing_url(url)
        except (ValueError, AttributeError):
            continue
        listing_urls.append(url)
        if url == wanted:
            exact.append(page)

    diag_event(
        "resume_page",
        "MATCH",
        expected_url=wanted,
        listing_tabs=len(listing_urls),
        listing_urls=listing_urls,
        exact_matches=len(exact),
    )
    if len(exact) != 1:
        raise RuntimeError(
            "The exact Step 2/3 page from the failed run is no longer uniquely present. "
            f"matching_tabs={len(exact)}. Browser/page ownership changed, so automatic resume was refused."
        )

    page = exact[0]
    page.set_default_timeout(15_000)
    page.wait_for_timeout(250)
    stage = _listing_stage(page)
    if stage not in {"step2", "step3"}:
        raise RuntimeError(
            "The exact prior page is no longer a safely operable Step 2 or Step 3 surface; "
            "automatic resume was refused."
        )
    diag_event("resume_page", "COMPLETE", page_url=str(page.url or ""), detected_stage=stage)
    return page


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
    diag_event(
        "listing_target",
        "PARSED",
        page_url=str(getattr(page, "url", "") or ""),
        vertical=vertical,
        brand=brand,
    )
    return vertical, brand


def _listing_stage(page: Any | None) -> str:
    """Return the highest verified Makro listing stage currently visible."""

    if page is None:
        return "pre_step1"
    try:
        if is_product_info_step(page):
            return "step3"
    except Exception:
        pass
    try:
        if is_brand_step(page):
            return "step2"
    except Exception:
        pass
    return "pre_step1"


def _record_listing_checkpoint(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    page: Any,
    status: str,
    vertical: str,
    brand: str = "",
) -> None:
    manifest["vertical"] = str(vertical or "").strip()
    if brand:
        manifest["brand"] = str(brand).strip()
    manifest["page_url"] = str(getattr(page, "url", "") or "")
    if str(manifest.get("ownership_mode") or "") in {
        "fresh_dedicated_tab",
        "resume_exact_page",
    }:
        # Step 2 -> Step 3 may replace the Chromium page target. Refresh the
        # ownership token at every verified checkpoint so planner/executor bind
        # the page that actually owns the current stage, never the stale origin.
        manifest["makro_target_id"] = page_target_id(page)
    manifest["status"] = status
    _write_manifest(manifest_path, manifest)
    diag_event(
        "checkpoint",
        "RECORDED",
        status=status,
        page_url=manifest.get("page_url", ""),
        vertical=manifest.get("vertical", ""),
        brand=manifest.get("brand", ""),
        makro_target_id=manifest.get("makro_target_id", ""),
    )


def _advance_listing_to_step3(
    *,
    page: Any | None,
    prepare_step1: Callable[[], Any],
    provider: Any,
    hints: Any,
    manifest: dict[str, Any],
    manifest_path: Path,
    allow_initial_later_stage: bool,
    set_current: Callable[[str], None] | None = None,
) -> tuple[Any, str, str]:
    """Reconcile the current page/progress and advance only the missing stages.

    The state machine is shared by Single and Batch. A later Step 2/3 page may be
    adopted at function entry only when the caller has independent ownership /
    same-task resume proof. Later stages reached *during* this invocation are
    safe to continue because they originate from this exact controlled flow.
    """

    def current(name: str) -> None:
        if set_current is not None:
            set_current(name)

    initial_stage = _listing_stage(page)
    diag_event(
        "state_machine",
        "INSPECT",
        initial_stage=initial_stage,
        allow_initial_later_stage=allow_initial_later_stage,
        page_url=str(getattr(page, "url", "") or "") if page is not None else "",
        makro_target_id=str(manifest.get("makro_target_id") or ""),
        ownership_mode=str(manifest.get("ownership_mode") or ""),
    )
    current("step1")
    if initial_stage in {"step2", "step3"} and not allow_initial_later_stage:
        _phase("step1", "START", f"state-machine current={initial_stage}")
        raise RuntimeError(
            "Current Makro page is already Step 2/3, but this run has no verified same-task "
            "resume/ownership proof. Refusing to adopt an unknown draft."
        )

    vertical = ""
    brand = ""

    if initial_stage in {"step2", "step3"}:
        assert page is not None
        vertical, brand = _target_values(page)
        current("step1")
        _phase("step1", "START", f"state-machine current={initial_stage}")
        _record_listing_checkpoint(
            manifest_path,
            manifest,
            page=page,
            status="step1_complete",
            vertical=vertical,
        )
        _phase("step1", "COMPLETE", f"resume vertical={vertical}")
    else:
        current("step1")
        _phase("step1", "START", "state-machine reconcile")
        diag_event("step1_prepare", "START", page_url=str(getattr(page, "url", "") or ""))
        prepared = prepare_step1()
        if prepared is not None:
            page = prepared
        if page is None:
            raise RuntimeError("Step 1 preparation returned no Makro page")
        stage_after_prepare = _listing_stage(page)
        diag_event(
            "step1_prepare",
            "COMPLETE",
            page_url=str(getattr(page, "url", "") or ""),
            detected_stage=stage_after_prepare,
        )

        if stage_after_prepare in {"step2", "step3"}:
            vertical, brand = _target_values(page)
        else:
            dismiss_joyride_overlay(page)
            diag_event("vertical_selection", "START", page_url=str(page.url or ""))
            vertical = select_vertical(page, provider, hints)
            stage_after_prepare = _listing_stage(page)
            diag_event(
                "vertical_selection",
                "COMPLETE",
                vertical=vertical,
                page_url=str(page.url or ""),
                detected_stage=stage_after_prepare,
            )
            if stage_after_prepare not in {"step2", "step3"}:
                raise RuntimeError(
                    "Makro Step 1 completed but the page did not reconcile to Step 2/3."
                )

        _record_listing_checkpoint(
            manifest_path,
            manifest,
            page=page,
            status="step1_complete",
            vertical=vertical,
        )
        _phase("step1", "COMPLETE", vertical)
        initial_stage = stage_after_prepare

    stage = _listing_stage(page)
    diag_event("state_machine", "AFTER_STEP1", detected_stage=stage, vertical=vertical, brand=brand)
    if stage == "step3":
        actual_vertical, actual_brand = _target_values(page)
        if vertical and actual_vertical.casefold() != vertical.casefold():
            raise RuntimeError(
                "Makro state-machine vertical changed unexpectedly before Step 3: "
                f"expected={vertical!r}, actual={actual_vertical!r}"
            )
        vertical = actual_vertical
        brand = actual_brand or brand
        current("step2")
        _phase("step2", "START", "state-machine current=step3")
        _record_listing_checkpoint(
            manifest_path,
            manifest,
            page=page,
            status="step2_complete",
            vertical=vertical,
            brand=brand,
        )
        _phase("step2", "COMPLETE", f"resume brand={brand}")
        return page, vertical, brand

    if stage != "step2":
        raise RuntimeError(
            f"Makro state-machine expected Step 2 after Vertical reconciliation; current={stage}"
        )

    current("step2")
    _phase("step2", "START", "state-machine reconcile")
    diag_event("brand_selection", "START", page_url=str(page.url or ""), vertical=vertical)
    selected_brand, page = select_brand_to_product_info(page, provider, hints)
    actual_vertical, actual_brand = _target_values(page)
    if vertical and actual_vertical.casefold() != vertical.casefold():
        raise RuntimeError(
            "Makro state-machine vertical changed during Step 2 transition: "
            f"expected={vertical!r}, actual={actual_vertical!r}"
        )
    vertical = actual_vertical
    brand = actual_brand or selected_brand
    stage_after_brand = _listing_stage(page)
    diag_event(
        "brand_selection",
        "COMPLETE",
        selected_brand=selected_brand,
        actual_brand=actual_brand,
        final_brand=brand,
        page_url=str(page.url or ""),
        detected_stage=stage_after_brand,
    )
    if stage_after_brand != "step3":
        raise RuntimeError("Makro Step 2 completed but Step 3 is not safely operable")
    _record_listing_checkpoint(
        manifest_path,
        manifest,
        page=page,
        status="step2_complete",
        vertical=vertical,
        brand=brand,
    )
    _phase("step2", "COMPLETE", brand)
    return page, vertical, brand


def _plan_command(
    args: Any,
    *,
    live_schema: Path,
    vertical: str,
    resolver_manifest: dict[str, Any],
    output_root: Path,
    makro_target_id: str = "",
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
    if not decision_packet or not snapshot:
        raise RuntimeError(
            "Current Resolver manifest is missing final decisions / canonical source snapshot"
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
    if str(makro_target_id or "").strip():
        command.extend(["--makro-target-id", str(makro_target_id).strip()])
    for image in evidence_images:
        command.extend(["--image", image])
    return command


def _run(command: list[str], label: str) -> None:
    print(f"\n===== {label} =====", flush=True)
    started = time.monotonic()
    diag_event(
        "subprocess",
        "START",
        label=label,
        argv=command,
        cwd=str(Path.cwd()),
    )
    try:
        result = subprocess.run(command, check=False)
    except Exception:
        diag_current_exception(
            "subprocess",
            label=label,
            elapsed_s=round(time.monotonic() - started, 3),
        )
        raise
    elapsed = round(time.monotonic() - started, 3)
    diag_event(
        "subprocess",
        "COMPLETE" if result.returncode == 0 else "FAILED",
        label=label,
        returncode=result.returncode,
        elapsed_s=elapsed,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")


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

    diag_event("cold_resolver", "START", output_root=str(cold_root), live_schema=str(live_schema))
    cold_command = _resolver_command(args, live_schema, cold_root)
    _run(cold_command, "STEP 3 CURRENT RESOLVER · COLD")
    cold_run = _single_run_dir(cold_root, "resolve-ai-")
    cold_manifest_path = cold_run / "run-manifest.json"
    cold_manifest = json.loads(cold_manifest_path.read_text(encoding="utf-8"))
    diag_event(
        "cold_resolver",
        "COMPLETE",
        manifest=str(cold_manifest_path.resolve()),
        decision_summary=cold_manifest.get("final_decision_summary") or {},
        total_model_calls=cold_manifest.get("total_model_calls"),
        wall_elapsed_seconds=cold_manifest.get("wall_elapsed_seconds"),
    )

    diag_event("hot_resolver", "START", output_root=str(hot_root), live_schema=str(live_schema))
    hot_command = _resolver_command(args, live_schema, hot_root)
    _run(hot_command, "STEP 3 CURRENT RESOLVER · HOT/CACHE")
    hot_run = _single_run_dir(hot_root, "resolve-ai-")
    hot_manifest_path = hot_run / "run-manifest.json"
    hot_manifest = json.loads(hot_manifest_path.read_text(encoding="utf-8"))
    diag_event(
        "hot_resolver",
        "COMPLETE",
        manifest=str(hot_manifest_path.resolve()),
        decision_summary=hot_manifest.get("final_decision_summary") or {},
        total_model_calls=hot_manifest.get("total_model_calls"),
        wall_elapsed_seconds=hot_manifest.get("wall_elapsed_seconds"),
    )
    return cold_manifest_path, cold_manifest, hot_manifest_path, hot_manifest


def _prepare_step3(
    args: Any,
    *,
    run_dir: Path,
    page: Any,
    manifest: dict[str, Any],
) -> None:
    vertical, brand = _target_values(page)
    diag_event(
        "step3_prepare",
        "START",
        page_url=str(page.url or ""),
        vertical=vertical,
        brand=brand,
        makro_target_id=str(manifest.get("makro_target_id") or ""),
    )
    adapter = MakroDomainAdapter(page)
    adapter.assert_expected_vertical(vertical)

    schema_root = run_dir / "01-live-schema" / "live-scan-current"
    schema_root.mkdir(parents=True, exist_ok=True)
    diag_event(
        "live_schema_scan",
        "START",
        target=str(schema_root / "live-schema.json"),
        scroll_wait_ms=args.scroll_wait_ms,
        max_scroll_steps=args.max_scroll_steps,
    )
    live_schema, scan_info = _scan_and_write_live_schema(
        adapter,
        schema_root / "live-schema.json",
        wait_ms=args.scroll_wait_ms,
        max_scroll_steps=args.max_scroll_steps,
    )
    diag_event(
        "live_schema_scan",
        "COMPLETE",
        live_schema=str(live_schema.resolve()),
        listing_attribute_fields=scan_info.get("listing_attribute_fields"),
        semantic_fields_before_filter=scan_info.get("semantic_fields_before_filter"),
        sections=scan_info.get("sections") or [],
        scan=scan_info.get("scan") or {},
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
    diag_event(
        "resolver_pair",
        "COMPLETE",
        cold_manifest=str(cold_path.resolve()),
        hot_manifest=str(hot_path.resolve()),
        cold_summary=cold_manifest.get("final_decision_summary") or {},
        hot_summary=hot_manifest.get("final_decision_summary") or {},
    )

    plan_root = run_dir / "04-fill-plan"
    plan_root.mkdir(parents=True, exist_ok=True)
    diag_event(
        "fill_plan",
        "START",
        output_root=str(plan_root),
        live_schema=str(live_schema.resolve()),
        vertical=vertical,
        makro_target_id=str(manifest.get("makro_target_id") or ""),
    )
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
    diag_event(
        "fill_plan",
        "COMPLETE",
        fill_plan=str(fill_plan.resolve()),
        fill_plan_manifest=str(fill_plan_manifest.resolve()),
        summary=manifest["fill_plan_summary"],
    )
    diag_event(
        "step3_prepare",
        "COMPLETE",
        page_url=str(page.url or ""),
        vertical=vertical,
        brand=brand,
        live_schema=str(live_schema.resolve()),
        fill_plan=str(fill_plan.resolve()),
    )


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
    if args.resume_current_url and args.mode != "full":
        raise SystemExit("--resume-current-url is accepted only with --mode full")
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
        "resume_current_url": str(args.resume_current_url or ""),
        "makro_target_id": "",
        "ownership_mode": "",
        "writes_performed": 0,
        "save_clicked": False,
        "send_to_qc_clicked": False,
        "browser_closed": False,
    }
    _write_manifest(manifest_path, manifest)
    diag_event(
        "workflow",
        "START",
        mode=args.mode,
        product_url=args.product_url,
        makro_cdp_port=args.cdp_port,
        source_cdp_port=args.source_cdp_port,
        provider=args.provider,
        model=args.model,
        fact_model=args.fact_model,
        web_search_model=args.web_search_model,
        resume_current_url=str(args.resume_current_url or ""),
    )

    active = {
        "step1": {"source", "step1"},
        "step2": {"source", "step2"},
        "step3": {"source", "step3"},
        "full": {"source", "step1", "step2", "step3"},
    }[args.mode]
    current = "source"
    page: Any | None = None

    def set_current_phase(name: str) -> None:
        nonlocal current
        current = name

    try:
        for phase_name in ("source", "step1", "step2", "step3"):
            if phase_name not in active:
                _phase(phase_name, "SKIPPED", "not requested")

        current = "source"
        _phase("source", "START")
        diag_event(
            "source_capture",
            "START",
            product_url=args.product_url,
            output_dir=str((run_dir / "bootstrap-source").resolve()),
            cdp_port=args.source_cdp_port,
            use_current_page=args.source_use_current_page,
            force_refresh=args.refresh_source,
            cache_dir=args.source_cache_dir,
            cache_ttl_seconds=args.source_cache_ttl_seconds,
            max_scroll_steps=args.source_max_scroll_steps,
        )
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
        diag_event(
            "source_capture",
            "COMPLETE",
            snapshot=str(captured.snapshot_path.resolve()),
            screenshot=str(captured.screenshot_path.resolve()),
            cache_hit=bool(captured.cache_hit),
            source_edge_launched=bool(captured.launched_now),
            product_images=len(captured.product_image_paths),
            visible_text_chars=len(captured.snapshot.visible_text),
            table_rows=len(captured.snapshot.table_rows),
            json_ld_items=len(captured.snapshot.json_ld),
            embedded_data_items=len(captured.snapshot.embedded_data),
            warnings=list(captured.snapshot.warnings),
        )
        diag_event("listing_bootstrap", "START", product_images=len(captured.product_image_paths))
        hints = infer_listing_bootstrap(
            provider,
            captured.snapshot,
            image_paths=captured.product_image_paths,
        )
        diag_event("listing_bootstrap", "COMPLETE", hints=hints.as_dict())
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

        diag_event(
            "makro_browser",
            "START",
            cdp_port=args.cdp_port,
            profile_dir=str(Path(args.profile_dir).resolve()),
            start_url=MAKRO_NEW_LISTING_URL,
        )
        with sync_playwright() as playwright:
            harness = EdgeHarness(
                playwright,
                profile_dir=Path(args.profile_dir).resolve(),
                port=args.cdp_port,
                start_url=MAKRO_NEW_LISTING_URL,
            )
            if harness.launched_now:
                raise RuntimeError("Makro Edge unexpectedly entered launch path; aborted")
            diag_event(
                "makro_browser",
                "COMPLETE",
                launched_now=bool(harness.launched_now),
                context_available=harness.context is not None,
                browser_page_count=len(harness.context.pages) if harness.context is not None else 0,
                page_urls=(
                    [str(getattr(item, "url", "") or "") for item in harness.context.pages]
                    if harness.context is not None
                    else []
                ),
            )

            if args.mode == "full":
                if args.resume_current_url:
                    page = _resume_current_page(harness, args.resume_current_url)
                    manifest["resumed_from_page_url"] = page.url
                    manifest["makro_target_id"] = page_target_id(page)
                    manifest["ownership_mode"] = "resume_exact_page"
                    _write_manifest(manifest_path, manifest)
                    prepare_step1 = lambda: page
                    allow_initial_later_stage = True
                else:
                    page, owned_target_id = _create_fresh_owned_page(harness)
                    manifest["makro_target_id"] = owned_target_id
                    manifest["ownership_mode"] = "fresh_dedicated_tab"
                    manifest["owned_page_start_url"] = str(page.url or "")
                    _write_manifest(manifest_path, manifest)

                    def prepare_step1():
                        assert page is not None
                        prepare_owned_step1_page(page)
                        return page

                    allow_initial_later_stage = False

                page, _vertical, _brand = _advance_listing_to_step3(
                    page=page,
                    prepare_step1=prepare_step1,
                    provider=provider,
                    hints=hints,
                    manifest=manifest,
                    manifest_path=manifest_path,
                    allow_initial_later_stage=allow_initial_later_stage,
                    set_current=set_current_phase,
                )
                harness.page = page

                current = "step3"
                _phase("step3", "START")
                _prepare_step3(args, run_dir=run_dir, page=page, manifest=manifest)
                harness.detach()
                diag_event("makro_browser", "DETACH", page_url=str(page.url or ""))
                manifest["status"] = "prepare_complete"
                _write_manifest(manifest_path, manifest)
                _phase("step3", "COMPLETE", "current Resolver + Fill Plan")
            elif args.mode == "step1":
                current = "step1"
                _phase("step1", "START")
                page = prepare_single_step1_page(harness)
                diag_event("step1_prepare", "COMPLETE", page_url=str(page.url or ""), detected_stage=_listing_stage(page))
                dismiss_joyride_overlay(page)
                diag_event("vertical_selection", "START", page_url=str(page.url or ""))
                vertical = select_vertical(page, provider, hints)
                diag_event("vertical_selection", "COMPLETE", vertical=vertical, page_url=str(page.url or ""))
                manifest["vertical"] = vertical
                manifest["page_url"] = page.url
                manifest["status"] = "step1_complete"
                _write_manifest(manifest_path, manifest)
                harness.detach()
                diag_event("makro_browser", "DETACH", page_url=str(page.url or ""))
                _phase("step1", "COMPLETE", vertical)
            elif args.mode == "step2":
                current = "step2"
                _phase("step2", "START")
                page = _listing_page(harness, is_brand_step, "Step 2")
                diag_event("brand_selection", "START", page_url=str(page.url or ""))
                brand, page = select_brand_to_product_info(page, provider, hints)
                harness.page = page
                vertical, actual_brand = _target_values(page)
                diag_event(
                    "brand_selection",
                    "COMPLETE",
                    selected_brand=brand,
                    actual_brand=actual_brand,
                    vertical=vertical,
                    page_url=str(page.url or ""),
                )
                manifest["vertical"] = vertical
                manifest["brand"] = actual_brand or brand
                manifest["page_url"] = page.url
                manifest["status"] = "step2_complete"
                _write_manifest(manifest_path, manifest)
                harness.detach()
                diag_event("makro_browser", "DETACH", page_url=str(page.url or ""))
                _phase("step2", "COMPLETE", manifest["brand"])
            else:
                current = "step3"
                _phase("step3", "START")
                page = _listing_page(harness, is_product_info_step, "Step 3")
                _prepare_step3(args, run_dir=run_dir, page=page, manifest=manifest)
                harness.detach()
                diag_event("makro_browser", "DETACH", page_url=str(page.url or ""))
                manifest["status"] = "prepare_complete"
                _write_manifest(manifest_path, manifest)
                _phase("step3", "COMPLETE", "current Resolver + Fill Plan")

        diag_event(
            "workflow",
            "COMPLETE",
            mode=args.mode,
            status=manifest.get("status", ""),
            vertical=manifest.get("vertical", ""),
            brand=manifest.get("brand", ""),
            makro_target_id=manifest.get("makro_target_id", ""),
            fill_plan_summary=manifest.get("fill_plan_summary") or {},
            send_to_qc=False,
        )
        print(
            f"GUI WORKFLOW COMPLETE mode={args.mode} writes=0 save=False send_to_qc=False",
            flush=True,
        )
        return 0
    except SourceAccessBlocked as exc:
        manifest["status"] = "source_access_blocked"
        manifest["failed_phase"] = current
        manifest["error"] = str(exc)
        _write_manifest(manifest_path, manifest)
        _phase(current, "FAILED", str(exc))
        print(str(exc), flush=True)
        print("source Edge 保持打开；人工完成合法验证后用当前页选项重试。", flush=True)
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
