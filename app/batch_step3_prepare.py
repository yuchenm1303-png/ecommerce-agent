from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .makro.domain import MakroDomainAdapter
from .workflow_diagnostics import diag_event
from .workflow_runtime import scan_and_write_live_schema, single_run_dir


def capture_batch_step3_schema(
    args: Any,
    *,
    run_dir: Path,
    page: Any,
    manifest: dict[str, Any],
) -> tuple[Path, str, str, str]:
    """Capture the browser-owned Step 3 contract, then return pure artifacts.

    Batch workers must release their Playwright transport immediately after this
    function. Resolver and Fill Plan generation consume only the captured schema
    and supplier evidence, so they can continue in parallel without holding the
    one Edge transport lane.
    """

    from makro_gui_workflow import _target_values

    vertical, brand = _target_values(page)
    page_url = str(page.url or "")
    adapter = MakroDomainAdapter(page)
    adapter.assert_expected_vertical(vertical)

    schema_root = run_dir / "01-live-schema" / "live-scan-current"
    schema_root.mkdir(parents=True, exist_ok=True)
    diag_event(
        "batch_live_schema_scan",
        "START",
        page_url=page_url,
        target=str(schema_root / "live-schema.json"),
        vertical=vertical,
        makro_target_id=str(manifest.get("makro_target_id") or ""),
    )
    live_schema, scan_info = scan_and_write_live_schema(
        adapter,
        schema_root / "live-schema.json",
        wait_ms=args.scroll_wait_ms,
        max_scroll_steps=args.max_scroll_steps,
    )
    manifest.update(
        {
            "vertical": vertical,
            "brand": brand,
            "page_url": page_url,
            "live_schema": str(live_schema.resolve()),
            "live_schema_scan": scan_info,
            "browser_transport_released_before_resolver": True,
        }
    )
    diag_event(
        "batch_live_schema_scan",
        "COMPLETE",
        page_url=page_url,
        live_schema=str(live_schema.resolve()),
        listing_attribute_fields=scan_info.get("listing_attribute_fields"),
        semantic_fields_before_filter=scan_info.get("semantic_fields_before_filter"),
        sections=scan_info.get("sections") or [],
    )
    return live_schema, vertical, brand, page_url


def complete_batch_step3_from_schema(
    args: Any,
    *,
    run_dir: Path,
    live_schema: Path,
    vertical: str,
    manifest: dict[str, Any],
    pack_manifest: Path | None = None,
) -> None:
    """Run Resolver + pure Final Plan after browser ownership has been released."""

    if pack_manifest is not None:
        from makro_product_pack_workflow import _resolver_pair_for_pack

        cold_path, cold_manifest, hot_path, hot_manifest = _resolver_pair_for_pack(
            args,
            run_dir=run_dir,
            live_schema=live_schema,
            pack_manifest=pack_manifest,
        )
    else:
        from makro_gui_workflow import _run_resolver_pair

        cold_path, cold_manifest, hot_path, hot_manifest = _run_resolver_pair(
            args,
            run_dir=run_dir,
            live_schema=live_schema,
        )

    manifest["cold_resolver_manifest"] = str(cold_path.resolve())
    manifest["resolver_manifest"] = str(hot_path.resolve())
    manifest["resolver_summary"] = hot_manifest.get("final_decision_summary")
    diag_event(
        "batch_resolver_pair",
        "COMPLETE",
        cold_manifest=str(cold_path.resolve()),
        hot_manifest=str(hot_path.resolve()),
        cold_summary=cold_manifest.get("final_decision_summary") or {},
        hot_summary=hot_manifest.get("final_decision_summary") or {},
        browser_transport_held=False,
    )

    from makro_gui_workflow import _plan_command, _run

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
    plan_run = single_run_dir(plan_root, "plan-")
    fill_plan = plan_run / "fill-plan.json"
    fill_plan_manifest = plan_run / "manifest.json"
    plan_payload = json.loads(fill_plan.read_text(encoding="utf-8"))
    manifest["fill_plan"] = str(fill_plan.resolve())
    manifest["fill_plan_manifest"] = str(fill_plan_manifest.resolve())
    manifest["fill_plan_summary"] = plan_payload.get("summary") or {}
    diag_event(
        "batch_fill_plan",
        "COMPLETE",
        fill_plan=str(fill_plan.resolve()),
        fill_plan_manifest=str(fill_plan_manifest.resolve()),
        summary=manifest["fill_plan_summary"],
        browser_transport_held=False,
    )


__all__ = [
    "capture_batch_step3_schema",
    "complete_batch_step3_from_schema",
]
