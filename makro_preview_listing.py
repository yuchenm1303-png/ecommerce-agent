"""Run a no-save real-data review of Makro Step 3.

Two modes share the same execution path:

- ``--section``: fill one field section and leave it open for inspection.
- ``--all-step3``: exercise every core field section in one run, screenshot it,
  Cancel only that section, continue, then test explicit Product Photos uploads.

The command never clicks section Save or Send to QC. Evidence images and listing
images are deliberately separate: ``--image`` is resolver evidence only;
``--upload-image`` is the exact file the user explicitly wants to test-upload.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.alias_config import load_alias_config
from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness, is_cdp_ready
from app.fill_plan import LiveFillPlan, LiveFillPlanItem, build_live_fill_plan
from app.makro import MAKRO_HOME_URL, base_section_title, is_listing_url
from app.makro.direct_visual_hold import is_listing_attribute_field
from app.makro.domain import MakroDomainAdapter
from app.makro.listing_preflight import CORE_FORM_SECTIONS
from app.qa_catalog import load_question_catalog
from app.resolution_engine import ResolutionPolicy
from app.resolver_inputs import ResolutionInputSpec, build_resolution_inputs
from app.review_preview import execution_answer_for_item, preview_mode_for_item
from app.source_bundle import normalize_key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "使用真实 Evidence 对 Makro Step 3 做 no-save review："
            "字段填写 + 回读 + 可选图片上传；绝不 Save / Send to QC。"
        )
    )
    parser.add_argument("--qa", required=True)
    parser.add_argument("--sku", default="")
    parser.add_argument("--expected-model", default="")
    parser.add_argument("--expected-brand", default="")
    parser.add_argument("--product-table", default=None)
    parser.add_argument("--facts-json", action="append", default=[])
    parser.add_argument("--evidence-packet", action="append", default=[])
    parser.add_argument("--supplier-snapshot", action="append", default=[])
    parser.add_argument("--official-snapshot", action="append", default=[])
    parser.add_argument("--supplemental-text", default="")
    parser.add_argument("--supplemental-text-file", default=None)
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="Resolver evidence image；不会因为传入这里就上传到 Makro。",
    )
    parser.add_argument(
        "--upload-image",
        action="append",
        default=[],
        help="明确要上传到 Product Photos 的图片，可重复传入。",
    )
    parser.add_argument("--product-url", default=None)
    parser.add_argument("--expected-vertical", required=True)

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--section", help="只测试一个字段 section。")
    target.add_argument(
        "--all-step3",
        action="store_true",
        help="一次测试三个 core field sections，并在最后测试 Product Photos。",
    )

    parser.add_argument(
        "--alias-config",
        default=None,
        help="可选、人工审核过的 QA question -> Makro label 显式别名配置。",
    )
    parser.add_argument("--auto-fill-min-confidence", type=float, default=0.85)
    parser.add_argument("--ai-auto-fill-min-confidence", type=float, default=0.92)
    parser.add_argument(
        "--include-review-candidates",
        action="store_true",
        help=(
            "允许 preview_eligible 的 needs_review 候选临时进入页面。"
            "它们仍然不是 autofill-safe，也不会允许 Save。"
        ),
    )
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    parser.add_argument("--recheck-wait-ms", type=int, default=800)
    parser.add_argument("--upload-timeout-ms", type=int, default=30_000)
    parser.add_argument("--output-dir", default="logs/makro-review-preview")
    return parser


def _input_spec(args: argparse.Namespace) -> ResolutionInputSpec:
    return ResolutionInputSpec(
        sku=args.sku,
        expected_model=args.expected_model,
        expected_brand=args.expected_brand,
        product_table=args.product_table,
        facts_json=tuple(args.facts_json),
        evidence_packets=tuple(args.evidence_packet),
        supplier_snapshots=tuple(args.supplier_snapshot),
        official_snapshots=tuple(args.official_snapshot),
        supplemental_text=args.supplemental_text,
        supplemental_text_file=args.supplemental_text_file,
        image_paths=tuple(args.image),
        product_url=args.product_url,
    )


def _assert_single_listing_tab(context: Any) -> None:
    listing_pages = [page for page in context.pages if is_listing_url(page.url)]
    if len(listing_pages) != 1:
        raise RuntimeError(
            f"Add Listing tab 必须恰好 1 个，当前 {len(listing_pages)} 个；已停止，不做填写。"
        )


def _assert_no_unsaved_section(adapter: MakroDomainAdapter) -> None:
    expanded = [
        title
        for title in CORE_FORM_SECTIONS
        if (section := adapter.find_section(title)) is not None
        and not section.get("has_edit")
    ]
    photo = adapter.find_section("Product Photos")
    if photo is not None and not photo.get("has_edit"):
        expanded.append("Product Photos")
    if expanded:
        raise RuntimeError(
            "检测到仍处于编辑状态的 section："
            + " | ".join(expanded)
            + "。为避免覆盖/Cancel 人工未保存内容，本次 review 已停止。"
        )


def _identity(attribute_key: str, label: str, section_heading: str) -> tuple[str, str, str]:
    return (
        normalize_key(attribute_key),
        normalize_key(label),
        normalize_key(base_section_title(section_heading)),
    )


def _field_identity(field: dict[str, Any]) -> tuple[str, str, str]:
    return _identity(
        str(field.get("attribute_key") or ""),
        str(field.get("label") or ""),
        str(field.get("section_heading") or ""),
    )


def _item_identity(item: LiveFillPlanItem) -> tuple[str, str, str]:
    return _identity(item.attribute_key, item.label, item.section_heading)


def _has_existing_value(field: dict[str, Any]) -> bool:
    placeholders = {"selectone", "select", "choose", "请选择"}
    for control in field.get("controls") or []:
        if control.get("value_recorded") and str(control.get("value") or "").strip():
            return True
        for option in control.get("options") or []:
            if not option.get("selected"):
                continue
            visible = str(option.get("text") or option.get("value") or "").strip()
            if visible and normalize_key(visible) not in placeholders:
                return True
    return False


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return text or "section"


def _cancel_section(adapter: MakroDomainAdapter, section_title: str) -> None:
    section = adapter.find_section(section_title)
    if section is None:
        raise RuntimeError(f"Cancel 前找不到 section：{section_title}")
    path = str(section.get("path") or "")
    if not path:
        raise RuntimeError(f"section {section_title!r} 缺少稳定 DOM path。")
    cancel = adapter.page.locator(path).get_by_text("Cancel", exact=True)
    if cancel.count() != 1 or not cancel.first.is_visible():
        raise RuntimeError(
            f"section {section_title!r} 没有唯一可见 Cancel；拒绝猜测其它按钮。"
        )
    cancel.first.click()
    adapter.page.wait_for_timeout(450)
    collapsed = adapter.find_section(section_title)
    if collapsed is None or not collapsed.get("has_edit"):
        raise RuntimeError(f"section {section_title!r} Cancel 后未恢复折叠态。")


def _section_candidates(
    plan: LiveFillPlan,
    section_title: str,
    *,
    include_review_candidates: bool,
) -> list[LiveFillPlanItem]:
    wanted = base_section_title(section_title)
    return [
        item
        for item in plan.items
        if base_section_title(item.section_heading) == wanted
        and preview_mode_for_item(
            item,
            include_review_candidates=include_review_candidates,
        )
        is not None
    ]


def _fill_one_section(
    adapter: MakroDomainAdapter,
    plan: LiveFillPlan,
    section_title: str,
    *,
    include_review_candidates: bool,
    scroll_wait_ms: int,
    max_scroll_steps: int,
    recheck_wait_ms: int,
    run_dir: Path,
    cancel_after: bool,
) -> dict[str, Any]:
    candidates = _section_candidates(
        plan,
        section_title,
        include_review_candidates=include_review_candidates,
    )
    section_report: dict[str, Any] = {
        "section": section_title,
        "candidate_count": len(candidates),
        "writes_attempted": 0,
        "validated": 0,
        "validation_failed": 0,
        "fill_error": 0,
        "skipped_existing": 0,
        "skipped_live_match": 0,
        "results": [],
        "cancelled_after_test": False,
        "screenshot": None,
    }
    if not candidates:
        section_report["status"] = "no_candidates"
        return section_report

    section = adapter.find_section(section_title)
    if section is None:
        section_report["status"] = "section_not_found"
        return section_report

    adapter.open_section_for_edit(section)
    section = adapter.find_section(section_title) or section
    section_path = str(section.get("path") or "")
    if not section_path:
        section_report["status"] = "section_path_missing"
        return section_report

    fresh_controls = adapter.scan_section_fields(
        section_path,
        include_values=True,
        wait_ms=scroll_wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    fresh_fields = [
        field
        for field in adapter.build_semantic_fields(fresh_controls)
        if is_listing_attribute_field(field)
    ]
    fields_by_identity: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for field in fresh_fields:
        fields_by_identity.setdefault(_field_identity(field), []).append(field)

    for item in candidates:
        mode = preview_mode_for_item(
            item,
            include_review_candidates=include_review_candidates,
        )
        matches = fields_by_identity.get(_item_identity(item), [])
        base_payload = {
            "attribute_key": item.attribute_key,
            "label": item.label,
            "question_number": item.question_number,
            "question": item.question,
            "preview_mode": mode,
            "original_status": item.resolution.status,
            "eligible_for_autofill": item.resolution.eligible_for_autofill,
            "preview_eligible": item.resolution.preview_eligible,
            "gate_reason": item.resolution.gate_reason,
            "answer": item.resolution.answer,
            "answer_values": item.resolution.answer_values,
            "confidence": item.resolution.confidence,
            "source_type": item.resolution.source_type,
            "source_reference": item.resolution.source_reference,
            "evidence": item.resolution.evidence,
        }

        if len(matches) != 1:
            section_report["skipped_live_match"] += 1
            section_report["results"].append(
                {
                    **base_payload,
                    "execution_status": "skipped_live_match",
                    "detail": f"展开 section 后 live field 匹配数={len(matches)}，期望恰好 1。",
                }
            )
            continue

        semantic_field = matches[0]
        if _has_existing_value(semantic_field):
            section_report["skipped_existing"] += 1
            section_report["results"].append(
                {
                    **base_payload,
                    "execution_status": "skipped_existing",
                    "detail": "当前控件已有值；review 禁止覆盖。",
                }
            )
            continue

        execution_answer = execution_answer_for_item(
            item,
            include_review_candidates=include_review_candidates,
        )
        section_report["writes_attempted"] += 1
        verification = adapter.fill_resolved_field(
            semantic_field,
            execution_answer,
            section_path=section_path,
            recheck_wait_ms=recheck_wait_ms,
        )
        if verification.status == "validated":
            section_report["validated"] += 1
        elif verification.status == "fill_error":
            section_report["fill_error"] += 1
        else:
            section_report["validation_failed"] += 1
        section_report["results"].append(
            {
                **base_payload,
                "execution_status": verification.status,
                "verification": verification.as_dict(),
            }
        )

    screenshot = run_dir / f"{_safe_name(section_title)}.png"
    adapter.page.screenshot(path=str(screenshot), full_page=True)
    section_report["screenshot"] = str(screenshot.resolve())
    section_report["status"] = "completed"

    if cancel_after:
        _cancel_section(adapter, section_title)
        section_report["cancelled_after_test"] = True
    return section_report


def _totals(section_reports: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "candidate_count",
        "writes_attempted",
        "validated",
        "validation_failed",
        "fill_error",
        "skipped_existing",
        "skipped_live_match",
    )
    return {
        key: sum(int(report.get(key) or 0) for report in section_reports)
        for key in keys
    }


def main() -> int:
    args = build_parser().parse_args()
    for name, value in (
        ("auto-fill-min-confidence", args.auto_fill_min_confidence),
        ("ai-auto-fill-min-confidence", args.ai_auto_fill_min_confidence),
    ):
        if not 0.0 <= value <= 1.0:
            raise SystemExit(f"--{name} 必须在 0..1")
    if args.upload_timeout_ms <= 0:
        raise SystemExit("--upload-timeout-ms 必须大于 0")

    if not is_cdp_ready(args.cdp_port):
        raise RuntimeError(
            f"长期 Makro Edge 的 CDP 127.0.0.1:{args.cdp_port} 不可达；"
            "review 已停止，不会自动启动、关闭或重启 Edge。"
        )

    catalog = load_question_catalog(args.qa)
    input_result = build_resolution_inputs(catalog, _input_spec(args))
    policy = ResolutionPolicy(
        auto_fill_min_confidence=args.auto_fill_min_confidence,
        ai_auto_fill_min_confidence=args.ai_auto_fill_min_confidence,
    )
    alias_config = (
        load_alias_config(args.alias_config, expected_vertical=args.expected_vertical)
        if args.alias_config
        else None
    )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_dir) / f"preview-{stamp}"
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
                "CDP 在连接前消失，EdgeHarness 进入启动路径；"
                "review 已中止，不继续页面操作。"
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
        _assert_no_unsaved_section(adapter)

        sections_payload, flat_controls, scan_stats = adapter.scan_sections(
            include_values=False,
            wait_ms=args.scroll_wait_ms,
            max_scroll_steps=args.max_scroll_steps,
        )
        all_semantic_fields = adapter.build_semantic_fields(flat_controls)
        semantic_fields = [
            field for field in all_semantic_fields if is_listing_attribute_field(field)
        ]
        plan = build_live_fill_plan(
            catalog,
            semantic_fields,
            input_result.bundle,
            policy=policy,
            aliases=alias_config.aliases if alias_config else None,
        )

        print("===== MAKRO STEP 3 REVIEW =====")
        print(f"page={page.url}")
        print(
            f"ready={plan.summary()['ready']}, "
            f"preview_eligible={plan.summary()['preview_eligible']}, "
            f"blocked={plan.summary()['blocked']}"
        )
        print("不会 Save，不会 Send to QC。")

        section_reports: list[dict[str, Any]] = []
        photo_report: dict[str, Any] | None = None

        if args.all_step3:
            for section_title in CORE_FORM_SECTIONS:
                try:
                    report = _fill_one_section(
                        adapter,
                        plan,
                        section_title,
                        include_review_candidates=args.include_review_candidates,
                        scroll_wait_ms=args.scroll_wait_ms,
                        max_scroll_steps=args.max_scroll_steps,
                        recheck_wait_ms=args.recheck_wait_ms,
                        run_dir=run_dir,
                        cancel_after=True,
                    )
                except Exception as exc:
                    report = {
                        "section": section_title,
                        "status": "section_error",
                        "detail": str(exc),
                        "results": [],
                    }
                    try:
                        live = adapter.find_section(section_title)
                        if live is not None and not live.get("has_edit"):
                            _cancel_section(adapter, section_title)
                            report["cancelled_after_error"] = True
                    except Exception as cleanup_exc:
                        report["cleanup_error"] = str(cleanup_exc)
                section_reports.append(report)
                print(
                    f"{section_title}: status={report.get('status')} "
                    f"attempted={report.get('writes_attempted', 0)} "
                    f"validated={report.get('validated', 0)}"
                )

            if args.upload_image:
                try:
                    photo_result = adapter.upload_product_photos(
                        args.upload_image,
                        timeout_ms=args.upload_timeout_ms,
                    )
                    photo_report = photo_result.as_dict()
                    screenshot = run_dir / "Product-Photos.png"
                    page.screenshot(path=str(screenshot), full_page=True)
                    photo_report["screenshot"] = str(screenshot.resolve())
                except Exception as exc:
                    photo_report = {
                        "status": "upload_error",
                        "detail": str(exc),
                    }
            else:
                photo_report = {
                    "status": "skipped",
                    "detail": "没有传入 --upload-image；本轮没有执行 Product Photos 上传。",
                }
        else:
            section_title = base_section_title(str(args.section or ""))
            if section_title.casefold() == "product photos".casefold():
                if not args.upload_image:
                    photo_report = {
                        "status": "skipped",
                        "detail": "测试 Product Photos 必须显式传入 --upload-image。",
                    }
                else:
                    photo_result = adapter.upload_product_photos(
                        args.upload_image,
                        timeout_ms=args.upload_timeout_ms,
                    )
                    photo_report = photo_result.as_dict()
                    screenshot = run_dir / "Product-Photos.png"
                    page.screenshot(path=str(screenshot), full_page=True)
                    photo_report["screenshot"] = str(screenshot.resolve())
            else:
                section_reports.append(
                    _fill_one_section(
                        adapter,
                        plan,
                        section_title,
                        include_review_candidates=args.include_review_candidates,
                        scroll_wait_ms=args.scroll_wait_ms,
                        max_scroll_steps=args.max_scroll_steps,
                        recheck_wait_ms=args.recheck_wait_ms,
                        run_dir=run_dir,
                        cancel_after=False,
                    )
                )

        totals = _totals(section_reports)
        final_screenshot = run_dir / "step3-final.png"
        page.screenshot(path=str(final_screenshot), full_page=True)
        payload = {
            "mode": "all_step3_review" if args.all_step3 else "single_section_review",
            "page_url": page.url,
            "expected_vertical": args.expected_vertical,
            "include_review_candidates": args.include_review_candidates,
            "plan_summary": plan.summary(),
            "scan": scan_stats,
            "sections": [item.get("title") for item in sections_payload],
            "section_reports": section_reports,
            "field_totals": totals,
            "photo_upload": photo_report,
            "evidence_items": len(input_result.bundle.evidence),
            "evidence_warnings": input_result.warnings,
            "save_clicked": False,
            "send_to_qc_clicked": False,
            "browser_closed": False,
            "final_screenshot": str(final_screenshot.resolve()),
        }
        report_path = run_dir / "report.json"
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("\n===== REVIEW COMPLETE =====")
        print(
            f"field candidate={totals['candidate_count']}, "
            f"attempted={totals['writes_attempted']}, "
            f"validated={totals['validated']}, "
            f"validation_failed={totals['validation_failed']}, "
            f"fill_error={totals['fill_error']}"
        )
        if photo_report is not None:
            print(
                "photos: "
                f"status={photo_report.get('status')} "
                f"attempted={photo_report.get('attempted', 0)} "
                f"uploaded={photo_report.get('uploaded', 0)}"
            )
        print("没有 Save / Send to QC。完整问题集中记录在同一个 report.json。")
        print(f"报告：{report_path.resolve()}")
        print(f"最终截图：{final_screenshot.resolve()}")

        harness.detach()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
