"""Evidence-grounded Makro dry-run filler.

This command intentionally does NOT click Save or Send to QC. It scans all
current Makro semantic fields, resolves answers from explicit product evidence,
then fills one editable section and leaves it open for human inspection.

The default browser model is one long-lived detached Microsoft Edge instance.
Later invocations reconnect to that same Edge/profile/login through localhost
CDP instead of launching a new browser.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

from app.answer_resolver import RESOLVED, resolve_fields
from app.browser_session import DEFAULT_CDP_PORT, connect_single_edge
from app.makro_dryrun import fill_resolved_field
from app.platforms.makro import is_makro_listing_page, parse_makro_listing_url
from app.source_bundle import bundle_from_product_table, bundle_from_qa_file
from makro_probe import (
    MAKRO_HOME_URL,
    build_semantic_fields,
    find_sections,
    scan_section_fields,
    scan_sections,
    wait_for_authenticated_listing,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Makro 真实页面证据驱动 dry-run：动态识别 → 解析 → 填写 → 回读，绝不保存。"
    )
    parser.add_argument("--product", required=True, help="商品 .csv/.xlsx/.xlsm 数据或 QA 文件")
    parser.add_argument("--sku", default=None, help="商品表含多行时指定 SKU；QA 文件可不填")
    parser.add_argument(
        "--source-format",
        choices=("auto", "table", "qa"),
        default="auto",
        help="auto 先尝试标准商品表，失败后尝试 Question/Answer 文件",
    )
    parser.add_argument("--image", action="append", default=[], help="商品图片路径，可重复传入；本阶段只记录，不自动识图")
    parser.add_argument("--product-url", default=None, help="商品/供应商链接；本阶段只记录，不自动联网猜参数")
    parser.add_argument("--supplemental-text", default="", help="补充说明；本阶段不作为无结构事实自动填入")
    parser.add_argument(
        "--section",
        default=None,
        help="只填写指定 section（可写 Product Description 等，不含 (x/y) 计数）；默认选首个有 resolved 答案的 section",
    )
    parser.add_argument(
        "--expected-vertical",
        default=None,
        help="可选安全门。要求当前 Add Listing URL 的 vertical 与此值完全一致；不一致时在扫描/填写前停止",
    )
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=DEFAULT_CDP_PORT,
        help="长期 Edge 的 localhost CDP 端口；默认 9222。后续运行复用同一浏览器，不会重新启动 Edge。",
    )
    parser.add_argument("--logs-dir", default="logs/makro-fill")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="当前版本始终为 dry-run；参数保留用于明确表达运行意图",
    )
    parser.add_argument("--scroll-wait-ms", type=int, default=350)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    return parser


def _load_bundle(args: argparse.Namespace):
    kwargs = {
        "image_paths": args.image,
        "product_url": args.product_url,
        "supplemental_text": args.supplemental_text,
    }
    if args.source_format == "table":
        return bundle_from_product_table(args.product, sku=args.sku, **kwargs)
    if args.source_format == "qa":
        return bundle_from_qa_file(args.product, sku=args.sku or "", **kwargs)

    try:
        return bundle_from_product_table(args.product, sku=args.sku, **kwargs)
    except (ValueError, KeyError):
        return bundle_from_qa_file(args.product, sku=args.sku or "", **kwargs)


def _base_section_title(title: str) -> str:
    """Return a stable Makro section identity, ignoring UI-only suffixes.

    Makro appends changing completion counters such as ``(14/14)`` and can also
    append ``(Optional)`` to section labels. Neither suffix is part of the
    semantic section identity used by CLI selection or resolver matching.
    """

    normalized = re.sub(r"\s*\(\d+\s*/\s*\d+\)\s*$", "", title).strip()
    normalized = re.sub(r"\s*\(\s*optional\s*\)\s*$", "", normalized, flags=re.IGNORECASE).strip()
    return normalized


def _assert_expected_vertical(page: Page, expected_vertical: str | None) -> None:
    """Stop before scanning/filling if the current listing is the wrong vertical."""

    if not expected_vertical:
        return
    target = parse_makro_listing_url(page.url)
    actual = (target.vertical or "").strip()
    expected = expected_vertical.strip()
    if actual.casefold() != expected.casefold():
        raise RuntimeError(
            "当前 Add Listing vertical 与商品资料不匹配，已在扫描/填写前停止："
            f" expected={expected!r}, actual={actual or '(missing)'!r}。"
        )
    print(f"vertical 安全校验通过：{actual}")


def _find_section(page: Page, wanted: str) -> dict[str, Any] | None:
    wanted_base = _base_section_title(wanted).casefold()
    for section in find_sections(page):
        if _base_section_title(str(section.get("title") or "")).casefold() == wanted_base:
            return section
    return None


def _open_section_for_edit(page: Page, section: dict[str, Any]) -> None:
    if not section.get("has_edit"):
        return
    path = str(section.get("path") or "")
    if not path:
        raise RuntimeError("section 缺少 DOM path，无法安全打开。")
    card = page.locator(path).first
    button = card.get_by_text("EDIT", exact=True).first
    button.scroll_into_view_if_needed()
    button.click()
    page.wait_for_timeout(500)


def _select_target_section(
    sections_payload: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    requested: str | None,
) -> str | None:
    resolved_by_section: dict[str, int] = {}
    for item in resolutions:
        if item.get("status") != RESOLVED:
            continue
        section = _base_section_title(str(item.get("section_heading") or ""))
        if section:
            resolved_by_section[section] = resolved_by_section.get(section, 0) + 1

    if requested:
        wanted = _base_section_title(requested)
        return wanted if resolved_by_section.get(wanted, 0) else None

    for section in sections_payload:
        title = _base_section_title(str(section.get("title") or ""))
        if resolved_by_section.get(title, 0):
            return title
    return None


def main() -> int:
    args = build_parser().parse_args()
    bundle = _load_bundle(args)
    profile_dir = Path(args.profile_dir).resolve()
    logs_dir = Path(args.logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"商品资料：{Path(args.product).resolve()}")
    print(f"SKU：{bundle.sku or '(QA 文件未指定)'}")
    print(f"user_data_dir：{profile_dir}")
    print(f"长期 Edge CDP：127.0.0.1:{args.cdp_port}")
    print("安全模式：dry-run；不会点击 Save / Send to QC。")
    if args.expected_vertical:
        print(f"预期 vertical：{args.expected_vertical}")

    with sync_playwright() as playwright:
        session = connect_single_edge(
            playwright,
            profile_dir=profile_dir,
            port=args.cdp_port,
            start_url=MAKRO_HOME_URL,
        )
        page = session.page
        page.set_default_timeout(15_000)

        if session.launched_now:
            print("已启动长期 Makro Edge。以后脚本将复用这个浏览器，不再重复启动。")
        else:
            print("已连接现有 Makro Edge；不会新开浏览器。")

        # Never navigate away from an already-open listing. If the long-lived
        # Edge was just created (or is on another page), let the user navigate in
        # that same window and then continue.
        if not is_makro_listing_page(page):
            wait_for_authenticated_listing(
                page,
                MAKRO_HOME_URL,
                headless=False,
                navigate_first=session.launched_now,
            )

        _assert_expected_vertical(page, args.expected_vertical)

        sections_payload, flat_controls, scan_stats = scan_sections(
            page,
            include_values=False,
            wait_ms=args.scroll_wait_ms,
            max_scroll_steps=args.max_scroll_steps,
        )
        semantic_fields = build_semantic_fields(flat_controls)
        resolved = resolve_fields(semantic_fields, bundle)

        resolutions_payload: list[dict[str, Any]] = []
        for answer in resolved:
            data = answer.as_dict()
            matching_field = next(
                (
                    field
                    for field in semantic_fields
                    if field.get("attribute_key") == answer.attribute_key
                    and field.get("label") == answer.label
                ),
                None,
            )
            data["section_heading"] = (
                str(matching_field.get("section_heading") or "") if matching_field else ""
            )
            resolutions_payload.append(data)

        target_section = _select_target_section(
            sections_payload, resolutions_payload, args.section
        )
        verifications: list[dict[str, Any]] = []

        if target_section is None:
            print("没有找到可安全自动填写的 resolved section；不会修改页面。")
        else:
            print(f"本次 dry-run 只填写一个 section：{target_section}")
            print("其他 section 已解析但不填写，避免在禁止 Save 的阶段跨 section 丢失状态。")
            current = _find_section(page, target_section)
            if current is None:
                raise RuntimeError(f"当前页面找不到 section：{target_section}")
            _open_section_for_edit(page, current)
            current = _find_section(page, target_section) or current
            section_path = str(current.get("path") or "")
            fresh_controls = scan_section_fields(
                page,
                section_path,
                include_values=False,
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
            )
            fresh_fields = build_semantic_fields(fresh_controls)
            fresh_answers = resolve_fields(fresh_fields, bundle)
            fresh_field_by_key = {
                str(field.get("attribute_key") or ""): field for field in fresh_fields
            }
            for answer in fresh_answers:
                if answer.status != RESOLVED:
                    continue
                semantic_field = fresh_field_by_key.get(answer.attribute_key)
                if semantic_field is None:
                    continue
                result = fill_resolved_field(page, semantic_field, answer)
                verifications.append(result.as_dict())
                print(f"  {answer.label}: {result.status}")

            print("\n已停在 Save 前：程序不会点击 Save。请直接在当前 Edge 中检查填写结果。")

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = logs_dir / f"makro-fill-{timestamp}.json"
        payload = {
            "mode": "dry_run",
            "browser_session": "single_edge_cdp",
            "cdp_port": args.cdp_port,
            "page_url": page.url,
            "sku": bundle.sku,
            "source_file": str(Path(args.product).resolve()),
            "expected_vertical": args.expected_vertical,
            "scan": scan_stats,
            "semantic_field_count": len(semantic_fields),
            "target_section": target_section,
            "resolutions": resolutions_payload,
            "verifications": verifications,
            "save_clicked": False,
            "send_to_qc_clicked": False,
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"日志：{output.resolve()}")
        print("脚本已结束，但长期 Edge 会继续保持打开；不会因终端返回而关闭浏览器。")

        # Important: the browser is an externally launched long-lived process.
        # Do NOT call browser.close() or context.close(). Leaving the Playwright
        # connection is enough; the same Edge remains available to later runs.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
