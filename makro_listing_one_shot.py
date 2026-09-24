"""One-command Makro whole-listing synthetic inspection.

For a disposable test draft only: dynamically fill every current empty listing
attribute in the three core sections, Save each section so Makro can move to the
next one, verify the saved values by reopening, and leave the final draft intact
for human inspection. Never clicks Send to QC.
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
from app.makro.persisted_inspection import run_one_shot_persisted_inspection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Makro 测试 draft 一键完整验收：动态发现三个核心 section 的所有当前空字段，"
            "自动填写、逐 section Save、保存后回读验证，最终把整张测试商品保留给人工检查。"
        )
    )
    parser.add_argument("--expected-vertical", required=True)
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--logs-dir", default="logs/makro-listing-one-shot")
    parser.add_argument("--recheck-wait-ms", type=int, default=800)
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    parser.add_argument(
        "--persist-test-values",
        action="store_true",
        help="必需安全开关：明确允许把 synthetic 测试值保存到当前测试 draft。",
    )
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


def _progress(section: str, item: Any, index: int, total: int) -> None:
    mark = "PASS" if item.status == "pass" else item.status.upper()
    print(
        f"  {section} [{index:02d}/{total:02d}] "
        f"{item.label or item.attribute_key}: {mark} {item.shape}"
    )
    if item.status != "pass":
        print(f"      {item.detail}")


def main() -> int:
    args = build_parser().parse_args()
    if not args.persist_test_values:
        raise SystemExit(
            "拒绝运行：这是会 Save synthetic 测试值的验收命令。"
            "请明确加 --persist-test-values。"
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.logs_dir) / f"one-shot-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print("===== MAKRO ONE-SHOT WHOLE LISTING INSPECTION =====")
    print("字段数量完全动态发现；不使用 14/46/74/78 等固定总数。")
    print("会把 synthetic 测试值 Save 到当前测试 draft，以便三个 section 全部连续完成。")
    print("绝不会点击 Send to QC。完成后测试值保留，供你一次性人工验收。")

    harness: EdgeHarness | None = None
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
            target = adapter.current_target()
            request_id = target.request_id if target else ""
            print(f"操作标签页：{page.url}")
            print(f"requestId：{request_id or '<none>'}")

            results = run_one_shot_persisted_inspection(
                adapter,
                run_token=stamp,
                recheck_wait_ms=args.recheck_wait_ms,
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
                on_result=_progress,
            )

            sections_payload: dict[str, Any] = {}
            total_discovered = 0
            total_empty = 0
            total_passed = 0
            total_existing = 0
            for section_result in results:
                payload = section_result.as_dict()
                sections_payload[section_result.section] = payload
                summary = payload["summary"]
                discovered = len(section_result.results)
                total_discovered += discovered
                total_empty += summary["empty_field_attempts"]
                total_passed += summary["passed"]
                total_existing += summary["skipped_existing"]
                print(
                    f"SAVED + VERIFIED: {section_result.section} | "
                    f"discovered={discovered}, empty={summary['empty_field_attempts']}, "
                    f"PASS={summary['passed']}, existing={summary['skipped_existing']}"
                )

            screenshot = run_dir / "whole-listing-after-all-saves.png"
            page.screenshot(path=str(screenshot), full_page=True)
            report = {
                "mode": "persisted_synthetic_one_shot_inspection",
                "page_url": page.url,
                "request_id": request_id,
                "expected_vertical": args.expected_vertical,
                "listing_tab_count": listing_tab_count,
                "sections": sections_payload,
                "total_discovered_listing_attributes": total_discovered,
                "total_empty_targets": total_empty,
                "total_passed": total_passed,
                "total_existing_skipped": total_existing,
                "all_sections_saved": all(item.saved for item in results),
                "all_sections_persisted_verified": all(item.persisted_verified for item in results),
                "send_to_qc_clicked": False,
                "browser_closed": False,
                "screenshot": str(screenshot.resolve()),
            }
            report_path = run_dir / "report.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

            print("\n===== ONE-SHOT INSPECTION READY =====")
            print(
                f"TOTAL discovered={total_discovered}, current empty targets={total_empty}, "
                f"PASS={total_passed}, existing={total_existing}."
            )
            print("三个核心 section 均已 Save，并在 Save 后重新打开完成持久化回读验证。")
            print("测试值现在保留在这个测试 draft 中。去 Edge 一次性检查整张商品。")
            print("不要点击 Send to QC。")
            print(f"报告：{report_path.resolve()}")
            print(f"截图：{screenshot.resolve()}")
            harness.detach()
            return 0
        except KeyboardInterrupt:
            print("\n已收到 Ctrl+C。不会点击 Send to QC；已 Save 的 section 会继续保留。")
            if harness is not None:
                harness.detach()
            return 130
        except Exception as exc:
            print(f"\nONE-SHOT FAILED：{exc}")
            print("不会自动 Cancel，也不会 Send to QC；失败现场保持，便于直接检查。")
            if harness is not None:
                harness.detach()
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
