"""Fill every currently empty Makro core-form field and leave it visible.

This is a human visual-acceptance runner, not the production publisher and not the
per-field coverage runner. It dynamically discovers the current three core form
sections, fills every currently empty *listing attribute* it can operate, performs
settled readback, and then disconnects from CDP without Save, Cancel, or cleanup.
The browser is intentionally left exactly as filled so the user can inspect it.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness
from app.makro import MAKRO_HOME_URL, is_listing_url, parse_makro_listing_url
from app.makro.coverage import summarize_results
from app.makro.domain import MakroDomainAdapter
from app.makro.listing_preflight import CORE_FORM_SECTIONS
from app.makro.visual_hold import _verify_final_hold, fill_section_for_visual_hold


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _is_non_listing_helper(field_item: dict[str, Any]) -> bool:
    """Exclude Makro UI helpers that are not attributes of the listing itself.

    The Price/Stock card contains a search box used to copy attributes from an
    old SKU. It is a visible input, but filling it can trigger a copy workflow and
    must never be treated as a product/listing attribute.
    """

    key = _norm(field_item.get("attribute_key"))
    label = _norm(field_item.get("label"))
    if key in {"search for sku id", "search_for_sku_id"} or label == "search for sku id":
        return True

    for control in field_item.get("controls") or []:
        placeholder = _norm(control.get("placeholder"))
        context = _norm(control.get("context_text"))
        if placeholder == "search for sku id" and "copy attribute values" in context:
            return True
    return False


class ListingAttributeAdapter:
    """Transparent adapter wrapper that removes non-listing helper controls."""

    def __init__(self, delegate: MakroDomainAdapter) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def build_semantic_fields(self, controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        fields = self._delegate.build_semantic_fields(controls)
        return [item for item in fields if not _is_non_listing_helper(item)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Makro 全空字段视觉验收：运行时动态发现三个核心 section 的当前空 listing "
            "attributes，全部填入 synthetic 值并保持现场；不 Save、不 Cancel、不清理。"
        )
    )
    parser.add_argument("--expected-vertical", required=True)
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--recheck-wait-ms", type=int, default=800)
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    parser.add_argument(
        "--screenshot",
        default="logs/makro-visual-fill/all-core-sections-filled.png",
        help="最终整页截图路径。",
    )
    return parser


def _assert_single_listing_tab(context: Any) -> None:
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


def _assert_core_sections_collapsed(adapter: Any) -> None:
    bad: list[str] = []
    for title in CORE_FORM_SECTIONS:
        section = adapter.find_section(title)
        if section is None:
            bad.append(f"{title}: not found")
        elif not section.get("has_edit"):
            bad.append(f"{title}: currently expanded")
    if bad:
        raise RuntimeError(
            "开始前三个核心 section 必须全部折叠并显示 EDIT；请先处理当前未保存编辑：\n- "
            + "\n- ".join(bad)
        )


def _progress(section: str):
    def callback(item: Any, index: int, total: int) -> None:
        mark = "PASS" if item.status == "pass" else item.status.upper()
        print(
            f"  {section} [{index:02d}/{total:02d}] "
            f"{item.label or item.attribute_key}: {mark} {item.shape}"
        )
        if item.status != "pass":
            print(f"      {item.detail}")

    return callback


def main() -> int:
    args = build_parser().parse_args()
    screenshot = Path(args.screenshot)
    screenshot.parent.mkdir(parents=True, exist_ok=True)

    print("===== MAKRO ALL-EMPTY VISUAL FILL =====")
    print("字段数量不写死：每次运行都以当前 listing 的真实 DOM 动态发现结果为准。")
    print("只排除非 listing 的 Makro 辅助控件（例如 Copy old SKU 的 Search for SKU ID）。")
    print("成功后：不 Save、不 Cancel、不清理，脚本退出但长期 Edge 保持原样供人工检查。")

    with sync_playwright() as playwright:
        harness = EdgeHarness(
            playwright,
            profile_dir=Path(args.profile_dir).resolve(),
            port=args.cdp_port,
            start_url=MAKRO_HOME_URL,
        )
        page = harness.page
        page.set_default_timeout(15_000)
        base_adapter = MakroDomainAdapter(page)

        if not base_adapter.is_listing_page():
            base_adapter.wait_for_authenticated_listing(
                MAKRO_HOME_URL,
                headless=False,
                navigate_first=harness.launched_now,
            )

        page = harness.ensure_page()
        base_adapter = MakroDomainAdapter(page)
        adapter = ListingAttributeAdapter(base_adapter)
        _assert_single_listing_tab(harness.context)
        adapter.assert_expected_vertical(args.expected_vertical)
        _assert_core_sections_collapsed(adapter)
        print(f"操作标签页：{page.url}")

        results_by_section: dict[str, list[Any]] = {}
        opened: list[str] = []
        runtime_error: Exception | None = None

        for section_title in CORE_FORM_SECTIONS:
            print(f"\n===== {section_title} =====")
            try:
                results = fill_section_for_visual_hold(
                    adapter,
                    section_title,
                    recheck_wait_ms=args.recheck_wait_ms,
                    wait_ms=args.scroll_wait_ms,
                    max_scroll_steps=args.max_scroll_steps,
                    on_result=_progress(section_title),
                )
                results_by_section[section_title] = results
                opened.append(section_title)

                # Makro must keep previously populated cards expanded; otherwise
                # this run cannot provide the requested whole-listing visual proof.
                for previous in opened:
                    live = adapter.find_section(previous)
                    if live is None or live.get("has_edit"):
                        raise RuntimeError(
                            f"打开 {section_title!r} 后 {previous!r} 被折叠；"
                            "无法保持所有已填字段同时可检查。"
                        )
            except Exception as exc:
                runtime_error = exc
                print(f"\nSTOPPED: {exc}")
                print("不会清理此前已经留在页面上的测试值；请直接在 Edge 检查现场。")
                break

        # Re-read every successful section after all later React updates. This is
        # the decisive anti-false-positive check for the user's visual acceptance.
        if runtime_error is None:
            page.wait_for_timeout(args.recheck_wait_ms)
            for section_title in CORE_FORM_SECTIONS:
                live = adapter.find_section(section_title)
                if live is None or live.get("has_edit"):
                    runtime_error = RuntimeError(
                        f"最终复核时 {section_title!r} 未保持展开。"
                    )
                    break
                section_path = str(live.get("path") or "")
                if not section_path:
                    runtime_error = RuntimeError(
                        f"最终复核时 {section_title!r} 缺少 DOM path。"
                    )
                    break
                _verify_final_hold(
                    adapter,
                    section_title,
                    section_path,
                    results_by_section[section_title],
                    wait_ms=args.scroll_wait_ms,
                    max_scroll_steps=args.max_scroll_steps,
                )

        total_semantic = 0
        total_empty = 0
        total_passed = 0
        total_existing = 0
        total_failed = 0

        print("\n===== DYNAMIC RESULT =====")
        for section_title in CORE_FORM_SECTIONS:
            results = results_by_section.get(section_title, [])
            if not results:
                print(f"{section_title}: 未完成")
                continue
            summary = summarize_results(results)
            semantic = len(results)
            total_semantic += semantic
            total_empty += summary["empty_field_attempts"]
            total_passed += summary["passed"]
            total_existing += summary["skipped_existing"]
            total_failed += summary["failed_or_unsupported"]
            print(
                f"{section_title}: semantic={semantic}, "
                f"empty targets={summary['empty_field_attempts']}, "
                f"PASS={summary['passed']}, existing={summary['skipped_existing']}, "
                f"failed/unsupported={summary['failed_or_unsupported']}"
            )

        try:
            page.screenshot(path=str(screenshot), full_page=True)
            print(f"整页截图：{screenshot.resolve()}")
        except Exception as exc:
            print(f"截图失败（不影响页面现场）：{exc}")

        print(
            f"TOTAL(runtime): semantic={total_semantic}, empty targets={total_empty}, "
            f"PASS={total_passed}, existing={total_existing}, failed/unsupported={total_failed}"
        )
        print("页面现场现在故意保留：不要刷新、不要点 Cancel、不要点 Save。")
        print("脚本不会清掉测试值；请直接切到 Edge 从上到下检查。")
        harness.detach()

    success = (
        runtime_error is None
        and len(results_by_section) == len(CORE_FORM_SECTIONS)
        and total_failed == 0
        and total_passed == total_empty
    )
    if success:
        print("VISUAL FILL READY")
        return 0
    print("VISUAL FILL INCOMPLETE — 页面已填现场仍保留供检查。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
