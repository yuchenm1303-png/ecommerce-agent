"""Build an auditable answer-resolution report before touching Makro.

This command is browser-free. It loads every question from the customer's QA
workbook, merges explicit evidence sources, resolves what can be justified, and
exports JSON/XLSX with answer, source, confidence, provenance and autofill gate.
It does not invent answers for missing evidence and does not modify Makro.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.qa_catalog import load_question_catalog
from app.resolution_engine import ResolutionPolicy, resolve_catalog, summarize_resolution
from app.resolution_report import write_resolution_json, write_resolution_xlsx
from app.resolver_inputs import ResolutionInputSpec, build_resolution_inputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "读取客户 QA 问题清单 + 明确证据，输出 Answer/Source/Confidence/Status；"
            "本命令不打开浏览器、不填写 Makro。"
        )
    )
    parser.add_argument("--qa", required=True, help="客户 Question/Answer .xlsx/.xlsm/.csv")
    parser.add_argument("--sku", default="", help="SKU；商品表包含多行时必须提供")
    parser.add_argument("--expected-model", default="", help="可选身份安全门：预期 Model Number")
    parser.add_argument("--expected-brand", default="", help="可选身份安全门：预期 Brand")
    parser.add_argument("--product-table", default=None, help="可选：结构化商品/经营数据表")
    parser.add_argument(
        "--facts-json",
        action="append",
        default=[],
        help="可重复：人工/确定性抽取后的可信结构化 facts JSON",
    )
    parser.add_argument(
        "--evidence-packet",
        action="append",
        default=[],
        help=(
            "可重复：图片/网页/AI 抽取结果。必须包含 product_identity、facts、"
            "source_reference、evidence_text、confidence。"
        ),
    )
    parser.add_argument(
        "--supplier-snapshot",
        action="append",
        default=[],
        help="可重复：makro_capture_source.py 产生的供应商页面 snapshot JSON。",
    )
    parser.add_argument(
        "--official-snapshot",
        action="append",
        default=[],
        help="可重复：官方页面 snapshot JSON。",
    )
    parser.add_argument("--supplemental-text", default="", help="仅解析明确的 key: value 行；自由文本不会猜")
    parser.add_argument("--supplemental-text-file", default=None, help="可选 UTF-8 文本；仅解析 key: value 行")
    parser.add_argument("--image", action="append", default=[], help="记录商品图片路径；模型识图模块单独生成 evidence packet")
    parser.add_argument("--product-url", default=None, help="记录商品/供应商 URL")
    parser.add_argument("--auto-fill-min-confidence", type=float, default=0.85)
    parser.add_argument("--ai-auto-fill-min-confidence", type=float, default=0.92)
    parser.add_argument("--output-dir", default="logs/answer-resolver")
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
    bundle = input_result.bundle
    policy = ResolutionPolicy(
        auto_fill_min_confidence=args.auto_fill_min_confidence,
        ai_auto_fill_min_confidence=args.ai_auto_fill_min_confidence,
    )
    records = resolve_catalog(catalog, bundle, policy=policy)
    summary = summarize_resolution(records)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / f"resolve-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_resolution_json(records, output_dir / "resolution.json")
    xlsx_path = write_resolution_xlsx(records, output_dir / "resolution.xlsx")
    evidence_manifest = output_dir / "evidence-manifest.json"
    evidence_manifest.write_text(
        json.dumps(
            {
                "identity_guard": {
                    "sku": input_result.expected_identity.sku,
                    "model_number": input_result.expected_identity.model_number,
                    "brand": input_result.expected_identity.brand,
                },
                "qa_source": str(Path(args.qa).resolve()),
                "evidence_packet_files": input_result.evidence_packet_files,
                "source_snapshot_files": input_result.source_snapshot_files,
                "evidence_items": len(bundle.evidence),
                "warnings": input_result.warnings,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("===== ANSWER RESOLVER REPORT =====")
    print(f"QA: {Path(args.qa).resolve()}")
    identity = input_result.expected_identity
    if any((identity.sku, identity.model_number, identity.brand)):
        print(
            "identity guard: "
            f"sku={identity.sku or '-'}, model={identity.model_number or '-'}, "
            f"brand={identity.brand or '-'}"
        )
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
    if input_result.warnings:
        print(f"evidence_warnings={len(input_result.warnings)}（详见 evidence-manifest.json）")
    print(f"JSON: {json_path.resolve()}")
    print(f"XLSX: {xlsx_path.resolve()}")
    print(f"Evidence manifest: {evidence_manifest.resolve()}")
    print("本阶段只做解析报告；没有打开 Makro，也没有填写任何页面。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
