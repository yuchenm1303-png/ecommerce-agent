"""Makro Seller Center authenticated DOM probe (thin CLI facade).

The probe is intentionally read-only:

* opens an isolated persistent Microsoft Edge profile by default
  (browser_profiles/makro-edge; never committed to git and never touches the
  user's daily Edge profile),
* lets the user log in manually when needed,
* scans the whole page including internal scroll containers,
* recognises native controls and React-style custom dropdowns,
* writes field metadata JSON, a full-page screenshot and a sanitized
  DOM snapshot under logs/makro-probe/,
* never reads password fields, never records field values unless
  --include-values is passed, and never clicks Save / Send to QC.

All Makro DOM/section logic lives in ``app/makro``; this module only keeps the
CLI parser, launch kwargs and output orchestration.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

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
    """Build persistent-context launch kwargs.

    Edge uses Playwright's msedge channel with an isolated user-data-dir so the
    user's daily Edge profile is never opened or modified.
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
        help="可选。Add a Single Listing 完整 URL；仅作为初始导航/校验使用，"
        "按 Enter 后程序直接采集当前页面，不会强制跳回旧 URL（requestId 可能已失效）",
    )
    parser.add_argument(
        "--browser",
        choices=("edge", "chromium"),
        default="edge",
        help="默认使用本机 Microsoft Edge（channel=msedge）和独立 persistent profile；"
        "chromium 用于调试（需要先 playwright install chromium）",
    )
    parser.add_argument(
        "--profile-dir",
        default="browser_profiles/makro-edge",
        help="本地持久化浏览器目录；Edge 默认使用独立目录，不会接管日常 Edge",
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
        help="无头模式；仅在 profile 已登录且页面可复用时使用",
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
        help="保持同一个 Edge 会话：单次登录后可反复扫描多个 Add Listing 页面；"
        "每次扫描后询问是否继续，结束时询问是否保持浏览器打开"
        "（默认询问，Y 保持；不记录任何认证数据）",
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
    target = None
    initial_url = MAKRO_HOME_URL
    if args.url:
        try:
            target = parse_makro_listing_url(args.url)
            initial_url = target.url
        except ValueError:
            initial_url = args.url
            print(f"提示：--url 不是 Add a Single Listing 格式，仅作为初始导航地址：{initial_url}")
    profile_dir = Path(args.profile_dir)
    output_dir = Path(args.output_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    browser_label = "Microsoft Edge" if args.browser == "edge" else "Chromium"
    print(f"浏览器：{browser_label}")
    print(f"user_data_dir：{profile_dir.resolve()}")
    print(f"profile 确认：{_profile_artifacts(profile_dir)}（始终复用同一持久化目录）")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            **build_launch_kwargs(
                browser=args.browser,
                profile_dir=profile_dir,
                headless=args.headless,
            )
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(15_000)

        try:
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
                wait_for_authenticated_listing(
                    page,
                    initial_url,
                    headless=args.headless,
                    navigate_first=iteration == 1,
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

            if args.keep_open:
                if _ask_yes_no("继续保持 Edge 打开吗？ ", default=True):
                    print("Edge 保持打开（context 未关闭）。处理完后回终端按 Enter 关闭。")
                    try:
                        input()
                    except EOFError:
                        pass
        finally:
            context.close()

    print("默认没有记录密码/隐藏字段，也没有点击 Save / Send to QC。")
    return 0

