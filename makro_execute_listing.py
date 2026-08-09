"""Execute a validated single-product-URL Fill Plan against Makro Step 3.

This is the production browser entrypoint for the one-link workflow.  It reuses
existing browser fill/Save/reopen helpers, but product rebind comes only from the
same raw source snapshot/images used by makro_resolve_ai.py.  It never consumes
customer QA, a manual seller SKU, expected model/brand or product-table facts.

Send to QC is never clicked.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.ai_decisions import load_ai_decision_packet
from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness, is_cdp_ready
from app.business_fields import generate_listing_sku, generated_business_bundle
from app.evidence_contract import ProductIdentity
from app.fill_plan import build_live_fill_plan
from app.live_schema import assert_live_schema_matches, load_live_schema
from app.makro import MAKRO_HOME_URL, base_section_title
from app.makro.direct_visual_hold import is_listing_attribute_field
from app.makro.domain import MakroDomainAdapter
from app.makro.listing_preflight import CORE_FORM_SECTIONS
from app.semantic_grounding import build_grounding_catalog
from makro_preview_listing import (
    PRODUCT_PHOTOS,
    _assert_clean_step3_start,
    _assert_single_listing_tab,
    _blocked_reason_summary,
    _completion_summary,
    _fill_one_section,
    _run_photos,
    _totals,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "执行单一商品链接 Resolver 已验证的 Makro Step 3 计划；"
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
        help="Resolver evidence image；用于 strict source rebind，不自动上传 Makro。",
    )
    parser.add_argument(
        "--upload-image",
        action="append",
        default=[],
        help="明确要上传到 Product Photos 的 listing 图片。",
    )
    parser.add_argument("--expected-vertical", required=True)

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--section", help="单 section no-save 诊断预览。")
    target.add_argument("--all-step3", action="store_true")
    parser.add_argument("--allow-section-save", action="store_true")
    parser.add_argument("--include-review-candidates", action="store_true")

    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
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
    if args.section and args.allow_section_save:
        raise SystemExit("--allow-section-save 只允许与 --all-step3 一起使用。")
    if args.upload_timeout_ms <= 0:
        raise SystemExit("--upload-timeout-ms 必须大于 0")
    if args.max_text_chars < 500:
        raise SystemExit("--max-text-chars 不能小于 500")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_text_chars:
        raise SystemExit("--overlap-chars 必须 >=0 且小于 --max-text-chars")
    if not args.supplier_snapshot:
        raise SystemExit(
            "执行前必须传 Resolver 的 primary-source/source-snapshot.json；"
            "拒绝只凭 decision packet 写 Makro。"
        )
    if not args.image:
        raise SystemExit(
            "执行前必须传 Resolver 的 primary-source/source-page.png；"
            "strict source rebind 需要与 Resolver 相同 evidence。"
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


def main() -> int:
    args = build_parser().parse_args()
    _validate_args(args)

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
    business_bundle = generated_business_bundle(args.product_url)
    generated_sku = generate_listing_sku(args.product_url)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_dir) / f"execute-{stamp}"
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

        page = harness.page
        page.set_default_timeout(15_000)
        adapter = MakroDomainAdapter(page)
        if not adapter.is_listing_page():
            adapter.wait_for_authenticated_listing(
                MAKRO_HOME_URL,
                headless=False,
                navigate_first=False,
            )

        page = harness.ensure_page()
        adapter = MakroDomainAdapter(page)
        _assert_single_listing_tab(harness.context)
        adapter.assert_expected_vertical(args.expected_vertical)
        _assert_clean_step3_start(adapter)

        semantic_fields, sections_payload, scan_stats = _scan_semantic_fields(
            adapter,
            wait_ms=args.scroll_wait_ms,
            max_scroll_steps=args.max_scroll_steps,
        )

        # Last pre-write boundaries are purely mechanical: current Makro schema
        # and exact source manifest must still match the read-only plan.
        assert_live_schema_matches(planned_live_fields, semantic_fields)
        plan = build_live_fill_plan(
            decision_packet,
            semantic_fields,
            business_bundle,
        )
        summary = plan.summary()

        print(
            "===== MAKRO STEP 3 DIRECT ACCEPTANCE ====="
            if args.all_step3
            else "===== MAKRO DIRECT SECTION PREVIEW ====="
        )
        print(f"page={page.url}")
        print(f"product_url={args.product_url}")
        print(f"generated_listing_sku={generated_sku}")
        print(
            f"live_fields={summary['live_field_count']}, ready={summary['ready']}, "
            f"preview_eligible={summary['preview_eligible']}, blocked={summary['blocked']}, "
            f"required_blocked={summary['required_blocked']}"
        )
        print(
            "section Save 已显式授权；绝不 Send to QC。"
            if args.all_step3
            else "单 section 只预览，不 Save / Send to QC。"
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
            photo_report = _run_photos(
                adapter,
                list(args.upload_image),
                allow_save=True,
                upload_timeout_ms=args.upload_timeout_ms,
                run_dir=run_dir,
            )
        else:
            section_title = base_section_title(str(args.section or ""))
            if section_title.casefold() == PRODUCT_PHOTOS.casefold():
                photo_report = _run_photos(
                    adapter,
                    list(args.upload_image),
                    allow_save=False,
                    upload_timeout_ms=args.upload_timeout_ms,
                    run_dir=run_dir,
                )
            else:
                section_reports.append(
                    _fill_one_section(
                        adapter,
                        plan,
                        section_title,
                        include_review_candidates=args.include_review_candidates,
                        persist=False,
                        scroll_wait_ms=args.scroll_wait_ms,
                        max_scroll_steps=args.max_scroll_steps,
                        recheck_wait_ms=args.recheck_wait_ms,
                        run_dir=run_dir,
                    )
                )

        totals = _totals(section_reports)
        completion = (
            _completion_summary(section_reports, photo_report, summary)
            if args.all_step3
            else None
        )
        final_screenshot = run_dir / "step3-final.png"
        page.screenshot(path=str(final_screenshot), full_page=True)

        payload = {
            "mode": "single_url_all_step3_persisted_acceptance" if args.all_step3 else "single_url_section_preview",
            "page_url": page.url,
            "product_url": args.product_url,
            "generated_listing_sku": generated_sku,
            "expected_vertical": args.expected_vertical,
            "decision_packet": str(Path(args.decision_packet).resolve()),
            "live_schema": str(Path(args.live_schema).resolve()),
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
            if args.all_step3
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
                f"status={photo_report.get('status')} attempted={photo_report.get('attempted', 0)} "
                f"staged={photo_report.get('staged', 0)} saved={photo_report.get('saved', False)}"
            )
        if completion is not None:
            print(f"draft_persisted_complete={completion['draft_persisted_complete']}")
            print(f"autofill_safe_complete={completion['autofill_safe_complete']}")
        print("Send to QC=False。")
        print(f"报告：{report_path.resolve()}")
        print(f"最终截图：{final_screenshot.resolve()}")

        harness.detach()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
