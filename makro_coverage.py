"""Real Makro synthetic control-coverage runner.

Two safe modes are available:

- normal coverage: one empty field per open -> exercise -> Cancel transaction;
- visual hold: open one section once, fill every current empty field, verify all
  values coexist, then pause for human inspection before a final Cancel.

Neither mode clicks Save or Send to QC. The long-lived Edge remains open.
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
from app.makro.visual_hold import cleanup_visual_hold_section, fill_section_for_visual_hold


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Makro 全控件覆盖测试：用合成测试值验证真实控件；"
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
    parser.add_argument(
        "--visual-hold",
        action="store_true",
        help=(
            "视觉确认模式：只打开一个 section 一次，把当前所有空 semantic fields "
            "同时填上并保持页面不动；用户检查后回终端按 Enter，再统一 Cancel。"
        ),
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
        help=(
            "普通 coverage 只测第一个槽位，不测试字段右侧 +。"
            "visual-hold 固定不创建额外 + 槽位，因为目标是同时展示现有空字段。"
        ),
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


def _print_visual_progress(item: CoverageResult, index: int, total: int) -> None:
    mark = "PASS" if item.status == PASS else item.status.upper()
    print(f"  [{index:02d}/{total:02d}] {item.label or item.attribute_key}: {mark}  {item.shape}")
    if item.status != PASS:
        print(f"      {item.detail}")


def _validate_visual_hold_sections(
    args: argparse.Namespace, sections: list[str]
) -> str:
    if args.all_sections:
        raise RuntimeError(
            "--visual-hold 只允许一个 section；不要同时使用 --all-sections。"
        )
    if len(sections) != 1:
        raise RuntimeError(
            "--visual-hold 只允许一个 section；请只传一次 --section。"
        )
    return sections[0]


def main() -> int:
    args = build_parser().parse_args()
    profile_dir = Path(args.profile_dir).resolve()
    logs_dir = Path(args.logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"user_data_dir：{profile_dir}")
    print(f"长期 Edge CDP：127.0.0.1:{args.cdp_port}")
    if args.visual_hold:
        print(
            "安全模式：synthetic visual-hold；一次打开 section 并同时填满当前空字段，"
            "检查结束后只点 Cancel，不会保存。"
        )
    else:
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
        visual_hold_cleanup_clicked: bool | None = None
        visual_hold_interrupted = False

        if args.visual_hold:
            section_title = _validate_visual_hold_sections(args, sections)
            print(f"\n===== {section_title} / VISUAL HOLD =====")
            print(
                "将一次性填写所有当前空字段。不会点击 + 创建额外槽位；"
                "多值 + 能力已经由普通 coverage 单独验证。"
            )
            results = fill_section_for_visual_hold(
                adapter,
                section_title,
                recheck_wait_ms=args.recheck_wait_ms,
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
                on_result=_print_visual_progress,
            )
            summary = summarize_results(results)
            all_results.extend(results)
            section_payloads.append(
                {
                    "section": section_title,
                    "summary": summary,
                    "results": [item.as_dict() for item in results],
                }
            )

            print("\n===== VISUAL HOLD READY =====")
            print(
                f"最终整页复核：{summary['passed']}/{summary['empty_field_attempts']} "
                "empty fields PASS。"
            )
            print(
                "现在页面会保持展开，所有成功的测试值会同时留在当前 section。\n"
                "请去 Edge 自己上下滚动检查：普通文本、下拉、数值、静态单位和 qualifier。\n"
                "不要点击 Save / Send to QC。"
            )
            try:
                input(
                    "检查完成后回到这个 PowerShell，直接按 Enter；"
                    "程序会统一 Cancel 并清掉测试值。"
                )
            except KeyboardInterrupt:
                visual_hold_interrupted = True
                print("\n收到 Ctrl+C；正在安全 Cancel visual-hold 测试值……")
            finally:
                visual_hold_cleanup_clicked = cleanup_visual_hold_section(
                    adapter, section_title
                )
                if visual_hold_cleanup_clicked:
                    print("已自动 Cancel；visual-hold 测试值已清理。")
                else:
                    print("section 已经处于折叠状态；未再次点击 Cancel。")
        else:
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
                    f"section 结果：{summary['passed']}/{summary['empty_field_attempts']} "
                    f"empty fields PASS; existing skipped={summary['skipped_existing']}"
                )

        overall = summarize_results(all_results)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = logs_dir / f"makro-coverage-{stamp}.json"
        payload = {
            "mode": (
                "synthetic_visual_hold"
                if args.visual_hold
                else "synthetic_control_coverage"
            ),
            "browser_session": "single_edge_cdp",
            "cdp_port": args.cdp_port,
            "page_url": page.url,
            "expected_vertical": args.expected_vertical,
            "listing_tab_count": listing_tab_count,
            "sections": section_payloads,
            "overall": overall,
            "visual_hold_cleanup_clicked": visual_hold_cleanup_clicked,
            "visual_hold_interrupted": visual_hold_interrupted,
            "save_clicked": False,
            "send_to_qc_clicked": False,
        }
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

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