"""Execute a validated Fill Plan against Makro Step 3.

This is the production browser entrypoint for the listing workflow. It reuses
existing browser fill/Save/reopen helpers, but product rebind comes only from the
same canonical source snapshot and optional images used by the Resolver. It
never consumes customer QA, a manual seller SKU, expected model/brand or product-table facts.

Single-section execution may stay as a no-save visual hold or, when
``--allow-section-save`` is explicitly supplied, persist and reopen-verify that
one section. Full Step 3 remains a persisted acceptance and therefore requires
explicit Save authorization. Send to QC is never clicked.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.ai_decisions import load_ai_decision_packet
from app.browser_page_owner import find_page_by_target_id
from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness, is_cdp_ready
from app.business_fields import generate_listing_sku, generated_business_bundle
from app.evidence_contract import ProductIdentity
from app.fill_plan import BLOCKED, build_live_fill_plan
from app.live_schema import assert_live_schema_matches, load_live_schema
from app.makro import MAKRO_HOME_URL, base_section_title
from app.makro.direct_visual_hold import is_listing_attribute_field
from app.makro.domain import MakroDomainAdapter
from app.makro.execution import PRODUCT_PHOTOS, fill_one_section as _fill_one_section, run_photos as _run_photos
from app.makro.listing_preflight import CORE_FORM_SECTIONS
from app.makro.marketplace_constraints import apply_makro_decision_constraints
from app.required_overrides import apply_required_overrides, load_required_overrides
from app.semantic_grounding import build_grounding_catalog
from app.task_control import initialize_task_control, safe_pause_point
from makro_preview_listing import (
    _assert_clean_step3_start,
    _assert_single_listing_tab,
    _blocked_reason_summary,
    _completion_summary,
    _totals,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "执行 Resolver 已验证的 Makro Step 3 计划；"
            "不重新理解商品，不读取旧 QA/SKU，永不 Send to QC。"
        )
    )
    parser.add_argument("--decision-packet", required=True)
    parser.add_argument("--live-schema", required=True)
    parser.add_argument("--product-url", required=True)
    parser.add_argument("--supplier-snapshot", action="append", default=[])
    parser.add_argument("--official-snapshot", action="append", default=[])
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="Resolver evidence image；可为空，text/table-only 商品资料仍可 strict rebind。",
    )
    parser.add_argument(
        "--upload-image",
        action="append",
        default=[],
        help="明确要上传到 Product Photos 的 listing 图片。",
    )
    parser.add_argument("--expected-vertical", required=True)

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--section", help="单 section 真实填写；默认 no-save，可显式授权 Save。")
    target.add_argument("--all-step3", action="store_true")
    parser.add_argument(
        "--allow-section-save",
        action="store_true",
        help="显式允许 section Save + reopen verification；Full Step 3 必须开启。",
    )
    parser.add_argument("--include-review-candidates", action="store_true")

    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument(
        "--makro-target-id",
        default="",
        help=(
            "Batch-only owned-tab token. When supplied, execute only that exact Chromium tab; "
            "single-mode unique-tab safety remains unchanged when omitted."
        ),
    )
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    parser.add_argument("--recheck-wait-ms", type=int, default=800)
    parser.add_argument("--upload-timeout-ms", type=int, default=8_000)
    parser.add_argument("--max-text-chars", type=int, default=5000)
    parser.add_argument("--overlap-chars", type=int, default=250)
    parser.add_argument("--output-dir", default="logs/makro-direct-execution")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.all_step3 and not args.allow_section_save:
        raise SystemExit(
            "--all-step3 是真实持久化验收，必须同时传 --allow-section-save。"
        )
    if args.upload_timeout_ms <= 0:
        raise SystemExit("--upload-timeout-ms 必须大于 0")
    if args.max_text_chars < 500:
        raise SystemExit("--max-text-chars 不能小于 500")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_text_chars:
        raise SystemExit("--overlap-chars 必须 >=0 且小于 --max-text-chars")
    if not args.supplier_snapshot:
        raise SystemExit(
            "执行前必须传 Resolver 的 canonical source snapshot；"
            "拒绝只凭 decision packet 写 Makro。"
        )


def _scan_semantic_fields(
    adapter: MakroDomainAdapter,
    *,
    wait_ms: int,
    max_scroll_steps: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    sections_payload, flat_controls, scan_stats = adapter.scan_sections(
        include_values=True,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    semantic_fields = [
        field
        for field in adapter.build_semantic_fields(flat_controls)
        if is_listing_attribute_field(field)
    ]
    return semantic_fields, sections_payload, scan_stats


def _required_override_path(live_schema: str) -> Path:
    """GUI writes explicit required-field values beside this run's live schema."""

    return Path(live_schema).resolve().with_name("required-overrides.json")


def _pause_and_reconcile(
    control_root: Path,
    checkpoint: str,
    *,
    adapter: MakroDomainAdapter,
    planned_live_fields: list[dict[str, Any]],
    expected_vertical: str,
    wait_ms: int,
    max_scroll_steps: int,
    context: dict[str, Any] | None = None,
) -> None:
    """Pause only between atomic persistence units, then revalidate live Step 3."""

    resumed = safe_pause_point(control_root, checkpoint, context=context)
    if not resumed:
        return
    adapter.assert_expected_vertical(expected_vertical)
    semantic_fields, _sections, _scan = _scan_semantic_fields(
        adapter,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    assert_live_schema_matches(planned_live_fields, semantic_fields)
    print(
        "GUI_TASK_RECONCILE "
        f"checkpoint={checkpoint} page={adapter.page.url} live_fields={len(semantic_fields)}",
        flush=True,
    )


def main() -> int:
    args = build_parser().parse_args()
    _validate_args(args)
    control_root = Path(args.output_dir).resolve()
    initialize_task_control(
        control_root,
        workflow="makro_execute_listing",
        product_url=args.product_url,
    )

    if not is_cdp_ready(args.cdp_port):
        raise RuntimeError(
            f"长期 Makro Edge 的 CDP 127.0.0.1:{args.cdp_port} 不可达；"
            "不会自动启动、关闭或重启 Edge。"
        )

    planned_live_fields = load_live_schema(args.live_schema)
    grounding = build_grounding_catalog(
        image_paths=args.image,
        supplier_snapshots=args.supplier_snapshot,
        official_snapshots=args.official_snapshot,
        max_text_chars=args.max_text_chars,
        overlap_chars=args.overlap_chars,
    )
    decision_packet = load_ai_decision_packet(
        args.decision_packet,
        planned_live_fields,
        grounding,
        expected_identity=ProductIdentity(),
    )
    makro_constraint_summary = apply_makro_decision_constraints(
        decision_packet,
        planned_live_fields,
    )
    generated_sku = generate_listing_sku(args.product_url)
    business_bundle = generated_business_bundle(args.product_url, sku=generated_sku)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = control_root / f"execute-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        harness = EdgeHarness(
            playwright,
            profile_dir=Path(args.profile_dir).resolve(),
            port=args.cdp_port,
            start_url=MAKRO_HOME_URL,
        )
        if harness.launched_now:
            raise RuntimeError(
                "CDP 在连接前消失，EdgeHarness 进入启动路径；已中止，不继续页面操作。"
            )
        if harness.context is None:
            raise RuntimeError("Makro Edge context is unavailable")

        if args.makro_target_id:
            page = find_page_by_target_id(harness.context, args.makro_target_id)
            harness.page = page
        else:
            page = harness.page
            if page is None:
                raise RuntimeError("Makro Edge did not expose a usable page")

        page.set_default_timeout(15_000)
        adapter = MakroDomainAdapter(page)
        if not adapter.is_listing_page():
            if args.makro_target_id:
                raise RuntimeError(
                    "Batch owned Makro tab no longer points at an Add Listing page; refusing to navigate another tab."
                )
            adapter.wait_for_authenticated_listing(
                MAKRO_HOME_URL,
                headless=False,
                navigate_first=False,
            )

        if args.makro_target_id:
            page = find_page_by_target_id(harness.context, args.makro_target_id)
            harness.page = page
        else:
            page = harness.ensure_page()
        adapter = MakroDomainAdapter(page)
        if not args.makro_target_id:
            _assert_single_listing_tab(harness.context)
        adapter.assert_expected_vertical(args.expected_vertical)
        _assert_clean_step3_start(adapter)

        semantic_fields, sections_payload, scan_stats = _scan_semantic_fields(
            adapter,
            wait_ms=args.scroll_wait_ms,
            max_scroll_steps=args.max_scroll_steps,
        )

        assert_live_schema_matches(planned_live_fields, semantic_fields)
        plan = build_live_fill_plan(
            decision_packet,
            semantic_fields,
            business_bundle,
        )

        override_path = _required_override_path(args.live_schema)
        override_summary: dict[str, Any] = {"applied": 0, "field_ids": []}
        if override_path.is_file():
            override_summary = apply_required_overrides(
                plan,
                semantic_fields,
                load_required_overrides(override_path),
                planned_fields=planned_live_fields,
            )

        summary = plan.summary()
        if args.all_step3 and int(summary.get("required_blocked") or 0) > 0:
            missing_required = [
                item.label
                for item in plan.items
                if item.required and item.action == BLOCKED
            ]
            raise RuntimeError(
                "Full Step 3 仍有 Makro 必填项没有可靠答案；已在任何字段写入前停止。"
                "请在 GUI 的必填输入框补齐后再执行："
                + " | ".join(missing_required)
            )

        if args.all_step3:
            banner = "===== MAKRO STEP 3 DIRECT ACCEPTANCE ====="
        elif args.allow_section_save:
            banner = "===== MAKRO DIRECT SECTION PERSISTED ACCEPTANCE ====="
        else:
            banner = "===== MAKRO DIRECT SECTION PREVIEW ====="
        print(banner)
        print(f"page={page.url}")
        if args.makro_target_id:
            print(f"makro_target_id={args.makro_target_id}")
        print(f"product_url={args.product_url}")
        print(f"generated_listing_sku={generated_sku}")
        print(f"user_required_overrides={override_summary['applied']}")
        print(f"makro_constraints={makro_constraint_summary}")
        print(
            f"live_fields={summary['live_field_count']}, ready={summary['ready']}, "
            f"preview_eligible={summary['preview_eligible']}, blocked={summary['blocked']}, "
            f"required_blocked={summary['required_blocked']}"
        )
        if args.allow_section_save:
            print("section Save 已显式授权；绝不 Send to QC。")
        else:
            print("单 section 只预览，不 Save / Send to QC。")

        _pause_and_reconcile(
            control_root,
            "step3_prewrite",
            adapter=adapter,
            planned_live_fields=planned_live_fields,
            expected_vertical=args.expected_vertical,
            wait_ms=args.scroll_wait_ms,
            max_scroll_steps=args.max_scroll_steps,
            context={"page_url": page.url, "ready": summary.get("ready", 0)},
        )

        section_reports: list[dict[str, Any]] = []
        photo_report: dict[str, Any] | None = None

        if args.all_step3:
            for section_title in CORE_FORM_SECTIONS:
                report = _fill_one_section(
                    adapter,
                    plan,
                    section_title,
                    include_review_candidates=args.include_review_candidates,
                    persist=True,
                    scroll_wait_ms=args.scroll_wait_ms,
                    max_scroll_steps=args.max_scroll_steps,
                    recheck_wait_ms=args.recheck_wait_ms,
                    run_dir=run_dir,
                )
                section_reports.append(report)
                print(
                    f"{section_title}: status={report.get('status')} "
                    f"attempted={report.get('writes_attempted', 0)} "
                    f"validated={report.get('validated', 0)} "
                    f"persisted={report.get('persisted_verified', 0)}"
                )
                _pause_and_reconcile(
                    control_root,
                    f"section:{section_title}",
                    adapter=adapter,
                    planned_live_fields=planned_live_fields,
                    expected_vertical=args.expected_vertical,
                    wait_ms=args.scroll_wait_ms,
                    max_scroll_steps=args.max_scroll_steps,
                    context={
                        "section": section_title,
                        "status": report.get("status"),
                        "persisted_verified": report.get("persisted_verified", 0),
                    },
                )
            photo_report = _run_photos(
                adapter,
                list(args.upload_image),
                allow_save=True,
                upload_timeout_ms=args.upload_timeout_ms,
                run_dir=run_dir,
            )
            _pause_and_reconcile(
                control_root,
                "section:Product Photos",
                adapter=adapter,
                planned_live_fields=planned_live_fields,
                expected_vertical=args.expected_vertical,
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
                context={"photo_status": (photo_report or {}).get("status")},
            )
        else:
            section_title = base_section_title(str(args.section or ""))
            if section_title.casefold() == PRODUCT_PHOTOS.casefold():
                photo_report = _run_photos(
                    adapter,
                    list(args.upload_image),
                    allow_save=args.allow_section_save,
                    upload_timeout_ms=args.upload_timeout_ms,
                    run_dir=run_dir,
                )
                _pause_and_reconcile(
                    control_root,
                    "section:Product Photos",
                    adapter=adapter,
                    planned_live_fields=planned_live_fields,
                    expected_vertical=args.expected_vertical,
                    wait_ms=args.scroll_wait_ms,
                    max_scroll_steps=args.max_scroll_steps,
                    context={"photo_status": (photo_report or {}).get("status")},
                )
            else:
                report = _fill_one_section(
                    adapter,
                    plan,
                    section_title,
                    include_review_candidates=args.include_review_candidates,
                    persist=args.allow_section_save,
                    scroll_wait_ms=args.scroll_wait_ms,
                    max_scroll_steps=args.max_scroll_steps,
                    recheck_wait_ms=args.recheck_wait_ms,
                    run_dir=run_dir,
                )
                section_reports.append(report)
                print(
                    f"{section_title}: status={report.get('status')} "
                    f"attempted={report.get('writes_attempted', 0)} "
                    f"validated={report.get('validated', 0)} "
                    f"persisted={report.get('persisted_verified', 0)}"
                )
                _pause_and_reconcile(
                    control_root,
                    f"section:{section_title}",
                    adapter=adapter,
                    planned_live_fields=planned_live_fields,
                    expected_vertical=args.expected_vertical,
                    wait_ms=args.scroll_wait_ms,
                    max_scroll_steps=args.max_scroll_steps,
                    context={"section": section_title, "status": report.get("status")},
                )

        totals = _totals(section_reports)
        completion = (
            _completion_summary(section_reports, photo_report, summary)
            if args.all_step3
            else None
        )
        final_screenshot = run_dir / "step3-final.png"
        page.screenshot(path=str(final_screenshot), full_page=True)

        if args.all_step3:
            mode = "single_url_all_step3_persisted_acceptance"
        elif args.allow_section_save:
            mode = "single_url_section_persisted_acceptance"
        else:
            mode = "single_url_section_preview"

        payload = {
            "mode": mode,
            "page_url": page.url,
            "makro_target_id": args.makro_target_id,
            "product_url": args.product_url,
            "generated_listing_sku": generated_sku,
            "expected_vertical": args.expected_vertical,
            "decision_packet": str(Path(args.decision_packet).resolve()),
            "live_schema": str(Path(args.live_schema).resolve()),
            "required_override_file": str(override_path) if override_path.is_file() else "",
            "required_overrides": override_summary,
            "makro_constraints": makro_constraint_summary,
            "source_snapshots": [str(Path(path).resolve()) for path in args.supplier_snapshot],
            "evidence_images": [str(Path(path).resolve()) for path in args.image],
            "live_schema_verified": True,
            "decision_packet_rebound": True,
            "include_review_candidates": args.include_review_candidates,
            "allow_section_save": args.allow_section_save,
            "plan_summary": summary,
            "blocked_reason_summary": _blocked_reason_summary(plan),
            "fill_plan": plan.as_dict(),
            "scan": scan_stats,
            "sections": [item.get("title") for item in sections_payload],
            "section_reports": section_reports,
            "field_totals": totals,
            "photo_upload": photo_report,
            "completion": completion,
            "grounded_source_count": len(grounding.sources),
            "decision_warnings": decision_packet.warnings,
            "section_save_attempted": sum(1 for item in section_reports if item.get("save_attempted"))
            + int(bool(photo_report and photo_report.get("save_attempted"))),
            "section_saved": sum(1 for item in section_reports if item.get("saved"))
            + int(bool(photo_report and photo_report.get("saved"))),
            "send_to_qc_clicked": False,
            "browser_closed": False,
            "final_screenshot": str(final_screenshot.resolve()),
        }
        report_path = run_dir / "report.json"
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(
            "\n===== ACCEPTANCE COMPLETE ====="
            if args.all_step3 or args.allow_section_save
            else "\n===== PREVIEW READY ====="
        )
        print(
            f"field candidate={totals['candidate_count']}, attempted={totals['writes_attempted']}, "
            f"validated={totals['validated']}, persisted={totals['persisted_verified']}, "
            f"validation_failed={totals['validation_failed'] + totals['persisted_validation_failed']}, "
            f"fill_error={totals['fill_error']}"
        )
        if photo_report is not None:
            print(
                "photos: "
                f"status={photo_report.get('status')} requested={photo_report.get('requested', 0)} "
                f"attempted={photo_report.get('attempted', 0)} staged={photo_report.get('staged', 0)} "
                f"saved={photo_report.get('saved', False)}"
            )
        if completion is not None:
            print(f"draft_persisted_complete={completion['draft_persisted_complete']}")
            print(f"autofill_safe_complete={completion['autofill_safe_complete']}")
        print("Send to QC=False。")
        print(f"报告：{report_path.resolve()}")
        print(f"最终截图：{final_screenshot.resolve()}")

        harness.detach()
        if args.all_step3 and completion is not None:
            acceptance_ok = bool(completion.get("draft_persisted_complete")) and bool(
                completion.get("autofill_safe_complete")
            )
            if not acceptance_ok:
                print("Full Step 3 persisted acceptance 未完整通过；进程返回非零状态。")
                return 2
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
