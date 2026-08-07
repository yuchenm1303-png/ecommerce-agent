"""Build an auditable answer-resolution report before touching Makro.

This command is browser-free. It loads every question from the customer's QA
workbook, merges explicit evidence sources, resolves what can be justified, and
exports JSON/XLSX with answer, source, confidence, provenance and autofill gate.
It does not invent answers for missing evidence and does not modify Makro.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from app.evidence_pipeline import (
    bundle_from_catalog_answers,
    bundle_from_facts_json,
    bundle_from_key_value_text,
    merge_bundles,
)
from app.qa_catalog import load_question_catalog
from app.resolution_engine import ResolutionPolicy, resolve_catalog, summarize_resolution
from app.resolution_report import write_resolution_json, write_resolution_xlsx
from app.source_bundle import ProductSourceBundle, bundle_from_product_table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "读取客户 QA 问题清单 + 明确证据，输出 Answer/Source/Confidence/Status；"
            "本命令不打开浏览器、不填写 Makro。"
        )
    )
    parser.add_argument("--qa", required=True, help="客户 Question/Answer .xlsx/.xlsm/.csv")
    parser.add_argument("--sku", default="", help="SKU；商品表包含多行时必须提供")
    parser.add_argument("--product-table", default=None, help="可选：结构化商品/经营数据表")
    parser.add_argument(
        "--facts-json",
        action="append",
        default=[],
        help="可重复：图片/网页/人工抽取后的结构化 facts JSON",
    )
    parser.add_argument("--supplemental-text", default="", help="仅解析明确的 key: value 行；自由文本不会猜")
    parser.add_argument("--supplemental-text-file", default=None, help="可选 UTF-8 文本；仅解析 key: value 行")
    parser.add_argument("--image", action="append", default=[], help="记录商品图片路径；当前 CLI 不自行识图")
    parser.add_argument("--product-url", default=None, help="记录商品/供应商 URL；当前 CLI 不自行联网抓取")
    parser.add_argument("--auto-fill-min-confidence", type=float, default=0.85)
    parser.add_argument("--ai-auto-fill-min-confidence", type=float, default=0.92)
    parser.add_argument("--output-dir", default="logs/answer-resolver")
    return parser


def _load_bundle(args: argparse.Namespace, catalog) -> ProductSourceBundle:
    bundles: list[ProductSourceBundle] = [
        bundle_from_catalog_answers(
            catalog,
            sku=args.sku,
            image_paths=args.image,
            product_url=args.product_url,
            supplemental_text=args.supplemental_text,
        )
    ]

    if args.product_table:
        bundles.append(bundle_from_product_table(args.product_table, sku=args.sku or None))

    for path in args.facts_json:
        bundles.append(bundle_from_facts_json(path, sku=args.sku))

    text_parts = [args.supplemental_text]
    if args.supplemental_text_file:
        text_parts.append(Path(args.supplemental_text_file).read_text(encoding="utf-8"))
    explicit_text = "\n".join(part for part in text_parts if part.strip())
    if explicit_text:
        bundles.append(
            bundle_from_key_value_text(
                explicit_text,
                source_reference=args.supplemental_text_file or "--supplemental-text",
            )
        )

    return merge_bundles(*bundles)


def main() -> int:
    args = build_parser().parse_args()
    for name, value in (
        ("auto-fill-min-confidence", args.auto_fill_min_confidence),
        ("ai-auto-fill-min-confidence", args.ai_auto_fill_min_confidence),
    ):
        if not 0.0 <= value <= 1.0:
            raise SystemExit(f"--{name} 必须在 0..1")

    catalog = load_question_catalog(args.qa)
    bundle = _load_bundle(args, catalog)
    policy = ResolutionPolicy(
        auto_fill_min_confidence=args.auto_fill_min_confidence,
        ai_auto_fill_min_confidence=args.ai_auto_fill_min_confidence,
    )
    records = resolve_catalog(catalog, bundle, policy=policy)
    summary = summarize_resolution(records)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / f"resolve-{stamp}"
    json_path = write_resolution_json(records, output_dir / "resolution.json")
    xlsx_path = write_resolution_xlsx(records, output_dir / "resolution.xlsx")

    print("===== ANSWER RESOLVER REPORT =====")
    print(f"QA: {Path(args.qa).resolve()}")
    print(
        f"questions={summary['total']}, explicit_answers={catalog.answered_count}, "
        f"resolved={summary['resolved']}, needs_review={summary['needs_review']}, "
        f"conflict={summary['conflict']}, missing={summary['missing']}"
    )
    print(
        f"eligible_for_autofill={summary['eligible_for_autofill']}, "
        f"blocked={summary['blocked']}"
    )
    print(f"evidence_items={len(bundle.evidence)}")
    print(f"JSON: {json_path.resolve()}")
    print(f"XLSX: {xlsx_path.resolve()}")
    print("本阶段只做解析报告；没有打开 Makro，也没有填写任何页面。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
