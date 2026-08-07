"""One-command real Makro preflight for all three core form sections.

The command audits discovery completeness, exercises every current empty control,
creates visual evidence with all successful values coexisting inside each section,
then Cancels everything. It never clicks Save or Send to QC.
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
from app.makro.domain import MakroDomainAdapter
from app.makro.listing_preflight import (
    CORE_FORM_SECTIONS,
    page_has_coverage_residue,
    run_core_section_preflight,
    summarize_listing_preflight,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Makro 三个核心表单 section 一次性最终 preflight：完整发现、功能 coverage、"
            "整栏同时填满截图证明、Cancel 清理。绝不 Save / Send to QC。"
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


def main() -> int:
    args = build_parser().parse_args()
    logs_dir = Path(args.logs_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = logs_dir / f"makro-listing-preflight-{stamp}"
    evidence_dir = run_dir / "visual-evidence"
    run_dir.mkdir(parents=True, exist_ok=True)

    print("===== MAKRO CORE LISTING PREFLIGHT =====")
    print("目标：一次性封板三个核心表单 section 的浏览器执行层。")
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
        results = []
        failures: list[str] = []

        for index, section_title in enumerate(CORE_FORM_SECTIONS, start=1):
            print(f"\n[{index}/3] ===== {section_title} =====")
            try:
                result = run_core_section_preflight(
                    adapter,
                    section_title,
                    evidence_dir=evidence_dir,
                    recheck_wait_ms=args.recheck_wait_ms,
                    wait_ms=args.scroll_wait_ms,
                    max_scroll_steps=args.max_scroll_steps,
                )
                results.append(result)
                f = result.functional_summary
                v = result.visual_summary
                print(
                    f"发现 {result.audit.semantic_count}/"
                    f"{result.audit.advertised_total or '?'} semantic fields；"
                    f"functional {f['passed']}/{f['empty_field_attempts']} PASS，"
                    f"visual {v['passed']}/{v['empty_field_attempts']} PASS，"
                    f"existing skipped={f['skipped_existing']}。"
                )
                if result.screenshot:
                    print(f"视觉证据：{result.screenshot}")
                if not result.passed:
                    failures.append(section_title)
            except Exception as exc:
                failures.append(section_title)
                print(f"SECTION FAILED: {exc}")
                # Continue to the next section only if the page is safely collapsed.
                section = adapter.find_section(section_title)
                if section is not None and not section.get("has_edit"):
                    raise RuntimeError(
                        f"{section_title} 失败后仍展开；为保护真实 listing 已停止。"
                    ) from exc

        for title in CORE_FORM_SECTIONS:
            section = adapter.find_section(title)
            if section is None or not section.get("has_edit"):
                raise RuntimeError(f"最终安全复核：{title} 未恢复折叠。")
        residue = page_has_coverage_residue(page)
        if residue:
            raise RuntimeError("最终安全复核仍发现 COVERAGE_ 残留。")

        overall = summarize_listing_preflight(results)
        payload = {
            "mode": "core_listing_one_shot_preflight",
            "page_url": page.url,
            "expected_vertical": args.expected_vertical,
            "listing_tab_count": listing_tab_count,
            "core_sections": list(CORE_FORM_SECTIONS),
            "results": [item.as_dict() for item in results],
            "overall": overall,
            "failures": failures,
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
        print(f"截图目录：{evidence_dir.resolve()}")
        print("三个 section 已全部 Cancel/折叠；无 COVERAGE_ 残留。")
        print("Save=false；Send to QC=false；长期 Edge 继续保持打开。")
        harness.detach()

    success = (
        len(results) == len(CORE_FORM_SECTIONS)
        and not failures
        and overall["all_sections_passed"]
        and overall["semantic_total"] == overall["advertised_total"]
    )
    if success:
        print("BROWSER EXECUTION LAYER COMPLETE: 可以停止 synthetic fill 测试并进入下一阶段。")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
