"""Execute validated AI field decisions against Makro Step 3.

There are only two execution modes:

- ``--section``: one-card diagnostic preview. It never saves and leaves the card
  open for human inspection.
- ``--all-step3 --allow-section-save``: persisted Step 3 acceptance. It uses the
  exact read-only-planned live schema and AI decision packet, fills each core
  card, saves it, re-opens it and verifies persisted values. Product Photos is
  staged, saved and verified separately when validated upload images are supplied.

Before any browser write, the decision packet is rebound to the exact current
product sources and the current Makro schema must match the planning schema.
This command never clicks Send to QC. Evidence images (``--image``) and listing
upload images (``--upload-image``) remain deliberately separate.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.ai_decisions import load_ai_decision_packet
from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness, is_cdp_ready
from app.fill_plan import BLOCKED, LiveFillPlan, LiveFillPlanItem, build_live_fill_plan
from app.live_schema import assert_live_schema_matches, load_live_schema
from app.makro import MAKRO_HOME_URL, base_section_title, is_listing_url
from app.makro.direct_visual_hold import is_listing_attribute_field
from app.makro.domain import MakroDomainAdapter
from app.makro.listing_preflight import CORE_FORM_SECTIONS
from app.product_context import build_ai_product_context
from app.qa_catalog import load_question_catalog
from app.resolver_inputs import ResolutionInputSpec
from app.review_preview import execution_answer_for_item, preview_mode_for_item
from app.semantic_grounding import build_grounding_catalog
from app.source_bundle import normalize_key

PRODUCT_PHOTOS = "Product Photos"
DIAGNOSTIC_SCREENSHOT_TIMEOUT_MS = 5_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "使用已验证 AI field decisions 检查/持久化 Makro Step 3；"
            "单 section 为 no-save 预览，全量模式必须显式授权 section Save；"
            "永不 Send to QC。"
        )
    )
    parser.add_argument("--qa", required=True)
    parser.add_argument("--decision-packet", required=True, help="makro_resolve_ai.py 生成的 ai-decisions.json")
    parser.add_argument("--live-schema", required=True, help="生成该 decision packet 时使用的 live-schema.json")
    parser.add_argument("--sku", default="")
    parser.add_argument("--expected-model", default="")
    parser.add_argument("--expected-brand", default="")
    parser.add_argument("--product-table", default=None)
    parser.add_argument("--facts-json", action="append", default=[])
    parser.add_argument("--supplier-snapshot", action="append", default=[])
    parser.add_argument("--official-snapshot", action="append", default=[])
    parser.add_argument("--supplemental-text", default="")
    parser.add_argument("--supplemental-text-file", default=None)
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="AI evidence image；只用于 decision packet rebind，不会自动上传到 Makro。",
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
        help="执行完整 Step 3 persistence acceptance；必须同时传 --allow-section-save。",
    )
    parser.add_argument(
        "--allow-section-save",
        action="store_true",
        help="允许保存 Step 3 草稿卡片；只对 --all-step3 有效，不允许 Send to QC。",
    )
    parser.add_argument(
        "--include-review-candidates",
        action="store_true",
        help=(
            "显式把 AI 标记 REVIEW 且通过硬控件约束的候选放进当前人工验收 draft；"
            "它们仍不是 autofill-safe。"
        ),
    )
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    parser.add_argument("--recheck-wait-ms", type=int, default=800)
    parser.add_argument("--upload-timeout-ms", type=int, default=8_000)
    parser.add_argument("--max-text-chars", type=int, default=5000)
    parser.add_argument("--overlap-chars", type=int, default=250)
    parser.add_argument("--output-dir", default="logs/makro-review-preview")
    return parser


def _input_spec(args: argparse.Namespace) -> ResolutionInputSpec:
    return ResolutionInputSpec(
        sku=args.sku,
        expected_model=args.expected_model,
        expected_brand=args.expected_brand,
        product_table=args.product_table,
        facts_json=tuple(args.facts_json),
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
    """Reject only a structurally incomplete Step 3 page.

    Expanded/collapsed card state is presentation state, not evidence of unsaved
    or unsafe data.  Real execution safety is already enforced by target ownership,
    vertical/schema binding and post-write persistence verification, so this gate
    must never stop progress merely because a card is already open.
    """

    missing = [
        title
        for title in CORE_FORM_SECTIONS
        if adapter.find_section(title) is None
    ]
    if missing:
        raise RuntimeError(
            "当前页面缺少 Step 3 核心 section：" + " | ".join(missing)
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


def _fields_by_identity(
    fields: list[dict[str, Any]],
) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    output: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for field in fields:
        output.setdefault(_field_identity(field), []).append(field)
    return output


def _base_result_payload(item: LiveFillPlanItem, mode: str | None) -> dict[str, Any]:
    record = item.resolution
    return {
        "attribute_key": item.attribute_key,
        "label": item.label,
        "question_number": item.question_number,
        "question": item.question,
        "match_basis": item.match_basis,
        "preview_mode": mode,
        "original_status": record.status,
        "eligible_for_autofill": record.eligible_for_autofill,
        "preview_eligible": record.preview_eligible,
        "gate_reason": record.gate_reason,
        "answer": record.answer,
        "answer_values": record.answer_values,
        "qualifier": record.qualifier,
        "confidence": record.confidence,
        "source_type": record.source_type,
        "source_reference": record.source_reference,
        "evidence": record.evidence,
    }


def _open_and_index_section(
    adapter: MakroDomainAdapter,
    section_title: str,
    *,
    wait_ms: int,
    max_scroll_steps: int,
) -> tuple[str, dict[tuple[str, str, str], list[dict[str, Any]]]]:
    section = adapter.find_section(section_title)
    if section is None:
        raise RuntimeError(f"当前页面找不到 section：{section_title}")
    adapter.open_section_for_edit(section)
    section = adapter.find_section(section_title) or section
    section_path = str(section.get("path") or "")
    if not section_path:
        raise RuntimeError(f"section {section_title!r} 缺少稳定 DOM path。")
    fields = _live_fields(
        adapter,
        section_path,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    return section_path, _fields_by_identity(fields)


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
    section_path, live = _open_and_index_section(
        adapter,
        section_title,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    verifications: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in candidates:
        identity = _item_identity(item)
        if identity not in validated_identities:
            continue
        mode = preview_mode_for_item(
            item,
            include_review_candidates=include_review_candidates,
        )
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
        payload = verification.as_dict()
        payload["preview_mode"] = mode
        payload["question_number"] = item.question_number
        payload["question"] = item.question
        verifications.append(payload)
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
    """Delegate to the one canonical Step 3 execution policy.

    Local import avoids a module-initialization cycle because the canonical
    execution module intentionally imports the pure helper functions above.
    """

    from app.makro.execution import fill_one_section

    return fill_one_section(
        adapter,
        plan,
        section_title,
        include_review_candidates=include_review_candidates,
        persist=persist,
        scroll_wait_ms=scroll_wait_ms,
        max_scroll_steps=max_scroll_steps,
        recheck_wait_ms=recheck_wait_ms,
        run_dir=run_dir,
    )


def _run_photos(
    adapter: MakroDomainAdapter,
    image_paths: list[str],
    *,
    allow_save: bool,
    upload_timeout_ms: int,
    run_dir: Path,
) -> dict[str, Any]:
    """Delegate Product Photos to the canonical submission-boundary policy."""

    from app.makro.execution import run_photos

    return run_photos(
        adapter,
        image_paths,
        allow_save=allow_save,
        upload_timeout_ms=upload_timeout_ms,
        run_dir=run_dir,
    )


def _totals(section_reports: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "candidate_count",
        "writes_attempted",
        "review_candidates_attempted",
        "validated",
        "validation_failed",
        "fill_error",
        "skipped_existing",
        "skipped_live_match",
        "optional_prewrite_skipped",
        "persisted_verified",
        "review_candidates_persisted",
        "persisted_validation_failed",
    )
    return {
        key: sum(int(report.get(key) or 0) for report in section_reports)
        for key in keys
    }


def _blocked_reason_summary(plan: LiveFillPlan) -> dict[str, int]:
    output: dict[str, int] = {}
    for item in plan.items:
        if item.action != BLOCKED:
            continue
        reason = item.resolution.gate_reason or f"resolver_{item.resolution.status}"
        output[reason] = output.get(reason, 0) + 1
    return dict(sorted(output.items()))


def _completion_summary(
    section_reports: list[dict[str, Any]],
    photo_report: dict[str, Any] | None,
    plan_summary: dict[str, Any],
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

    photos_requested = bool(
        photo_report and int(photo_report.get("requested") or 0) > 0
    )
    photos_persisted = bool(
        photo_report
        and photo_report.get("status") == "persisted_verified"
        and int((photo_report.get("persistence") or {}).get("final_count") or 0) >= 1
    )
    photo_requirement_satisfied = bool(
        photo_report and (not photos_requested or photos_persisted)
    )
    draft_persisted_complete = (
        required_persisted and additional_ok and photo_requirement_satisfied
    )
    review_persisted = sum(
        int(report.get("review_candidates_persisted") or 0)
        for report in section_reports
    )
    required_blocked = int(plan_summary.get("required_blocked") or 0)
    return {
        "required_field_cards_persisted": required_persisted,
        "additional_description_ok": additional_ok,
        "photos_requested": photos_requested,
        "photos_persisted": photos_persisted,
        "photo_requirement_satisfied": photo_requirement_satisfied,
        "review_candidates_persisted": review_persisted,
        "required_blocked": required_blocked,
        "draft_persisted_complete": draft_persisted_complete,
        "autofill_safe_complete": (
            draft_persisted_complete
            and review_persisted == 0
            and required_blocked == 0
        ),
        "send_to_qc_allowed_by_this_runner": False,
    }


def _capture_optional_screenshot(page: Any, path: Path) -> dict[str, str]:
    """Keep presentation evidence outside the business acceptance contract."""

    try:
        page.screenshot(
            path=str(path),
            full_page=True,
            timeout=DIAGNOSTIC_SCREENSHOT_TIMEOUT_MS,
        )
        return {"status": "captured", "path": str(path.resolve()), "error": ""}
    except Exception as exc:
        return {"status": "failed", "path": "", "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    args = build_parser().parse_args()
    if args.all_step3 and not args.allow_section_save:
        raise SystemExit(
            "--all-step3 是真实持久化验收，必须显式同时传 --allow-section-save；"
            "不再提供会自动 Cancel 丢值的假全量模式。"
        )
    if args.section and args.allow_section_save:
        raise SystemExit("--allow-section-save 只允许与 --all-step3 一起使用。")
    if args.upload_timeout_ms <= 0:
        raise SystemExit("--upload-timeout-ms 必须大于 0")
    if args.max_text_chars < 500:
        raise SystemExit("--max-text-chars 不能小于 500")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_text_chars:
        raise SystemExit("--overlap-chars 必须 >=0 且小于 --max-text-chars")
    if not is_cdp_ready(args.cdp_port):
        raise RuntimeError(
            f"长期 Makro Edge 的 CDP 127.0.0.1:{args.cdp_port} 不可达；"
            "不会自动启动、关闭或重启 Edge。"
        )

    customer_catalog = load_question_catalog(args.qa)
    planned_live_fields = load_live_schema(args.live_schema)
    spec = _input_spec(args)
    product_context = build_ai_product_context(customer_catalog, spec)
    grounding = build_grounding_catalog(
        image_paths=args.image,
        supplier_snapshots=args.supplier_snapshot,
        official_snapshots=args.official_snapshot,
        supplemental_text=product_context.text,
        max_text_chars=args.max_text_chars,
        overlap_chars=args.overlap_chars,
    )
    decision_packet = load_ai_decision_packet(
        args.decision_packet,
        planned_live_fields,
        grounding,
        expected_identity=product_context.trusted_inputs.expected_identity,
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

        assert_live_schema_matches(planned_live_fields, semantic_fields)
        plan = build_live_fill_plan(
            decision_packet,
            semantic_fields,
            product_context.trusted_inputs.bundle,
        )

        summary = plan.summary()
        print(
            "===== MAKRO STEP 3 ACCEPTANCE ====="
            if args.all_step3
            else "===== MAKRO SECTION PREVIEW ====="
        )
        print(f"page={page.url}")
        print(
            f"live_fields={summary['live_field_count']}, ready={summary['ready']}, "
            f"preview_eligible={summary['preview_eligible']}, blocked={summary['blocked']}, "
            f"required_blocked={summary['required_blocked']}"
        )
        print(f"live_schema={Path(args.live_schema).resolve()} verified=True")
        print(f"decision_packet={Path(args.decision_packet).resolve()} rebound=True")
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
        final_screenshot_capture = _capture_optional_screenshot(page, final_screenshot)
        payload = {
            "mode": "all_step3_persisted_acceptance" if args.all_step3 else "single_section_review",
            "page_url": page.url,
            "expected_vertical": args.expected_vertical,
            "qa_source": str(Path(args.qa).resolve()),
            "decision_packet": str(Path(args.decision_packet).resolve()),
            "live_schema": str(Path(args.live_schema).resolve()),
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
            "trusted_input_items": len(product_context.trusted_inputs.bundle.evidence),
            "grounded_source_count": len(grounding.sources),
            "grounding_warnings": list(grounding.warnings),
            "decision_warnings": decision_packet.warnings,
            "section_save_attempted": sum(
                1 for item in section_reports if item.get("save_attempted")
            ) + int(bool(photo_report and photo_report.get("save_attempted"))),
            "section_saved": sum(
                1 for item in section_reports if item.get("saved")
            ) + int(bool(photo_report and photo_report.get("saved"))),
            "send_to_qc_clicked": False,
            "browser_closed": False,
            "final_screenshot": final_screenshot_capture.get("path", ""),
            "final_screenshot_capture": final_screenshot_capture,
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
            f"review_persisted={totals['review_candidates_persisted']}, "
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
        if final_screenshot_capture.get("status") == "captured":
            print(f"最终截图：{final_screenshot.resolve()}")
        else:
            print("最终截图：未生成；截图属于辅助诊断，不影响业务验收。")

        harness.detach()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
