"""Makro full field-behavior contract audit.

This is the missing closure between schema discovery and production execution.
For the current Vertical it:

1. inventories every live semantic field in every non-photo section;
2. fingerprints the field's DOM/control contract without using its label;
3. runs the existing safe synthetic coverage on empty fields only;
4. grades evidence conservatively (repeatable fields need repeatable commit proof);
5. merges evidence into a durable cross-Vertical behavior registry;
6. optionally compares audited Verticals against the full loadsheet schema registry.

Synthetic values are always discarded with Cancel.  This runner never clicks Save
or Send to QC and therefore cannot claim platform persistence acceptance; its job
is to prove the browser-side execution contract before production code is widened.
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
from app.makro.behavior_contract import (
    FieldContractObservation,
    build_run_contracts,
    marketplace_completeness,
    merge_contract_registry,
    observe_field,
)
from app.makro.coverage import cancel_section, run_section_coverage
from app.makro.domain import MakroDomainAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Makro 字段行为契约全量审计：扫描当前 Vertical 所有非图片 section，"
            "按真实 DOM 契约归类并安全验证；绝不 Save / Send to QC。"
        )
    )
    parser.add_argument(
        "--expected-vertical",
        required=True,
        help="安全门：必须与当前 Add Listing URL 的 vertical 完全一致。",
    )
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--logs-dir", default="logs/makro-contract-audit")
    parser.add_argument(
        "--registry",
        default="logs/makro-contract-audit/control-contract-registry.json",
        help="跨 Vertical 累积的行为契约 registry。",
    )
    parser.add_argument(
        "--schema-registry",
        default="",
        help=(
            "可选：makro_harvest_schema.py 生成的 makro-schema-registry.json。"
            "提供后会计算整个 Makro Vertical 清单还缺哪些审计。"
        ),
    )
    parser.add_argument(
        "--require-marketplace-complete",
        action="store_true",
        help="有 schema registry 时，若仍有未审计/未完整验证 Vertical 则返回非零。",
    )
    parser.add_argument("--recheck-wait-ms", type=int, default=800)
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    return parser


def _assert_single_listing_tab(context: Any) -> int:
    listing_pages = [page for page in context.pages if is_listing_url(page.url)]
    if len(listing_pages) <= 1:
        return len(listing_pages)
    print("发现多个 Add a Single Listing 标签页；contract audit 不会猜目标：")
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


def _non_photo_sections(adapter: MakroDomainAdapter) -> list[str]:
    titles: list[str] = []
    for section in adapter.find_sections():
        title = base_section_title(str(section.get("title") or ""))
        if not title or title.casefold() == "product photos":
            continue
        if title not in titles:
            titles.append(title)
    return titles


def _require_collapsed(adapter: MakroDomainAdapter, section_title: str) -> dict[str, Any]:
    section = adapter.find_section(section_title)
    if section is None:
        raise RuntimeError(f"当前页面找不到 section：{section_title}")
    if not section.get("has_edit"):
        raise RuntimeError(
            f"section {section_title!r} 当前不是折叠状态；"
            "为避免 Cancel 丢掉用户已有未保存内容，contract audit 已停止。"
        )
    return section


def _inventory_section(
    adapter: MakroDomainAdapter,
    section_title: str,
    *,
    wait_ms: int,
    max_scroll_steps: int,
) -> list[FieldContractObservation]:
    """Observe every field contract in one section without changing any value."""

    section = _require_collapsed(adapter, section_title)
    adapter.open_section_for_edit(section)
    try:
        live = adapter.find_section(section_title) or section
        section_path = str(live.get("path") or "")
        if not section_path:
            raise RuntimeError(f"section {section_title!r} 缺少 DOM path。")
        controls = adapter.scan_section_fields(
            section_path,
            include_values=True,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        fields = adapter.build_semantic_fields(controls)
        return [observe_field(field) for field in fields]
    finally:
        cancel_section(adapter, section_title)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} 不是 JSON object。")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _print_contract_summary(run_payload: dict[str, Any]) -> None:
    stats = run_payload["stats"]
    print("\n===== CURRENT VERTICAL CONTRACTS =====")
    print(
        f"fields={stats['observed_field_count']}  "
        f"unique_contracts={stats['unique_contract_count']}  "
        f"verified={stats['verified_contract_count']}  "
        f"unverified={stats['unverified_contract_count']}"
    )
    if stats["unverified_contract_count"]:
        print("未验证 contract（不会被误报为已掌握）：")
        for fingerprint, entry in run_payload["contracts"].items():
            if entry["verified_for_execution"]:
                continue
            signature = entry["signature"]
            examples = entry.get("observations") or []
            example = examples[0] if examples else {}
            print(
                f"  {fingerprint}  {signature.get('base_family')} / "
                f"{signature.get('commit_model')} / {signature.get('qualifier_model')}  "
                f"example={example.get('label') or example.get('attribute_key') or '?'}"
            )
            if example.get("verification_detail"):
                print(f"    {example['verification_detail']}")


def _print_marketplace_completeness(payload: dict[str, Any]) -> None:
    print("\n===== MARKETPLACE COVERAGE =====")
    print(
        f"audited={payload['audited_vertical_count']}/{payload['expected_vertical_count']}  "
        f"marketplace_complete={payload['marketplace_complete']}"
    )
    if payload["missing_verticals"]:
        print(f"missing_verticals={len(payload['missing_verticals'])}")
        for item in payload["missing_verticals"]:
            print(f"  - {item}")
    if payload["incomplete_verticals"]:
        print(f"incomplete_verticals={len(payload['incomplete_verticals'])}")
        for item in payload["incomplete_verticals"]:
            print(f"  - {item}")


def main() -> int:
    args = build_parser().parse_args()
    profile_dir = Path(args.profile_dir).resolve()
    logs_dir = Path(args.logs_dir)
    registry_path = Path(args.registry)
    logs_dir.mkdir(parents=True, exist_ok=True)

    print("安全模式：DOM-first behavior contract audit；所有合成值只 Cancel，不 Save。")
    print(f"预期 vertical：{args.expected_vertical}")
    print(f"长期 Edge CDP：127.0.0.1:{args.cdp_port}")

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
        sections = _non_photo_sections(adapter)
        if not sections:
            raise RuntimeError("当前 listing 没有发现可审计的非图片 section。")

        print(f"操作标签页：{page.url}")
        print(f"sections：{', '.join(sections)}")

        observations: list[FieldContractObservation] = []
        results: list[Any] = []
        section_reports: list[dict[str, Any]] = []

        # Phase A: first inventory every live field.  This means an existing value
        # can never disappear from coverage merely because we refuse to overwrite it.
        for section_title in sections:
            items = _inventory_section(
                adapter,
                section_title,
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
            )
            observations.extend(items)
            print(f"[inventory] {section_title}: fields={len(items)}")

        # Phase B: exercise only safe empty fields using the existing isolated
        # open -> write -> readback -> Cancel transactions.
        for section_title in sections:
            print(f"\n===== {section_title} / BEHAVIOR =====")
            section_results = run_section_coverage(
                adapter,
                section_title,
                recheck_wait_ms=args.recheck_wait_ms,
                exercise_multi_value=True,
                wait_ms=args.scroll_wait_ms,
                max_scroll_steps=args.max_scroll_steps,
            )
            results.extend(section_results)
            passed = sum(1 for item in section_results if item.status == "pass")
            skipped = sum(1 for item in section_results if item.status == "skipped_existing")
            failed = len(section_results) - passed - skipped
            print(
                f"behavior results={len(section_results)} pass={passed} "
                f"failed/unsupported={failed} existing_skipped={skipped}"
            )
            section_reports.append(
                {
                    "section": section_title,
                    "results": [item.as_dict() for item in section_results],
                }
            )

        run_payload = build_run_contracts(
            vertical=args.expected_vertical,
            observations=observations,
            results=results,
            source_url=page.url,
        )
        _print_contract_summary(run_payload)

        existing_registry = _read_json(registry_path)
        merged_registry = merge_contract_registry(existing_registry, run_payload)

        schema_completeness: dict[str, Any] | None = None
        if args.schema_registry:
            schema_path = Path(args.schema_registry)
            schema_payload = _read_json(schema_path)
            schema_completeness = marketplace_completeness(schema_payload, merged_registry)
            merged_registry["marketplace_completeness"] = schema_completeness
            merged_registry["schema_registry"] = str(schema_path.resolve())
            _print_marketplace_completeness(schema_completeness)
        elif args.require_marketplace_complete:
            raise RuntimeError("--require-marketplace-complete 必须同时提供 --schema-registry。")

        _write_json(registry_path, merged_registry)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = logs_dir / f"contract-audit-{stamp}.json"
        report = {
            "mode": "makro_behavior_contract_audit",
            "expected_vertical": args.expected_vertical,
            "page_url": page.url,
            "listing_tab_count": listing_tab_count,
            "sections": sections,
            "section_behavior": section_reports,
            "observations": [item.as_dict() for item in observations],
            "run_contracts": run_payload,
            "registry_path": str(registry_path.resolve()),
            "marketplace_completeness": schema_completeness,
            "save_clicked": False,
            "send_to_qc_clicked": False,
        }
        _write_json(report_path, report)

        print("\n===== OUTPUT =====")
        print(f"本次报告：{report_path.resolve()}")
        print(f"累积 registry：{registry_path.resolve()}")
        print("长期 Edge 保持打开；没有 Save / Send to QC。")
        harness.detach()

    current_complete = bool(run_payload["stats"]["fully_verified"])
    if not current_complete:
        return 2
    if (
        args.require_marketplace_complete
        and schema_completeness is not None
        and not schema_completeness["marketplace_complete"]
    ):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
