"""Fill current empty Makro listing attributes and leave them for inspection.

This is a development/inspection command. It never clicks Save, Send to QC, or
Cancel. Synthetic values intentionally remain in the current draft after the
command finishes so the user can inspect them in Edge.
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
from app.makro.direct_visual_hold import (
    fill_all_current_empty_attributes,
    summarize_direct_hold,
)
from app.makro.domain import MakroDomainAdapter
from app.makro.listing_preflight import CORE_FORM_SECTIONS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "动态发现并填写 Makro 核心 section 的所有当前空 listing attribute，"
            "填完后保持测试值供人工检查；不 Save / Send to QC / Cancel。"
        )
    )
    parser.add_argument("--expected-vertical", required=True)
    parser.add_argument(
        "--section",
        action="append",
        choices=CORE_FORM_SECTIONS,
        help=(
            "只检查指定核心 section；可重复传入。未指定时按三个核心 section 依次处理。"
            "Makro 若一次只能编辑一个 section，建议人工验收时一次只传一个。"
        ),
    )
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--logs-dir", default="logs/makro-listing-visual-hold")
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


def _print_progress(section: str, item: Any, index: int, total: int) -> None:
    status = "PASS" if item.status == "pass" else item.status.upper()
    print(
        f"  {section} [{index:02d}/{total:02d}] "
        f"{item.label or item.attribute_key}: {status} {item.shape}"
    )
    if item.status != "pass":
        print(f"      {item.detail}")


def main() -> int:
    args = build_parser().parse_args()
    sections = tuple(args.section) if args.section else CORE_FORM_SECTIONS
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.logs_dir) / f"visual-hold-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print("===== MAKRO DYNAMIC LISTING VISUAL HOLD =====")
    print("本命令不使用 14/46/74/78 等固定字段数量。")
    print("运行时动态发现当前商品所选 section 的真实 listing attributes。")
    print("只填当前空字段；已有值不覆盖。")
    print("不会 Save，不会 Send to QC，不会 Cancel；测试值会留在页面供你检查。")
    print("本次 section：" + " | ".join(sections))

    harness: EdgeHarness | None = None
    summary: dict[str, Any] | None = None
    page_url = ""
    listing_tab_count = 0

    with sync_playwright() as playwright:
        try:
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
            page_url = page.url
            print(f"操作标签页：{page_url}")

            results_by_section = fill_all_current_empty_attributes(
                adapter,
                sections=sections,
                recheck_wait_ms=args.recheck_wait_ms,
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
                on_result=_print_progress,
            )
            summary = summarize_direct_hold(results_by_section)

            report = {
                "mode": "dynamic_listing_visual_hold_no_cleanup",
                "page_url": page_url,
                "expected_vertical": args.expected_vertical,
                "listing_tab_count": listing_tab_count,
                "sections_requested": list(sections),
                "summary": summary,
                "results": {
                    title: [item.as_dict() for item in results]
                    for title, results in results_by_section.items()
                },
                "save_clicked": False,
                "send_to_qc_clicked": False,
                "cancel_clicked": False,
                "browser_closed": False,
            }
            report_path = run_dir / "report.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            print("\n===== VISUAL HOLD READY =====")
            for title, item in summary["sections"].items():
                print(
                    f"{title}: discovered={item['discovered_listing_attributes']}, "
                    f"empty={item['empty_targets']}, PASS={item['passed']}, "
                    f"existing={item['existing_skipped']}, "
                    f"failed={item['failed_or_unsupported']}"
                )
            print(
                "TOTAL: discovered="
                f"{summary['total_discovered_listing_attributes']}, "
                f"empty targets={summary['total_empty_targets']}, "
                f"PASS={summary['total_passed']}, "
                f"existing={summary['total_existing_skipped']}, "
                f"failed={summary['total_failed_or_unsupported']}"
            )
            print(f"报告：{report_path.resolve()}")
            print("现在去 Edge 检查。不要点 Save。程序不会帮你清空这些测试值。")
            input("检查完成后回到这里按 Enter 只退出程序；测试值仍继续保留在页面。")
            print("已退出 inspection hold；没有 Cancel，页面测试值保持不动。")

            harness.detach()
            return 0 if summary["total_failed_or_unsupported"] == 0 else 2
        except KeyboardInterrupt:
            print("\n已收到 Ctrl+C。不会 Cancel；当前已经填入的测试值保持原样供检查。")
            if harness is not None:
                harness.detach()
            return 130
        except Exception as exc:
            print(f"\n运行停止：{exc}")
            print("不会 Cancel；已经写入页面的测试值保持原样，方便你直接检查失败现场。")
            if harness is not None:
                harness.detach()
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
