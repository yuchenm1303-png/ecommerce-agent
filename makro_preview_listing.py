"""Preview grounded real product answers in one Makro section without saving.

This command is the bridge between the read-only Fill Plan and the browser
execution layer. It intentionally keeps the autofill trust gate unchanged:

- READY items may be filled for inspection;
- NEEDS_REVIEW items may be filled only when the resolver explicitly marks them
  ``preview_eligible`` (low confidence is the sole blocking gate) and the caller
  opts in with ``--include-review-candidates``;
- conflict / missing / field-constraint / unmatched-live-field candidates are
  never preview-filled;
- existing non-empty controls are never overwritten;
- only one section is opened per run;
- Save and Send to QC are never clicked, and the section is deliberately left
  open so the user can inspect the real values in the long-lived Edge window.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.alias_config import load_alias_config
from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness
from app.fill_plan import build_live_fill_plan
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
            "把 Fill Plan 中可执行的真实商品答案临时填入一个 Makro section 供人工检查；"
            "绝不 Save / Send to QC。"
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
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--product-url", default=None)
    parser.add_argument("--expected-vertical", required=True)
    parser.add_argument("--section", required=True, help="本次只预览填写一个 section。")
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
            "显式允许把 preview_eligible 的 needs_review 候选临时填进页面供人工检查。"
            "不会改变其 needs_review 状态，也不会允许 Save。"
        ),
    )
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    parser.add_argument("--recheck-wait-ms", type=int, default=800)
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
    expanded: list[str] = []
    for title in CORE_FORM_SECTIONS:
        section = adapter.find_section(title)
        if section is not None and not section.get("has_edit"):
            expanded.append(title)
    if expanded:
        raise RuntimeError(
            "检测到仍处于编辑状态的 section："
            + " | ".join(expanded)
            + "。review preview 不会 Cancel/覆盖现有人工编辑，请先人工处理后重试。"
        )


def _section_matches(section_heading: str, requested: str) -> bool:
    return base_section_title(section_heading) == base_section_title(requested)


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


def _item_identity(item: Any) -> tuple[str, str, str]:
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


def main() -> int:
    args = build_parser().parse_args()
    for name, value in (
        ("auto-fill-min-confidence", args.auto_fill_min_confidence),
        ("ai-auto-fill-min-confidence", args.ai_auto_fill_min_confidence),
    ):
        if not 0.0 <= value <= 1.0:
            raise SystemExit(f"--{name} 必须在 0..1")

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
    target_section = base_section_title(args.section)

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
        page = harness.page
        page.set_default_timeout(15_000)
        adapter = MakroDomainAdapter(page)

        if not adapter.is_listing_page():
            adapter.wait_for_authenticated_listing(
                MAKRO_HOME_URL,
                headless=False,
                navigate_first=harness.launched_now,
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

        candidates = [
            item
            for item in plan.items
            if _section_matches(item.section_heading, target_section)
            and preview_mode_for_item(
                item,
                include_review_candidates=args.include_review_candidates,
            )
            is not None
        ]
        if not candidates:
            report = {
                "mode": "grounded_review_preview",
                "page_url": page.url,
                "expected_vertical": args.expected_vertical,
                "target_section": target_section,
                "include_review_candidates": args.include_review_candidates,
                "plan_summary": plan.summary(),
                "candidate_count": 0,
                "writes_attempted": 0,
                "save_clicked": False,
                "send_to_qc_clicked": False,
                "section_left_open": False,
            }
            report_path = run_dir / "report.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print("没有符合当前 preview policy 的候选字段；页面未修改。")
            print(f"报告：{report_path.resolve()}")
            return 0

        section = adapter.find_section(target_section)
        if section is None:
            raise RuntimeError(f"当前页面找不到 section：{target_section}")
        adapter.open_section_for_edit(section)
        section = adapter.find_section(target_section) or section
        section_path = str(section.get("path") or "")
        if not section_path:
            raise RuntimeError(f"section {target_section!r} 缺少稳定 DOM path；拒绝填写。")

        fresh_controls = adapter.scan_section_fields(
            section_path,
            include_values=True,
            wait_ms=args.scroll_wait_ms,
            max_scroll_steps=args.max_scroll_steps,
        )
        fresh_fields = [
            field
            for field in adapter.build_semantic_fields(fresh_controls)
            if is_listing_attribute_field(field)
        ]
        fields_by_identity: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for field in fresh_fields:
            fields_by_identity.setdefault(_field_identity(field), []).append(field)

        results: list[dict[str, Any]] = []
        writes_attempted = 0
        validated = 0
        skipped_existing = 0
        skipped_missing_live_field = 0

        print("===== MAKRO GROUNDED REVIEW PREVIEW =====")
        print(f"section={target_section}")
        print(f"include_review_candidates={args.include_review_candidates}")
        print("不会 Save，不会 Send to QC；完成后 section 保持展开供人工检查。")

        for item in candidates:
            mode = preview_mode_for_item(
                item,
                include_review_candidates=args.include_review_candidates,
            )
            matches = fields_by_identity.get(_item_identity(item), [])
            base_payload = {
                "attribute_key": item.attribute_key,
                "label": item.label,
                "section_heading": item.section_heading,
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
                skipped_missing_live_field += 1
                results.append(
                    {
                        **base_payload,
                        "execution_status": "skipped_live_match",
                        "detail": f"展开 section 后 live field 匹配数={len(matches)}，期望恰好 1。",
                    }
                )
                print(f"  SKIP {item.label}: 展开后 live field 匹配数={len(matches)}")
                continue

            semantic_field = matches[0]
            if _has_existing_value(semantic_field):
                skipped_existing += 1
                results.append(
                    {
                        **base_payload,
                        "execution_status": "skipped_existing",
                        "detail": "当前控件已有值；review preview 禁止覆盖。",
                    }
                )
                print(f"  SKIP {item.label}: existing value")
                continue

            execution_answer = execution_answer_for_item(
                item,
                include_review_candidates=args.include_review_candidates,
            )
            writes_attempted += 1
            verification = adapter.fill_resolved_field(
                semantic_field,
                execution_answer,
                section_path=section_path,
                recheck_wait_ms=args.recheck_wait_ms,
            )
            if verification.status == "validated":
                validated += 1
            results.append(
                {
                    **base_payload,
                    "execution_status": verification.status,
                    "verification": verification.as_dict(),
                }
            )
            print(
                f"  {item.label}: {verification.status} | mode={mode} | "
                f"conf={item.resolution.confidence:.2f} | {item.resolution.source_type}"
            )

        screenshot = run_dir / "preview-after-fill.png"
        page.screenshot(path=str(screenshot), full_page=True)
        report = {
            "mode": "grounded_review_preview",
            "page_url": page.url,
            "expected_vertical": args.expected_vertical,
            "target_section": target_section,
            "include_review_candidates": args.include_review_candidates,
            "plan_summary": plan.summary(),
            "scan": scan_stats,
            "sections": [item.get("title") for item in sections_payload],
            "candidate_count": len(candidates),
            "writes_attempted": writes_attempted,
            "validated": validated,
            "skipped_existing": skipped_existing,
            "skipped_missing_live_field": skipped_missing_live_field,
            "results": results,
            "save_clicked": False,
            "send_to_qc_clicked": False,
            "section_left_open": True,
            "browser_closed": False,
            "screenshot": str(screenshot.resolve()),
        }
        report_path = run_dir / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        print("\n===== REVIEW PREVIEW READY =====")
        print(
            f"candidate={len(candidates)}, attempted={writes_attempted}, "
            f"validated={validated}, existing={skipped_existing}."
        )
        print("页面未 Save / Send to QC，当前 section 保持展开。请直接去 Edge 检查真实填写内容。")
        print(f"报告：{report_path.resolve()}")
        print(f"截图：{screenshot.resolve()}")

        # The Edge process is external and long-lived. Never browser.close(),
        # context.close(), Save, Cancel or Send to QC here; leave the exact
        # review state for the human inspector.
        harness.detach()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
