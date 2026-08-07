"""Prepare a strict provider-neutral extraction request from the customer's QA sheet."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.evidence_contract import ProductIdentity
from app.extraction_request import build_extraction_request_payload
from app.qa_catalog import load_question_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把完整 QA 问题清单整理成图片/网页/AI 证据抽取请求；不调用任何模型。"
    )
    parser.add_argument("--qa", required=True)
    parser.add_argument("--sku", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--brand", default="")
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--product-url", default=None)
    parser.add_argument("--supplemental-text", default="")
    parser.add_argument("--output-dir", default="logs/evidence-extraction")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    catalog = load_question_catalog(args.qa)
    payload = build_extraction_request_payload(
        catalog,
        identity=ProductIdentity(sku=args.sku, model_number=args.model, brand=args.brand),
        image_paths=tuple(args.image),
        product_url=args.product_url,
        supplemental_text=args.supplemental_text,
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = Path(args.output_dir) / f"extraction-request-{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    locked = sum(1 for item in payload["questions"] if item["business_locked"])
    print("===== EVIDENCE EXTRACTION REQUEST READY =====")
    print(f"questions={len(payload['questions'])}, business_locked={locked}")
    print(f"images={len(args.image)}, product_url={'yes' if args.product_url else 'no'}")
    print(f"output={output.resolve()}")
    print("未调用模型；未访问网页；未修改 Makro。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
