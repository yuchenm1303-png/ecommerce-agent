"""Prepare the exact source-grounded request for a future vision/web model.

This command does not call a model and does not touch Makro. It turns the
customer QA plus captured source snapshots/images into a bounded request where
all sources have stable ids that can be validated later.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.evidence_contract import ProductIdentity
from app.qa_catalog import load_question_catalog
from app.semantic_extraction import build_grounded_semantic_request
from app.semantic_grounding import build_grounding_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成严格 source-id 约束的图片/网页语义抽取请求；不调用模型、不修改 Makro。"
    )
    parser.add_argument("--qa", required=True)
    parser.add_argument("--sku", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--brand", default="")
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--supplier-snapshot", action="append", default=[])
    parser.add_argument("--official-snapshot", action="append", default=[])
    parser.add_argument("--supplemental-text", default="")
    parser.add_argument("--supplemental-text-file", default=None)
    parser.add_argument("--max-text-chars", type=int, default=3000)
    parser.add_argument("--overlap-chars", type=int, default=250)
    parser.add_argument("--output-dir", default="logs/semantic-extraction")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    supplemental_text = args.supplemental_text
    if args.supplemental_text_file:
        file_text = Path(args.supplemental_text_file).read_text(encoding="utf-8")
        supplemental_text = "\n".join(
            part for part in (supplemental_text, file_text) if part.strip()
        )

    catalog = load_question_catalog(args.qa)
    grounding = build_grounding_catalog(
        image_paths=args.image,
        supplier_snapshots=args.supplier_snapshot,
        official_snapshots=args.official_snapshot,
        supplemental_text=supplemental_text,
        max_text_chars=args.max_text_chars,
        overlap_chars=args.overlap_chars,
    )
    request = build_grounded_semantic_request(
        catalog,
        grounding,
        identity=ProductIdentity(
            sku=args.sku,
            model_number=args.model,
            brand=args.brand,
        ),
    )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / f"grounded-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    request_path = output_dir / "semantic-request.json"
    manifest_path = output_dir / "source-manifest.json"
    request_path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(grounding.as_manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("===== GROUNDED SEMANTIC REQUEST READY =====")
    print(f"questions={len(catalog.questions)}")
    print(f"grounded_sources={len(grounding.sources)}")
    print(f"images={len(args.image)}")
    print(f"supplier_snapshots={len(args.supplier_snapshot)}")
    print(f"official_snapshots={len(args.official_snapshot)}")
    print(f"request={request_path.resolve()}")
    print(f"manifest={manifest_path.resolve()}")
    print("未调用模型；未访问 Makro；未填写、Save 或 Send to QC。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
