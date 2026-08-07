"""Evidence-grounded Makro dry-run filler.

This command intentionally does NOT click Save or Send to QC. It scans all
current Makro semantic fields, resolves answers from explicit product evidence,
then fills one editable section and leaves it open for human inspection.

Example:
    python makro_fill.py --product data/product.xlsx --sku ABC123 --dry-run
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
from app.makro_dryrun import fill_resolved_field
from app.source_bundle import bundle_from_product_table, bundle_from_qa_file
from makro_probe import (
    MAKRO_HOME_URL,
    build_launch_kwargs,
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
    parser.add_argument("--browser", choices=("edge", "chromium"), default="edge")
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--logs-dir", default="logs/makro-fill")
    parser.add_argument("--headless", action="store_true")
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
    return re.sub(r"\s*\(\d+\s*/\s*\d+\)\s*$", "", title).strip()


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
    print("安全模式：dry-run；不会点击 Save / Send to QC。")

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
            wait_for_authenticated_listing(
                page,
                MAKRO_HOME_URL,
                headless=args.headless,
                navigate_first=True,
            )

            sections_payload, flat_controls, scan_stats = scan_sections(
                page,
                include_values=False,
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
            )
            semantic_fields = build_semantic_fields(flat_controls)
            resolved = resolve_fields(semantic_fields, bundle)

            resolutions_payload: list[dict[str, Any]] = []
            field_lookup = {
                (str(field.get("section_heading") or ""), str(field.get("attribute_key") or "")): field
                for field in semantic_fields
            }
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

                print("\n已停在 Save 前：程序不会点击 Save。请在 Edge 中检查填写结果。")

            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            output = logs_dir / f"makro-fill-{timestamp}.json"
            payload = {
                "mode": "dry_run",
                "page_url": page.url,
                "sku": bundle.sku,
                "source_file": str(Path(args.product).resolve()),
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

            if not args.headless:
                input("检查完成后回终端按 Enter，关闭自动化 Edge（未保存修改会被丢弃）。")
        finally:
            context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
