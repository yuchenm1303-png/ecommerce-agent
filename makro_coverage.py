"""Real Makro synthetic control-coverage runner.

Purpose: prove that every currently empty non-file field can be operated by the
browser layer. Synthetic values are never saved; each field attempt ends with
Cancel and the long-lived Edge remains open.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness
from app.makro import MAKRO_HOME_URL, base_section_title, is_listing_url, parse_makro_listing_url
from app.makro.coverage import PASS, CoverageResult, run_section_coverage, summarize_results
from app.makro.domain import MakroDomainAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Makro 全控件覆盖测试：只对当前空字段写入合成测试值，逐字段回读并 Cancel；"
            "绝不 Save / Send to QC。"
        )
    )
    parser.add_argument(
        "--expected-vertical",
        required=True,
        help="安全门：必须与当前 Add Listing URL 的 vertical 完全一致。",
    )
    parser.add_argument(
        "--section",
        action="append",
        default=[],
        help="要测试的 section，可重复传入。默认 Additional Description。",
    )
    parser.add_argument(
        "--all-sections",
        action="store_true",
        help="测试当前页面全部非 Product Photos section。图片上传另做独立 coverage。",
    )
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--logs-dir", default="logs/makro-coverage")
    parser.add_argument("--recheck-wait-ms", type=int, default=800)
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    parser.add_argument(
        "--no-multi-value",
        action="store_true",
        help="只测第一个槽位，不测试字段右侧 + 新增第二槽。默认会测试 +。",
    )
    return parser


def _assert_single_listing_tab(context: Any) -> int:
    listing_pages = [page for page in context.pages if is_listing_url(page.url)]
    if len(listing_pages) <= 1:
        return len(listing_pages)
    print("发现多个 Add a Single Listing 标签页；coverage test 不会猜目标：")
    for index, page in enumerate(listing_pages):
        try:
            target = parse_makro_listing_url(page.url)
            info = (
                f"vertical={target.vertical!r}, brand={target.brand!r}, "
                f"requestId={target.request_id!r}"
            )
        except ValueError:
            info = "无法解析 listing target"
        print(f"  tab {index}: {info}")
        print(f"    {page.url}")
    raise RuntimeError("请先关闭多余 Add Listing 标签页后再运行。")


def _target_sections(adapter: MakroDomainAdapter, args: argparse.Namespace) -> list[str]:
    if args.all_sections:
        titles: list[str] = []
        for section in adapter.find_sections():
            title = base_section_title(str(section.get("title") or ""))
            if not title or title.casefold() == "product photos":
                continue
            if title not in titles:
                titles.append(title)
        return titles
    return [base_section_title(item) for item in (args.section or ["Additional Description"])]


def _print_result(item: CoverageResult) -> None:
    mark = "PASS" if item.status == PASS else item.status.upper()
    plus = " +slot" if item.plus_available else ""
    print(f"  {item.label or item.attribute_key}: {mark}  {item.shape}{plus}")
    if item.status != PASS:
        print(f"    {item.detail}")


def main() -> int:
    args = build_parser().parse_args()
    profile_dir = Path(args.profile_dir).resolve()
    logs_dir = Path(args.logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"user_data_dir：{profile_dir}")
    print(f"长期 Edge CDP：127.0.0.1:{args.cdp_port}")
    print("安全模式：synthetic coverage；逐字段测试后只点 Cancel，不会保存。")
    print(f"预期 vertical：{args.expected_vertical}")

    with sync_playwright() as playwright:
        harness = EdgeHarness(
            playwright,
            profile_dir=profile_dir,
            port=args.cdp_port,
            start_url=MAKRO_HOME_URL,
        )
        page = harness.page
        page.set_default_timeout(15_000)
        adapter = MakroDomainAdapter(page)

        if harness.launched_now:
            print("已启动长期 Makro Edge；后续仍复用同一浏览器。")
        else:
            print("已连接现有 Makro Edge；不会新开浏览器。")

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
        print(f"操作标签页：{page.url}")

        sections = _target_sections(adapter, args)
        if not sections:
            raise RuntimeError("没有发现可测试的非图片 section。")

        all_results: list[CoverageResult] = []
        section_payloads: list[dict[str, Any]] = []
        for section_title in sections:
            print(f"\n===== {section_title} =====")
            results = run_section_coverage(
                adapter,
                section_title,
                recheck_wait_ms=args.recheck_wait_ms,
                exercise_multi_value=not args.no_multi_value,
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
            )
            for item in results:
                _print_result(item)
            summary = summarize_results(results)
            section_payloads.append(
                {
                    "section": section_title,
                    "summary": summary,
                    "results": [item.as_dict() for item in results],
                }
            )
            all_results.extend(results)
            print(
                f"section 结果：{summary['passed']}/{summary['empty_field_attempts']} empty fields PASS; "
                f"existing skipped={summary['skipped_existing']}"
            )

        overall = summarize_results(all_results)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = logs_dir / f"makro-coverage-{stamp}.json"
        payload = {
            "mode": "synthetic_control_coverage",
            "browser_session": "single_edge_cdp",
            "cdp_port": args.cdp_port,
            "page_url": page.url,
            "expected_vertical": args.expected_vertical,
            "listing_tab_count": listing_tab_count,
            "sections": section_payloads,
            "overall": overall,
            "save_clicked": False,
            "send_to_qc_clicked": False,
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        print("\n===== OVERALL =====")
        print(
            f"{overall['passed']}/{overall['empty_field_attempts']} empty fields PASS; "
            f"failed/unsupported={overall['failed_or_unsupported']}; "
            f"existing skipped={overall['skipped_existing']}"
        )
        print(f"日志：{output.resolve()}")
        print("脚本结束后长期 Edge 继续保持打开。没有 Save / Send to QC。")
        harness.detach()

    return 0 if overall["failed_or_unsupported"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
