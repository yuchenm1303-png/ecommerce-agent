"""Read-only Makro live-schema scanner and Fill Plan builder.

The final plan rebinds the AI packet only to the exact sources captured from the
supplier URL. Old QA answers and manually supplied seller SKU are not product
identity inputs. SKU is generated mechanically as a fresh seller identifier for
each planning attempt.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from app.ai_decisions import load_ai_decision_packet
from app.browser_page_owner import find_page_by_target_id
from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness
from app.business_fields import generate_listing_sku, generated_business_bundle
from app.evidence_contract import ProductIdentity
from app.fill_plan import build_live_fill_plan
from app.fill_plan_report import write_fill_plan_json, write_fill_plan_xlsx
from app.live_schema import assert_live_schema_matches, load_live_schema, write_live_schema
from app.makro import MAKRO_HOME_URL, is_listing_url
from app.makro.direct_visual_hold import is_listing_attribute_field
from app.makro.domain import MakroDomainAdapter
from app.makro.listing_preflight import CORE_FORM_SECTIONS
from app.semantic_grounding import build_grounding_catalog

_BATCH_TARGET_ENV = "MAKRO_BATCH_TARGET_ID"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "只读 Makro planner：首次扫描 live schema，或把单一商品链接 Resolver 的 "
            "AI decisions 重新绑定到同一批原始来源后生成 Fill Plan。"
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--scan-live-schema",
        action="store_true",
        help="首次只读扫描当前 Makro 页面并导出 live-schema.json。",
    )
    mode.add_argument(
        "--decision-packet",
        help="makro_resolve_ai.py 生成的 ai-decisions.json；用于最终只读 Fill Plan。",
    )
    parser.add_argument("--live-schema", default=None, help="最终 Fill Plan 模式必填。")
    parser.add_argument("--product-url", default=None, help="最终 Fill Plan 模式必填；必须与 Resolver 使用同一商品链接。")
    parser.add_argument("--supplier-snapshot", action="append", default=[])
    parser.add_argument("--official-snapshot", action="append", default=[])
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--expected-vertical", required=True)
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument(
        "--makro-target-id",
        default=os.environ.get(_BATCH_TARGET_ENV, ""),
        help=(
            "Batch-only owned-tab token. When supplied, plan only that exact Chromium tab; "
            "single-mode unique-tab safety remains unchanged when omitted."
        ),
    )
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)
    parser.add_argument("--max-text-chars", type=int, default=5000)
    parser.add_argument("--overlap-chars", type=int, default=250)
    parser.add_argument("--output-dir", default="logs/makro-fill-plan")
    return parser


def _validate_mode(args: argparse.Namespace) -> None:
    if args.scan_live_schema:
        if args.live_schema:
            raise SystemExit("--scan-live-schema 不接受 --live-schema；它本身就是生成 live schema。")
        return
    if not args.live_schema:
        raise SystemExit("最终 Fill Plan 模式必须提供 --live-schema。")
    if not str(args.product_url or "").strip():
        raise SystemExit("最终 Fill Plan 模式必须提供与 Resolver 相同的 --product-url。")


def _assert_single_listing_tab(context: Any) -> None:
    listing_pages = [page for page in context.pages if is_listing_url(page.url)]
    if len(listing_pages) <= 1:
        return
    raise RuntimeError(
        "检测到多个 Add a Single Listing 标签页；只读 planner 拒绝猜目标页面。"
    )


def _owned_listing_page(harness: EdgeHarness, target_id: str):
    """Resolve one Batch-owned page without ever falling back to another tab."""

    if harness.context is None:
        raise RuntimeError("Makro Edge context is unavailable")
    page = find_page_by_target_id(harness.context, target_id)
    page.set_default_timeout(15_000)
    adapter = MakroDomainAdapter(page)
    if not adapter.is_listing_page():
        raise RuntimeError(
            "Batch owned Makro tab no longer points at an Add Listing page; "
            "read-only planner refuses to navigate or adopt another tab."
        )
    harness.page = page
    return page


def _assert_no_unsaved_section(adapter: MakroDomainAdapter) -> None:
    expanded: list[str] = []
    for title in CORE_FORM_SECTIONS:
        section = adapter.find_section(title)
        if section is not None and not section.get("has_edit"):
            expanded.append(title)
    photo = adapter.find_section("Product Photos")
    if photo is not None and not photo.get("has_edit"):
        expanded.append("Product Photos")
    if expanded:
        raise RuntimeError(
            "检测到仍处于编辑状态的 section："
            + " | ".join(expanded)
            + "。为避免扫描影响人工未保存内容，planner 已停止。"
        )


def _scan_live_fields(
    adapter: MakroDomainAdapter,
    *,
    wait_ms: int,
    max_scroll_steps: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], int]:
    sections_payload, flat_controls, scan_stats = adapter.scan_sections(
        include_values=False,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    all_semantic_fields = adapter.build_semantic_fields(flat_controls)
    semantic_fields = [
        field for field in all_semantic_fields if is_listing_attribute_field(field)
    ]
    return semantic_fields, sections_payload, scan_stats, len(all_semantic_fields)


def main() -> int:
    args = build_parser().parse_args()
    _validate_mode(args)
    if args.max_text_chars < 500:
        raise SystemExit("--max-text-chars 不能小于 500")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_text_chars:
        raise SystemExit("--overlap-chars 必须 >=0 且小于 --max-text-chars")

    decision_packet = None
    business_bundle = None
    generated_sku: str | None = None
    planned_live_fields: list[dict[str, Any]] | None = None
    if not args.scan_live_schema:
        planned_live_fields = load_live_schema(args.live_schema)
        grounding = build_grounding_catalog(
            image_paths=args.image,
            supplier_snapshots=args.supplier_snapshot,
            official_snapshots=args.official_snapshot,
            max_text_chars=args.max_text_chars,
            overlap_chars=args.overlap_chars,
        )
        decision_packet = load_ai_decision_packet(
            args.decision_packet,
            planned_live_fields,
            grounding,
            expected_identity=ProductIdentity(),
        )
        generated_sku = generate_listing_sku(str(args.product_url))
        business_bundle = generated_business_bundle(
            str(args.product_url),
            sku=generated_sku,
        )

    with sync_playwright() as playwright:
        harness = EdgeHarness(
            playwright,
            profile_dir=Path(args.profile_dir).resolve(),
            port=args.cdp_port,
            start_url=MAKRO_HOME_URL,
        )
        if harness.context is None:
            raise RuntimeError("Makro Edge context is unavailable")

        if args.makro_target_id:
            page = _owned_listing_page(harness, args.makro_target_id)
        else:
            page = harness.page
            if page is None:
                raise RuntimeError("Makro Edge did not expose a usable page")
            page.set_default_timeout(15_000)
            adapter = MakroDomainAdapter(page)
            if not adapter.is_listing_page():
                adapter.wait_for_authenticated_listing(
                    MAKRO_HOME_URL,
                    headless=False,
                    navigate_first=harness.launched_now,
                )
            page = harness.ensure_page()
            _assert_single_listing_tab(harness.context)

        # Resolve the owned Batch page a second time immediately before the scan.
        # A missing/closed target must fail closed rather than silently switching
        # to another concurrent job's listing tab.
        if args.makro_target_id:
            page = _owned_listing_page(harness, args.makro_target_id)
        adapter = MakroDomainAdapter(page)
        adapter.assert_expected_vertical(args.expected_vertical)
        _assert_no_unsaved_section(adapter)

        semantic_fields, sections_payload, scan_stats, all_field_count = _scan_live_fields(
            adapter,
            wait_ms=args.scroll_wait_ms,
            max_scroll_steps=args.max_scroll_steps,
        )

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = Path(args.output_dir) / (
            f"live-scan-{stamp}" if args.scan_live_schema else f"plan-{stamp}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        live_schema_path = write_live_schema(semantic_fields, output_dir / "live-schema.json")

        if args.scan_live_schema:
            manifest = output_dir / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "mode": "read_only_live_schema_scan",
                        "page_url": page.url,
                        "makro_target_id": str(args.makro_target_id or ""),
                        "expected_vertical": args.expected_vertical,
                        "live_schema": str(live_schema_path.resolve()),
                        "semantic_fields_before_filter": all_field_count,
                        "listing_attribute_fields": len(semantic_fields),
                        "scan": scan_stats,
                        "sections": [item.get("title") for item in sections_payload],
                        "writes_performed": 0,
                        "save_clicked": False,
                        "send_to_qc_clicked": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print("===== MAKRO LIVE SCHEMA SCAN =====")
            print(f"page={page.url}")
            if args.makro_target_id:
                print(f"makro_target_id={args.makro_target_id}")
            print(f"live_fields={len(semantic_fields)}")
            print(f"Live schema={live_schema_path.resolve()}")
            print(f"Manifest={manifest.resolve()}")
            print("只读完成：没有 AI、没有填写字段、没有 Save、没有 Send to QC。")
            return 0

        assert planned_live_fields is not None
        assert decision_packet is not None
        assert business_bundle is not None
        assert generated_sku is not None
        assert_live_schema_matches(planned_live_fields, semantic_fields)
        plan = build_live_fill_plan(
            decision_packet,
            semantic_fields,
            business_bundle,
        )

        json_path = write_fill_plan_json(plan, output_dir / "fill-plan.json")
        xlsx_path = write_fill_plan_xlsx(plan, output_dir / "fill-plan.xlsx")
        manifest = output_dir / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "mode": "read_only_single_url_ai_decision_fill_plan",
                    "page_url": page.url,
                    "makro_target_id": str(args.makro_target_id or ""),
                    "expected_vertical": args.expected_vertical,
                    "product_url": str(args.product_url),
                    "generated_listing_sku": generated_sku,
                    "decision_packet": str(Path(args.decision_packet).resolve()),
                    "input_live_schema": str(Path(args.live_schema).resolve()),
                    "output_live_schema": str(live_schema_path.resolve()),
                    "live_schema_verified": True,
                    "semantic_fields_before_filter": all_field_count,
                    "listing_attribute_fields": len(semantic_fields),
                    "scan": scan_stats,
                    "sections": [item.get("title") for item in sections_payload],
                    "decision_warnings": decision_packet.warnings,
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
        print("===== MAKRO AI-DECISION FILL PLAN =====")
        print(f"page={page.url}")
        if args.makro_target_id:
            print(f"makro_target_id={args.makro_target_id}")
        print(f"generated_listing_sku={generated_sku}")
        print(
            f"live_fields={summary['live_field_count']}, ready={summary['ready']}, "
            f"preview_eligible={summary['preview_eligible']}, blocked={summary['blocked']}"
        )
        print(
            f"required_ready={summary['required_ready']}, "
            f"required_preview_eligible={summary['required_preview_eligible']}, "
            f"required_blocked={summary['required_blocked']}"
        )
        print(f"gate_counts={summary['gate_counts']}")
        print(f"Live schema={live_schema_path.resolve()}")
        print(f"JSON={json_path.resolve()}")
        print(f"XLSX={xlsx_path.resolve()}")
        print(f"Manifest={manifest.resolve()}")
        print("只读完成：没有填写字段，没有 Save，没有 Send to QC。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
