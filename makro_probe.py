"""Makro Seller Center authenticated DOM probe (thin CLI facade).

The probe is intentionally read-only:

* attaches to the same long-lived Microsoft Edge used by the fill CLI through
  localhost-only CDP (browser_profiles/makro-edge; never committed to git),
* lets the user log in manually when needed,
* scans the whole page including internal scroll containers,
* recognises native controls and React-style custom dropdowns,
* writes field metadata JSON, a full-page screenshot and a sanitized
  DOM snapshot under logs/makro-probe/,
* never reads password fields, never records field values unless
  --include-values is passed, and never clicks Save / Send to QC,
* never closes or restarts the externally owned long-lived Edge.

All Makro DOM/section logic lives in ``app/makro``; this module only keeps the
CLI parser, compatibility helpers and output orchestration.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness
from app.makro import (
    MAKRO_HOME_URL,
    build_semantic_fields,
    capture_controls,
    capture_dropdown_options,
    derive_attribute_key,
    find_scroll_containers,
    find_sections,
    is_makro_listing_page,
    merge_scans,
    parse_makro_listing_url,
    sanitize_dom_snapshot,
    scan_section_fields,
    scan_sections,
    scroll_and_capture,
    scroll_container,
    scroll_to_end,
    scroll_window,
    wait_for_authenticated_listing,
)
from app.makro.listing import _is_logged_in  # noqa: F401  (kept import path)

# Keep the old probe import surface working for tests/scripts that relied on
# these names being importable from makro_probe.
__all__ = [
    "MAKRO_HOME_URL",
    "build_launch_kwargs",
    "build_parser",
    "build_semantic_fields",
    "capture_controls",
    "capture_dropdown_options",
    "capture_listing",
    "derive_attribute_key",
    "find_scroll_containers",
    "find_sections",
    "is_makro_listing_page",
    "merge_scans",
    "parse_makro_listing_url",
    "sanitize_dom_snapshot",
    "scan_section_fields",
    "scan_sections",
    "scroll_and_capture",
    "scroll_container",
    "scroll_to_end",
    "scroll_window",
    "wait_for_authenticated_listing",
]


def build_launch_kwargs(
    *,
    browser: str,
    profile_dir: Path,
    headless: bool,
) -> dict[str, Any]:
    """Legacy launch-kwargs helper kept for compatibility tests/tools.

    The production probe no longer calls ``launch_persistent_context``. It uses
    :class:`EdgeHarness` so probe and fill attach to the same long-lived Edge.
    """

    kwargs: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "viewport": {"width": 1600, "height": 1000},
    }
    if browser == "edge":
        kwargs["channel"] = "msedge"
    return kwargs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="在本机已授权登录后，采集 Makro Add Listing 页面字段元数据。"
    )
    parser.add_argument(
        "--url",
        default=None,
        help="可选。Add a Single Listing 完整 URL；仅作为首次启动长期 Edge 时的初始导航/校验使用，"
        "按 Enter 后程序直接采集当前页面，不会强制跳回旧 URL（requestId 可能已失效）",
    )
    parser.add_argument(
        "--browser",
        choices=("edge", "chromium"),
        default="edge",
        help="生产流程固定使用长期 Microsoft Edge。chromium 选项仅为旧参数兼容，主流程会拒绝它，"
        "避免误开第二个浏览器会话。",
    )
    parser.add_argument(
        "--profile-dir",
        default="browser_profiles/makro-edge",
        help="长期 Edge 的独立 profile 目录；与 makro_fill.py 共用，不会接管日常 Edge",
    )
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=DEFAULT_CDP_PORT,
        help="长期 Edge 的 localhost CDP 端口；默认 9222，与 makro_fill.py 共用",
    )
    parser.add_argument(
        "--output-dir",
        default="logs/makro-probe",
        help="字段快照、DOM 快照和截图输出目录",
    )
    parser.add_argument(
        "--include-values",
        action="store_true",
        help="调试时包含当前输入值；默认关闭以减少敏感数据落盘",
    )
    parser.add_argument(
        "--no-dom-snapshot",
        action="store_true",
        help="不生成 makro-dom-*.html 安全快照",
    )
    parser.add_argument(
        "--open-dropdowns",
        action="store_true",
        help="尝试点击自定义下拉框读取弹出选项后关闭（可能有轻微副作用，默认关闭）",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="仅影响登录等待策略；长期 Edge 本身由 EdgeHarness 管理，不会因此重启为无头浏览器",
    )
    parser.add_argument(
        "--scan-sections",
        action="store_true",
        help="扫描所有 listing section：点击 EDIT 展开 Product Description / "
        "Additional Description / Product Photos 后逐 section 滚动扫描；"
        "只点安全 Cancel，不填写、不保存、不上传、不点 Send to QC",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="在同一个长期 Edge 中反复扫描多个 Add Listing 页面；每次扫描后询问是否继续。"
        "无论是否传此参数，脚本结束都不会关闭长期 Edge。",
    )
    parser.add_argument(
        "--scroll-wait-ms",
        type=int,
        default=350,
        help="每次滚动后等待懒加载的时间（毫秒）",
    )
    parser.add_argument(
        "--max-scroll-steps",
        type=int,
        default=200,
        help="单个滚动容器最大滚动次数（安全上限）",
    )
    return parser


def _ask_yes_no(prompt: str, *, default: bool = True) -> bool:
    """Ask a [Y/n] or [y/N] question and return the boolean answer."""
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{prompt}{suffix} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in {"y", "yes"}


def _profile_artifacts(profile_dir: Path) -> str:
    """Short confirmation that a Chromium persistent profile exists at path."""
    markers = [
        name
        for name in ("Local State", "Default", "Preferences")
        if (profile_dir / name).exists()
    ]
    return f"检测到 {', '.join(markers)}" if markers else "目录已创建（等待浏览器写入）"


def capture_listing(
    page: Page,
    *,
    output_dir: Path,
    stamp: str,
    include_values: bool,
    open_dropdowns: bool,
    scan_sections_mode: bool,
    no_dom_snapshot: bool,
    scroll_wait_ms: int,
    max_scroll_steps: int,
) -> dict[str, Any]:
    """Scan the current listing page and write JSON / screenshot / DOM snapshot.

    Read-only: never fills values, never uploads files and never clicks
    Save / Send to QC. Returns the JSON payload.
    """

    if scan_sections_mode:
        sections_payload, controls, section_stats = scan_sections(
            page,
            include_values=include_values,
            wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        scan_stats: dict[str, Any] = {"sections_scan": section_stats}
    else:
        controls, scan_stats = scroll_and_capture(
            page,
            include_values=include_values,
            open_dropdowns=open_dropdowns,
            wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        sections_payload = None

    json_path = output_dir / f"makro-fields-{stamp}.json"
    screenshot_path = output_dir / f"makro-page-{stamp}.png"
    dom_path = output_dir / f"makro-dom-{stamp}.html"

    dom_snapshot_saved = False
    if not no_dom_snapshot:
        sanitized = sanitize_dom_snapshot(page.content())
        dom_path.write_text(sanitized, encoding="utf-8")
        dom_snapshot_saved = True

    try:
        current_target = parse_makro_listing_url(page.url)
    except ValueError:
        current_target = None

    semantic_fields = build_semantic_fields(controls)
    payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "platform": "makro",
        "host": "seller.makro.co.za",
        "brand": current_target.brand if current_target else None,
        "vertical": current_target.vertical if current_target else None,
        "request_id_present": bool(current_target and current_target.request_id),
        "vid": current_target.vid if current_target else None,
        "page_url": page.url,
        "include_values": bool(include_values),
        "open_dropdowns": bool(open_dropdowns),
        "scan": scan_stats,
        "sections": sections_payload,
        "dom_snapshot_saved": dom_snapshot_saved,
        "control_count": len(controls),
        "field_count": sum(
            1 for item in controls if item.get("field_kind") != "option"
        ),
        "semantic_field_count": len(semantic_fields),
        "semantic_fields": semantic_fields,
        "controls": controls,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page.screenshot(path=str(screenshot_path), full_page=True)
    return payload


def main() -> int:
    args = build_parser().parse_args()
    if args.browser != "edge":
        raise RuntimeError(
            "makro_probe.py 生产流程只允许复用长期 Edge。"
            "请不要使用 --browser chromium，以免误开第二个浏览器会话。"
        )

    initial_url = MAKRO_HOME_URL
    if args.url:
        try:
            target = parse_makro_listing_url(args.url)
            initial_url = target.url
        except ValueError:
            initial_url = args.url
            print(f"提示：--url 不是 Add a Single Listing 格式，仅作为首次启动地址：{initial_url}")

    profile_dir = Path(args.profile_dir).resolve()
    output_dir = Path(args.output_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("浏览器：长期 Microsoft Edge（CDP 复用）")
    print(f"user_data_dir：{profile_dir}")
    print(f"长期 Edge CDP：127.0.0.1:{args.cdp_port}")
    print(f"profile 确认：{_profile_artifacts(profile_dir)}（与 makro_fill.py 共用）")

    with sync_playwright() as playwright:
        harness = EdgeHarness(
            playwright,
            profile_dir=profile_dir,
            port=args.cdp_port,
            start_url=initial_url,
        )
        page = harness.page
        page.set_default_timeout(15_000)

        if harness.launched_now:
            print("已启动长期 Makro Edge。后续 probe/fill 都会继续复用它。")
        else:
            print("已连接现有长期 Makro Edge；不会新开或重启浏览器。")

        iteration = 0
        while True:
            iteration += 1
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            if iteration > 1:
                stamp = f"{stamp}-{iteration:02d}"
            json_path = output_dir / f"makro-fields-{stamp}.json"
            screenshot_path = output_dir / f"makro-page-{stamp}.png"
            dom_path = output_dir / f"makro-dom-{stamp}.html"

            if iteration > 1:
                print(f"\n===== 第 {iteration} 个页面 =====")

            page = harness.ensure_page()
            if iteration == 1 and is_makro_listing_page(page):
                pass
            else:
                wait_for_authenticated_listing(
                    page,
                    initial_url,
                    headless=args.headless,
                    navigate_first=(iteration == 1 and harness.launched_now),
                )

            # Re-select after a manual navigation step so a newly opened Makro
            # listing tab can become the active probe target. This never closes
            # or navigates away from the long-lived Edge itself.
            page = harness.select_page()
            page.set_default_timeout(15_000)
            if not is_makro_listing_page(page):
                raise RuntimeError(
                    "当前长期 Edge 中没有可识别的 Add a Single Listing 页面，已停止采集。"
                )

            payload = capture_listing(
                page,
                output_dir=output_dir,
                stamp=stamp,
                include_values=args.include_values,
                open_dropdowns=args.open_dropdowns,
                scan_sections_mode=args.scan_sections,
                no_dom_snapshot=args.no_dom_snapshot,
                scroll_wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
            )

            print(
                f"已采集 {payload['control_count']} 个控件，"
                f"聚合为 {payload['semantic_field_count']} 个语义字段"
                f"（DOM 字段 {payload['field_count']} 个）。"
            )
            print(f"字段元数据：{json_path}")
            print(f"页面截图：{screenshot_path}")
            if payload.get("dom_snapshot_saved"):
                print(f"安全 DOM 快照：{dom_path}")
            if args.scan_sections:
                sec = payload["scan"]["sections_scan"]
                print(
                    f"section 扫描：发现 {sec['sections_found']} 个 section，"
                    f"展开 {sec['sections_expanded_by_scan']} 个，"
                    f"Cancel {sec['sections_cancelled']} 个。"
                )
            else:
                print(
                    f"滚动扫描：{payload['scan']['scroll_passes']} 次扫描，"
                    f"{payload['scan']['scroll_containers_found']} 个内部滚动容器。"
                )

            if not args.keep_open:
                break
            if not _ask_yes_no("继续扫描下一个页面？ ", default=True):
                break

        # Do not close browser/context. The Edge process is intentionally
        # external and shared by probe/fill. Dropping our references is enough.
        harness.detach()

    print("长期 Edge 保持打开；默认没有记录密码/隐藏字段，也没有点击 Save / Send to QC。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
