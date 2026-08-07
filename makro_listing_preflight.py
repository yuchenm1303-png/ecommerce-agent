"""One-command real Makro preflight for all three core form sections.

The command audits discovery completeness, functionally exercises every current
empty control, then opens all three core sections together, fills every current
empty semantic field, performs a listing-wide settled readback, and pauses for
human inspection. Only after the user presses Enter are all three sections
Cancelled and synthetic values cleared. It never clicks Save or Send to QC.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness
from app.makro import MAKRO_HOME_URL, is_listing_url, parse_makro_listing_url
from app.makro.coverage import run_section_coverage, summarize_results
from app.makro.domain import MakroDomainAdapter
from app.makro.listing_preflight import (
    CORE_FORM_SECTIONS,
    SectionPreflightResult,
    audit_section,
    page_has_coverage_residue,
    summarize_listing_preflight,
)
from app.makro.listing_visual_hold import (
    cleanup_all_visual_hold_sections,
    fill_all_core_sections_for_visual_hold,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Makro 三个核心表单 section 一次性最终 preflight：完整发现、功能 coverage、"
            "三栏同时填满并停住供人工检查，按 Enter 后统一 Cancel。绝不 Save / Send to QC。"
        )
    )
    parser.add_argument("--expected-vertical", required=True)
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--logs-dir", default="logs/makro-listing-preflight")
    parser.add_argument("--recheck-wait-ms", type=int, default=800)
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    return parser


def _assert_single_listing_tab(context: Any) -> int:
    listing_pages = [page for page in context.pages if is_listing_url(page.url)]
    if len(listing_pages) != 1:
        lines = [f"Add Listing tab 必须恰好 1 个，当前 {len(listing_pages)} 个。"]
        for page in listing_pages:
            try:
                target = parse_makro_listing_url(page.url)
                lines.append(
                    f"vertical={target.vertical!r}, brand={target.brand!r}, "
                    f"requestId={target.request_id!r}"
                )
            except Exception:
                lines.append(page.url)
        raise RuntimeError("\n".join(lines))
    return 1


def _assert_all_collapsed(adapter: MakroDomainAdapter) -> None:
    bad: list[str] = []
    for title in CORE_FORM_SECTIONS:
        section = adapter.find_section(title)
        if section is None:
            bad.append(f"{title}: not found")
        elif not section.get("has_edit"):
            bad.append(f"{title}: expanded / possibly unsaved")
    if bad:
        raise RuntimeError(
            "开始前三个 section 必须全部折叠，避免丢弃人工未保存内容：\n- "
            + "\n- ".join(bad)
        )


def _print_listing_progress(section: str, item: Any, index: int, total: int) -> None:
    mark = "PASS" if item.status == "pass" else item.status.upper()
    print(f"  {section} [{index:02d}/{total:02d}] {item.label or item.attribute_key}: {mark} {item.shape}")
    if item.status != "pass":
        print(f"      {item.detail}")


def main() -> int:
    args = build_parser().parse_args()
    logs_dir = Path(args.logs_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = logs_dir / f"makro-listing-preflight-{stamp}"
    evidence_dir = run_dir / "visual-evidence"
    run_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    print("===== MAKRO CORE LISTING PREFLIGHT =====")
    print("目标：一次运行，把三个核心 section 的当前空字段全部验证并最终同时留在页面供检查。")
    print("不会 Save，不会 Send to QC，不会关闭长期 Edge。")
    print(f"预期 vertical：{args.expected_vertical}")

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
        listing_tab_count = _assert_single_listing_tab(harness.context)
        adapter.assert_expected_vertical(args.expected_vertical)
        _assert_all_collapsed(adapter)
        if page_has_coverage_residue(page):
            raise RuntimeError("开始前页面已有 COVERAGE_ 测试值残留，请先清理。")

        print(f"操作标签页：{page.url}")

        audits: dict[str, Any] = {}
        functional_by_section: dict[str, list[Any]] = {}
        failures: list[str] = []

        # Phase 1: audit discovery completeness for all three sections.
        print("\n===== PHASE 1 / DISCOVERY AUDIT =====")
        for section_title in CORE_FORM_SECTIONS:
            audit = audit_section(
                adapter,
                section_title,
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
            )
            audits[section_title] = audit
            print(
                f"  {section_title}: semantic={audit.semantic_count}, "
                f"advertised={audit.advertised_total}, safe={audit.safe_to_test}"
            )
            if not audit.safe_to_test:
                failures.append(section_title)

        if failures:
            raise RuntimeError("discovery audit failed: " + ", ".join(failures))

        # Phase 2: exhaustive control capability coverage, including + slots.
        print("\n===== PHASE 2 / FUNCTIONAL COVERAGE =====")
        failures.clear()
        for section_title in CORE_FORM_SECTIONS:
            results = run_section_coverage(
                adapter,
                section_title,
                recheck_wait_ms=args.recheck_wait_ms,
                exercise_multi_value=True,
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
            )
            functional_by_section[section_title] = results
            summary = summarize_results(results)
            print(
                f"  {section_title}: {summary['passed']}/{summary['empty_field_attempts']} PASS; "
                f"failed/unsupported={summary['failed_or_unsupported']}; "
                f"existing skipped={summary['skipped_existing']}"
            )
            if summary["failed_or_unsupported"]:
                failures.append(section_title)

        if failures:
            raise RuntimeError(
                "functional coverage failed; 不进入最终 74-field visual hold: "
                + ", ".join(failures)
            )

        # Phase 3: the user-visible result they asked for: all three sections stay
        # expanded and all current empty fields stay populated simultaneously.
        print("\n===== PHASE 3 / WHOLE-LISTING VISUAL HOLD =====")
        visual_by_section: dict[str, list[Any]] = {}
        whole_screenshot: str | None = None
        cleanup_state: dict[str, bool] = {}
        interrupted = False

        try:
            visual_by_section = fill_all_core_sections_for_visual_hold(
                adapter,
                sections=CORE_FORM_SECTIONS,
                recheck_wait_ms=args.recheck_wait_ms,
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
                on_result=_print_listing_progress,
            )

            visual_failures: list[str] = []
            total_visual_attempts = 0
            total_visual_passed = 0
            for section_title in CORE_FORM_SECTIONS:
                summary = summarize_results(visual_by_section[section_title])
                total_visual_attempts += summary["empty_field_attempts"]
                total_visual_passed += summary["passed"]
                if summary["failed_or_unsupported"]:
                    visual_failures.append(section_title)
                print(
                    f"  {section_title}: visual {summary['passed']}/"
                    f"{summary['empty_field_attempts']} PASS; "
                    f"existing skipped={summary['skipped_existing']}"
                )
            if visual_failures:
                raise RuntimeError("whole-listing visual verification failed: " + ", ".join(visual_failures))

            image = evidence_dir / "all-three-core-sections-filled.png"
            page.screenshot(path=str(image), full_page=True)
            whole_screenshot = str(image.resolve())

            advertised_total = sum(audits[title].advertised_total or 0 for title in CORE_FORM_SECTIONS)
            semantic_total = sum(audits[title].semantic_count for title in CORE_FORM_SECTIONS)
            print("\n===== WHOLE-LISTING HOLD READY =====")
            print(
                f"三个 section 已同时保持展开。semantic discovered={semantic_total}/"
                f"{advertised_total}；当前空字段 visual={total_visual_passed}/"
                f"{total_visual_attempts} PASS。"
            )
            print("现在不要点 Save。你可以在 Edge 从上到下滚动，亲眼检查三个栏目里的测试值。")
            print(f"整页证据截图：{whole_screenshot}")
            try:
                input("检查完成后回到这个 PowerShell，只按 Enter；程序会统一 Cancel 三个 section 并清空测试值。")
            except KeyboardInterrupt:
                interrupted = True
                print("\n收到 Ctrl+C；正在统一安全 Cancel 三个 section……")
        finally:
            cleanup_state = cleanup_all_visual_hold_sections(adapter, CORE_FORM_SECTIONS)
            print("三个 core section 的 visual-hold 已统一 Cancel/清理。")

        # Final safety verification.
        _assert_all_collapsed(adapter)
        residue = page_has_coverage_residue(page)
        if residue:
            raise RuntimeError("最终安全复核仍发现 COVERAGE_ 残留。")

        section_results: list[SectionPreflightResult] = []
        for title in CORE_FORM_SECTIONS:
            section_results.append(
                SectionPreflightResult(
                    section=title,
                    audit=audits[title],
                    functional_results=functional_by_section[title],
                    visual_results=visual_by_section[title],
                    screenshot=whole_screenshot,
                    cleanup_clicked=bool(cleanup_state.get(title)),
                )
            )

        overall = summarize_listing_preflight(section_results)
        payload = {
            "mode": "core_listing_one_shot_whole_visual_hold",
            "page_url": page.url,
            "expected_vertical": args.expected_vertical,
            "listing_tab_count": listing_tab_count,
            "core_sections": list(CORE_FORM_SECTIONS),
            "results": [item.as_dict() for item in section_results],
            "overall": overall,
            "whole_listing_screenshot": whole_screenshot,
            "visual_hold_interrupted": interrupted,
            "cleanup_state": cleanup_state,
            "all_sections_collapsed_after_cleanup": True,
            "coverage_residue_after_cleanup": residue,
            "save_clicked": False,
            "send_to_qc_clicked": False,
            "browser_closed": False,
        }
        report = run_dir / "report.json"
        report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        print("\n===== FINAL =====")
        print(
            f"sections: {overall['sections_passed']}/{overall['sections']} PASS; "
            f"semantic discovered={overall['semantic_total']}/"
            f"{overall['advertised_total']}; "
            f"functional={overall['functional_passed']}/"
            f"{overall['functional_empty_attempts']} PASS; "
            f"visual={overall['visual_passed']}/"
            f"{overall['visual_empty_attempts']} PASS."
        )
        print(f"报告：{report.resolve()}")
        print("三个 section 已全部 Cancel/折叠；无 COVERAGE_ 残留。")
        print("Save=false；Send to QC=false；长期 Edge 继续保持打开。")
        harness.detach()

    success = (
        overall["sections"] == len(CORE_FORM_SECTIONS)
        and overall["all_sections_passed"]
        and overall["semantic_total"] == overall["advertised_total"]
        and overall["functional_failed_or_unsupported"] == 0
        and overall["visual_failed_or_unsupported"] == 0
    )
    if success:
        print("BROWSER EXECUTION LAYER COMPLETE: 可以停止 synthetic fill 测试并进入下一阶段。")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
