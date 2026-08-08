"""Execute grounded real-product data against Makro Step 3.

Modes are intentionally different and explicit:

- ``--section``: diagnostic preview of one field section. It never saves and
  leaves the section open for human inspection.
- ``--all-step3 --allow-section-save``: real Step 3 acceptance. Each field card
  is filled, section-Saved, re-opened and read back before the runner proceeds;
  Product Photos is staged, Saved, then verified by its persisted completion
  counter. Save failures are collected, safely Cancelled, and the run continues
  so one execution exposes the complete defect set.

Neither mode ever clicks Send to QC. Evidence images (``--image``) are separate
from listing images (``--upload-image``); no evidence screenshot is uploaded
implicitly.
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

PRODUCT_PHOTOS = "Product Photos"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "使用真实 Evidence 检查/持久化 Makro Step 3；"
            "单 section 为 no-save 预览，全量模式必须显式授权 section Save；永不 Send to QC。"
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
        help="Resolver evidence image；不会自动上传到 Makro。",
    )
    parser.add_argument(
        "--upload-image",
        action="append",
        default=[],
        help="明确要上传到 Product Photos 的 listing 图片，可重复传入。",
    )
    parser.add_argument("--product-url", default=None)
    parser.add_argument("--expected-vertical", required=True)

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--section", help="只预览一个字段 section；不 Save。")
    target.add_argument(
        "--all-step3",
        action="store_true",
        help="执行完整 Step 3 acceptance；必须同时传 --allow-section-save。",
    )
    parser.add_argument(
        "--allow-section-save",
        action="store_true",
        help=(
            "显式允许保存 Step 3 各卡片的 listing draft。"
            "只对 --all-step3 有效；仍绝不点击 Send to QC。"
        ),
    )

    parser.add_argument("--alias-config", default=None)
    parser.add_argument("--auto-fill-min-confidence", type=float, default=0.85)
    parser.add_argument("--ai-auto-fill-min-confidence", type=float, default=0.92)
    parser.add_argument(
        "--include-review-candidates",
        action="store_true",
        help=(
            "显式把 preview_eligible 的 needs_review 候选放进当前 draft。"
            "它们仍不等于生产环境 autofill-safe。"
        ),
    )
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    parser.add_argument("--recheck-wait-ms", type=int, default=800)
    parser.add_argument("--upload-timeout-ms", type=int, default=8_000)
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
            f"Add Listing tab 必须恰好 1 个，当前 {len(listing_pages)} 个；已停止。"
        )


def _assert_clean_step3_start(adapter: MakroDomainAdapter) -> None:
    expanded = [
        title
        for title in (*CORE_FORM_SECTIONS, PRODUCT_PHOTOS)
        if (section := adapter.find_section(title)) is not None
        and not section.get("has_edit")
    ]
    if expanded:
        raise RuntimeError(
            "检测到仍处于编辑状态的 section："
            + " | ".join(expanded)
            + "。请先人工 Save/Cancel 当前未保存内容，再运行完整验收。"
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


def _live_fields(
    adapter: MakroDomainAdapter,
    section_path: str,
    *,
    wait_ms: int,
    max_scroll_steps: int,
) -> list[dict[str, Any]]:
    controls = adapter.scan_section_fields(
        section_path,
        include_values=True,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    return [
        field
        for field in adapter.build_semantic_fields(controls)
        if is_listing_attribute_field(field)
    ]


def _fields_by_identity(fields: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    output: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for field in fields:
        output.setdefault(_field_identity(field), []).append(field)
    return output


def _base_result_payload(item: LiveFillPlanItem, mode: str | None) -> dict[str, Any]:
    return {
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
        "qualifier": item.resolution.qualifier,
        "confidence": item.resolution.confidence,
        "source_type": item.resolution.source_type,
        "source_reference": item.resolution.source_reference,
        "evidence": item.resolution.evidence,
    }


def _verify_saved_values(
    adapter: MakroDomainAdapter,
    candidates: list[LiveFillPlanItem],
    validated_identities: set[tuple[str, str, str]],
    section_title: str,
    *,
    include_review_candidates: bool,
    wait_ms: int,
    max_scroll_steps: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    section = adapter.find_section(section_title)
    if section is None:
        return [], ["Save 后找不到 section。"]
    adapter.open_section_for_edit(section)
    section = adapter.find_section(section_title) or section
    section_path = str(section.get("path") or "")
    if not section_path:
        return [], ["Save 后 section 缺少 DOM path。"]

    live = _fields_by_identity(
        _live_fields(
            adapter,
            section_path,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
    )
    verifications: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in candidates:
        identity = _item_identity(item)
        if identity not in validated_identities:
            continue
        matches = live.get(identity, [])
        if len(matches) != 1:
            errors.append(
                f"{item.label}: Save 后 live field 匹配数={len(matches)}，期望 1。"
            )
            continue
        answer = execution_answer_for_item(
            item,
            include_review_candidates=include_review_candidates,
        )
        verification = adapter.verify_resolved_field(
            matches[0],
            answer,
            section_path=section_path,
        )
        verifications.append(verification.as_dict())
        if verification.status != "persisted_verified":
            errors.append(f"{item.label}: {verification.detail}")

    errors.extend(adapter.visible_section_errors(section_path))
    return verifications, errors


def _fill_one_section(
    adapter: MakroDomainAdapter,
    plan: LiveFillPlan,
    section_title: str,
    *,
    include_review_candidates: bool,
    persist: bool,
    scroll_wait_ms: int,
    max_scroll_steps: int,
    recheck_wait_ms: int,
    run_dir: Path,
) -> dict[str, Any]:
    candidates = _section_candidates(
        plan,
        section_title,
        include_review_candidates=include_review_candidates,
    )
    report: dict[str, Any] = {
        "section": section_title,
        "candidate_count": len(candidates),
        "writes_attempted": 0,
        "validated": 0,
        "validation_failed": 0,
        "fill_error": 0,
        "skipped_existing": 0,
        "skipped_live_match": 0,
        "save_attempted": False,
        "saved": False,
        "persisted_verified": 0,
        "persisted_validation_failed": 0,
        "results": [],
        "persisted_verifications": [],
    }
    if not candidates:
        report["status"] = "no_candidates"
        return report

    section = adapter.find_section(section_title)
    if section is None:
        report["status"] = "section_not_found"
        return report
    adapter.open_section_for_edit(section)
    section = adapter.find_section(section_title) or section
    section_path = str(section.get("path") or "")
    if not section_path:
        report["status"] = "section_path_missing"
        return report

    live = _fields_by_identity(
        _live_fields(
            adapter,
            section_path,
            wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
    )
    validated_identities: set[tuple[str, str, str]] = set()

    for item in candidates:
        mode = preview_mode_for_item(
            item,
            include_review_candidates=include_review_candidates,
        )
        base_payload = _base_result_payload(item, mode)
        matches = live.get(_item_identity(item), [])
        if len(matches) != 1:
            report["skipped_live_match"] += 1
            report["results"].append(
                {
                    **base_payload,
                    "execution_status": "skipped_live_match",
                    "detail": f"展开 section 后 live field 匹配数={len(matches)}，期望恰好 1。",
                }
            )
            continue

        semantic_field = matches[0]
        if _has_existing_value(semantic_field):
            report["skipped_existing"] += 1
            report["results"].append(
                {
                    **base_payload,
                    "execution_status": "skipped_existing",
                    "detail": "当前控件已有非 placeholder 值；不覆盖。",
                }
            )
            continue

        answer = execution_answer_for_item(
            item,
            include_review_candidates=include_review_candidates,
        )
        report["writes_attempted"] += 1
        verification = adapter.fill_resolved_field(
            semantic_field,
            answer,
            section_path=section_path,
            recheck_wait_ms=recheck_wait_ms,
        )
        if verification.status == "validated":
            report["validated"] += 1
            validated_identities.add(_item_identity(item))
        elif verification.status == "fill_error":
            report["fill_error"] += 1
        else:
            report["validation_failed"] += 1
        report["results"].append(
            {
                **base_payload,
                "execution_status": verification.status,
                "verification": verification.as_dict(),
            }
        )

    before_save = run_dir / f"{_safe_name(section_title)}-before-save.png"
    adapter.page.screenshot(path=str(before_save), full_page=True)
    report["screenshot_before_save"] = str(before_save.resolve())

    if not persist:
        report["status"] = "preview_open"
        return report

    report["save_attempted"] = True
    try:
        adapter.save_section(section_title)
        report["saved"] = True
        persisted, errors = _verify_saved_values(
            adapter,
            candidates,
            validated_identities,
            section_title,
            include_review_candidates=include_review_candidates,
            wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        report["persisted_verifications"] = persisted
        report["persisted_verified"] = sum(
            1 for item in persisted if item.get("status") == "persisted_verified"
        )
        report["persisted_validation_failed"] = len(persisted) - report["persisted_verified"]
        report["post_save_errors"] = errors

        after_save = run_dir / f"{_safe_name(section_title)}-after-save-reopen.png"
        adapter.page.screenshot(path=str(after_save), full_page=True)
        report["screenshot_after_save"] = str(after_save.resolve())

        # Re-open was read-only. Cancel simply collapses the card; saved values remain.
        adapter.cancel_section(section_title)
        if errors or report["persisted_validation_failed"]:
            report["status"] = "persisted_validation_failed"
        else:
            report["status"] = "persisted_verified"
    except Exception as exc:
        report["status"] = "save_failed"
        report["save_error"] = str(exc)
        live_section = adapter.find_section(section_title)
        if live_section is not None and not live_section.get("has_edit"):
            path = str(live_section.get("path") or "")
            if path:
                report["visible_errors_after_save_failure"] = adapter.visible_section_errors(path)
            failed_shot = run_dir / f"{_safe_name(section_title)}-save-failed.png"
            adapter.page.screenshot(path=str(failed_shot), full_page=True)
            report["screenshot_save_failed"] = str(failed_shot.resolve())
            try:
                adapter.cancel_section(section_title)
                report["cancelled_unsaved_after_failure"] = True
            except Exception as cleanup_exc:
                report["cleanup_error"] = str(cleanup_exc)
    return report


def _run_photos(
    adapter: MakroDomainAdapter,
    image_paths: list[str],
    *,
    allow_save: bool,
    upload_timeout_ms: int,
    run_dir: Path,
) -> dict[str, Any]:
    if not image_paths:
        return {
            "status": "skipped",
            "detail": "没有传入 --upload-image；没有执行 Product Photos。",
        }

    staged = adapter.upload_product_photos(image_paths, timeout_ms=upload_timeout_ms)
    report = staged.as_dict()
    staged_shot = run_dir / "Product-Photos-staged.png"
    adapter.page.screenshot(path=str(staged_shot), full_page=True)
    report["screenshot_staged"] = str(staged_shot.resolve())
    report["save_attempted"] = False
    report["saved"] = False

    if not allow_save or staged.staged <= 0:
        return report

    report["save_attempted"] = True
    try:
        adapter.save_section(PRODUCT_PHOTOS)
        report["saved"] = True
        persistence = adapter.verify_persisted_photo_count(
            initial_count=staged.initial_count,
            expected_added=staged.staged,
        )
        report["persistence"] = persistence
        report["status"] = persistence["status"]

        # Re-open once so the final screenshot proves the saved photo remains.
        section = adapter.find_section(PRODUCT_PHOTOS)
        if section is not None:
            adapter.open_section_for_edit(section)
            reopened = adapter.inspect_product_photos()
            report["reopened_state"] = {
                "completion_count": reopened.get("completion_count"),
                "capacity": reopened.get("capacity"),
                "visible_image_count": reopened.get("visible_image_count"),
            }
            saved_shot = run_dir / "Product-Photos-after-save-reopen.png"
            adapter.page.screenshot(path=str(saved_shot), full_page=True)
            report["screenshot_after_save"] = str(saved_shot.resolve())
            adapter.cancel_section(PRODUCT_PHOTOS)
    except Exception as exc:
        report["status"] = "save_failed"
        report["save_error"] = str(exc)
        live = adapter.find_section(PRODUCT_PHOTOS)
        if live is not None and not live.get("has_edit"):
            path = str(live.get("path") or "")
            if path:
                report["visible_errors_after_save_failure"] = adapter.visible_section_errors(path)
            try:
                adapter.cancel_section(PRODUCT_PHOTOS)
                report["cancelled_unsaved_after_failure"] = True
            except Exception as cleanup_exc:
                report["cleanup_error"] = str(cleanup_exc)
    return report


def _totals(section_reports: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "candidate_count",
        "writes_attempted",
        "validated",
        "validation_failed",
        "fill_error",
        "skipped_existing",
        "skipped_live_match",
        "persisted_verified",
        "persisted_validation_failed",
    )
    return {
        key: sum(int(report.get(key) or 0) for report in section_reports)
        for key in keys
    }


def _completion_summary(
    section_reports: list[dict[str, Any]],
    photo_report: dict[str, Any] | None,
) -> dict[str, Any]:
    by_section = {str(item.get("section") or ""): item for item in section_reports}
    required_cards = (
        "Price, Stock and Shipping Information",
        "Product Description",
    )
    required_persisted = all(
        by_section.get(title, {}).get("status") == "persisted_verified"
        for title in required_cards
    )
    additional = by_section.get("Additional Description", {})
    additional_ok = additional.get("status") in {"persisted_verified", "no_candidates"}
    photos_ok = bool(
        photo_report
        and photo_report.get("status") == "persisted_verified"
        and int((photo_report.get("persistence") or {}).get("final_count") or 0) >= 1
    )
    return {
        "required_field_cards_persisted": required_persisted,
        "additional_description_ok": additional_ok,
        "photos_persisted": photos_ok,
        "step3_persisted_complete": required_persisted and additional_ok and photos_ok,
        "send_to_qc_allowed_by_this_runner": False,
    }


def main() -> int:
    args = build_parser().parse_args()
    if args.all_step3 and not args.allow_section_save:
        raise SystemExit(
            "--all-step3 是真实持久化验收，必须显式同时传 --allow-section-save；"
            "不再提供会自动 Cancel 丢值的假全量模式。"
        )
    if args.section and args.allow_section_save:
        raise SystemExit("--allow-section-save 只允许与 --all-step3 一起使用。")
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
            "不会自动启动、关闭或重启 Edge。"
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

        sections_payload, flat_controls, scan_stats = adapter.scan_sections(
            include_values=True,
            wait_ms=args.scroll_wait_ms,
            max_scroll_steps=args.max_scroll_steps,
        )
        semantic_fields = [
            field
            for field in adapter.build_semantic_fields(flat_controls)
            if is_listing_attribute_field(field)
        ]
        plan = build_live_fill_plan(
            catalog,
            semantic_fields,
            input_result.bundle,
            policy=policy,
            aliases=alias_config.aliases if alias_config else None,
        )

        print("===== MAKRO STEP 3 ACCEPTANCE =====" if args.all_step3 else "===== MAKRO SECTION PREVIEW =====")
        print(f"page={page.url}")
        summary = plan.summary()
        print(
            f"ready={summary['ready']}, preview_eligible={summary['preview_eligible']}, "
            f"blocked={summary['blocked']}, required_blocked={summary['required_blocked']}"
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
        completion = _completion_summary(section_reports, photo_report) if args.all_step3 else None
        final_screenshot = run_dir / "step3-final.png"
        page.screenshot(path=str(final_screenshot), full_page=True)
        payload = {
            "mode": "all_step3_persisted_acceptance" if args.all_step3 else "single_section_review",
            "page_url": page.url,
            "expected_vertical": args.expected_vertical,
            "include_review_candidates": args.include_review_candidates,
            "allow_section_save": args.allow_section_save,
            "plan_summary": summary,
            "scan": scan_stats,
            "sections": [item.get("title") for item in sections_payload],
            "section_reports": section_reports,
            "field_totals": totals,
            "photo_upload": photo_report,
            "completion": completion,
            "evidence_items": len(input_result.bundle.evidence),
            "evidence_warnings": input_result.warnings,
            "section_save_clicked": sum(1 for item in section_reports if item.get("save_attempted"))
            + int(bool(photo_report and photo_report.get("save_attempted"))),
            "send_to_qc_clicked": False,
            "browser_closed": False,
            "final_screenshot": str(final_screenshot.resolve()),
        }
        report_path = run_dir / "report.json"
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("\n===== ACCEPTANCE COMPLETE =====" if args.all_step3 else "\n===== PREVIEW READY =====")
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
            print(f"step3_persisted_complete={completion['step3_persisted_complete']}")
        print("Send to QC=False。")
        print(f"报告：{report_path.resolve()}")
        print(f"最终截图：{final_screenshot.resolve()}")

        harness.detach()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
