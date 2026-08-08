"""Build a live Makro fill plan without writing any field.

The command joins three already-separated layers:
1. current Makro DOM discovery,
2. customer QA -> live-field deterministic matching,
3. evidence-grounded answer resolution.

It produces a field-by-field READY/BLOCKED report and never fills, saves or
submits the listing. This is the final audit boundary before enabling real data
writes in the browser layer.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.alias_config import load_alias_config
from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness
from app.fill_plan import build_live_fill_plan
from app.fill_plan_report import write_fill_plan_json, write_fill_plan_xlsx
from app.makro import MAKRO_HOME_URL, is_listing_url
from app.makro.direct_visual_hold import is_listing_attribute_field
from app.makro.domain import MakroDomainAdapter
from app.makro.listing_preflight import CORE_FORM_SECTIONS
from app.qa_catalog import load_question_catalog
from app.resolution_engine import ResolutionPolicy
from app.resolver_inputs import ResolutionInputSpec, build_resolution_inputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读扫描当前 Makro listing，生成真实字段级 READY/BLOCKED 填写计划。"
    )
    parser.add_argument("--qa", required=True)
    parser.add_argument("--sku", default="")
    parser.add_argument("--expected-model", default="")
    parser.add_argument("--expected-brand", default="")
    parser.add_argument("--product-table", default=None)
    parser.add_argument("--facts-json", action="append", default=[])
    parser.add_argument("--evidence-packet", action="append", default=[])
    parser.add_argument("--supplier-snapshot", action="append", default=[])
    parser.add_argument("--official-snapshot", action="append", default=[])
    parser.add_argument("--supplemental-text", default="")
    parser.add_argument("--supplemental-text-file", default=None)
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--product-url", default=None)
    parser.add_argument("--expected-vertical", required=True)
    parser.add_argument(
        "--alias-config",
        default=None,
        help=(
            "可选 JSON：经过人工审核的 QA question -> Makro label 显式别名。"
            "配置必须声明与 --expected-vertical 相同的 vertical。"
        ),
    )
    parser.add_argument("--auto-fill-min-confidence", type=float, default=0.85)
    parser.add_argument("--ai-auto-fill-min-confidence", type=float, default=0.92)
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    parser.add_argument("--output-dir", default="logs/makro-fill-plan")
    return parser


def _input_spec(args: argparse.Namespace) -> ResolutionInputSpec:
    return ResolutionInputSpec(
        sku=args.sku,
        expected_model=args.expected_model,
        expected_brand=args.expected_brand,
        product_table=args.product_table,
        facts_json=tuple(args.facts_json),
        evidence_packets=tuple(args.evidence_packet),
        supplier_snapshots=tuple(args.supplier_snapshot),
        official_snapshots=tuple(args.official_snapshot),
        supplemental_text=args.supplemental_text,
        supplemental_text_file=args.supplemental_text_file,
        image_paths=tuple(args.image),
        product_url=args.product_url,
    )


def _assert_single_listing_tab(context: Any) -> None:
    listing_pages = [page for page in context.pages if is_listing_url(page.url)]
    if len(listing_pages) <= 1:
        return
    raise RuntimeError(
        "检测到多个 Add a Single Listing 标签页；只读 planner 也拒绝猜目标页面。"
        "请只保留一个 listing 标签页后重试。"
    )


def _assert_no_unsaved_section(adapter: MakroDomainAdapter) -> None:
    expanded: list[str] = []
    for title in CORE_FORM_SECTIONS:
        section = adapter.find_section(title)
        if section is not None and not section.get("has_edit"):
            expanded.append(title)
    if expanded:
        raise RuntimeError(
            "检测到仍处于编辑状态的 section："
            + " | ".join(expanded)
            + "。为避免扫描时 Cancel 掉人工未保存内容，planner 已停止。"
        )


def main() -> int:
    args = build_parser().parse_args()
    for name, value in (
        ("auto-fill-min-confidence", args.auto_fill_min_confidence),
        ("ai-auto-fill-min-confidence", args.ai_auto_fill_min_confidence),
    ):
        if not 0.0 <= value <= 1.0:
            raise SystemExit(f"--{name} 必须在 0..1")

    catalog = load_question_catalog(args.qa)
    input_result = build_resolution_inputs(catalog, _input_spec(args))
    policy = ResolutionPolicy(
        auto_fill_min_confidence=args.auto_fill_min_confidence,
        ai_auto_fill_min_confidence=args.ai_auto_fill_min_confidence,
    )
    alias_config = (
        load_alias_config(args.alias_config, expected_vertical=args.expected_vertical)
        if args.alias_config
        else None
    )

    with sync_playwright() as playwright:
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
        _assert_single_listing_tab(harness.context)
        adapter.assert_expected_vertical(args.expected_vertical)
        _assert_no_unsaved_section(adapter)

        sections_payload, flat_controls, scan_stats = adapter.scan_sections(
            include_values=False,
            wait_ms=args.scroll_wait_ms,
            max_scroll_steps=args.max_scroll_steps,
        )
        all_semantic_fields = adapter.build_semantic_fields(flat_controls)
        semantic_fields = [
            field for field in all_semantic_fields if is_listing_attribute_field(field)
        ]

        plan = build_live_fill_plan(
            catalog,
            semantic_fields,
            input_result.bundle,
            policy=policy,
            aliases=alias_config.aliases if alias_config else None,
        )

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = Path(args.output_dir) / f"plan-{stamp}"
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = write_fill_plan_json(plan, output_dir / "fill-plan.json")
        xlsx_path = write_fill_plan_xlsx(plan, output_dir / "fill-plan.xlsx")
        manifest = output_dir / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "mode": "read_only_fill_plan",
                    "page_url": page.url,
                    "expected_vertical": args.expected_vertical,
                    "qa_source": str(Path(args.qa).resolve()),
                    "alias_config": alias_config.source_path if alias_config else None,
                    "semantic_fields_before_filter": len(all_semantic_fields),
                    "listing_attribute_fields": len(semantic_fields),
                    "scan": scan_stats,
                    "sections": [item.get("title") for item in sections_payload],
                    "evidence_items": len(input_result.bundle.evidence),
                    "evidence_packet_files": input_result.evidence_packet_files,
                    "source_snapshot_files": input_result.source_snapshot_files,
                    "evidence_warnings": input_result.warnings,
                    "plan_summary": plan.summary(),
                    "writes_performed": 0,
                    "save_clicked": False,
                    "send_to_qc_clicked": False,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        summary = plan.summary()
        print("===== MAKRO LIVE FILL PLAN =====")
        print(f"page={page.url}")
        print(
            f"live_fields={summary['live_field_count']}, ready={summary['ready']}, "
            f"preview_eligible={summary['preview_eligible']}, blocked={summary['blocked']}"
        )
        print(
            f"required_ready={summary['required_ready']}, "
            f"required_preview_eligible={summary['required_preview_eligible']}, "
            f"required_blocked={summary['required_blocked']}"
        )
        print(
            f"qa_matched={summary['qa_matched']}, qa_unmatched={summary['qa_unmatched']}, "
            f"qa_ambiguous={summary['qa_ambiguous']}"
        )
        if alias_config:
            print(f"alias_config={alias_config.source_path}")
        print(f"JSON={json_path.resolve()}")
        print(f"XLSX={xlsx_path.resolve()}")
        print(f"Manifest={manifest.resolve()}")
        print("只读完成：没有填写字段，没有 Save，没有 Send to QC。")

        # Edge is the external long-lived browser. Do not close browser/context.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
