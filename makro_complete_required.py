"""Targeted AI completion for unresolved required Makro fields.

This helper is invoked by the GUI immediately before Full Step 3. It does not
open Makro and does not write browser fields. It only produces guarded
required-overrides candidates for the production executor.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.live_schema import load_live_schema
from app.providers.registry import (
    ProviderConfig,
    ProviderConfigurationError,
    build_semantic_provider,
)
from app.required_field_completion import (
    build_required_completion_request,
    parse_required_completion_response,
    required_targets,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="用当前 Resolver 证据只补齐 Full Step 3 尚未解决的 Makro 必填字段。"
    )
    parser.add_argument("--fill-plan", required=True)
    parser.add_argument("--live-schema", required=True)
    parser.add_argument("--resolver-manifest", required=True)
    parser.add_argument("--output", required=True)
    return parser


def _read_object(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {resolved}")
    return payload


def _provider_config(manifest: dict[str, Any]) -> ProviderConfig:
    raw = manifest.get("fact_provider_config") or manifest.get("provider_config") or {}
    if not isinstance(raw, dict):
        raw = {}
    return ProviderConfig(
        provider=str(raw.get("provider") or "openai-compatible"),
        model=str(raw.get("model") or "qwen3.7-max"),
        api_key_env=str(raw.get("api_key_env") or "AI_API_KEY"),
        base_url=str(raw.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        image_detail=str(raw.get("image_detail") or "auto"),
        max_output_tokens=int(raw.get("max_output_tokens") or 12000),
        structured_mode=str(raw.get("structured_mode") or "json_object"),
        compat_profile=str(raw.get("compat_profile") or "generic"),
        request_timeout_seconds=float(raw.get("request_timeout_seconds") or 120.0),
        enable_thinking=raw.get("enable_thinking", False),
    )


def _compact_sources(manifest: dict[str, Any]) -> list[dict[str, str]]:
    outputs = manifest.get("outputs") or {}
    path = Path(str(outputs.get("compact_evidence") or ""))
    if not path.is_file():
        return []
    payload = _read_object(path)
    sources: list[dict[str, str]] = []
    web_text = str(payload.get("web_text") or "").strip()
    if web_text:
        sources.append(
            {
                "source_id": "compact:web",
                "source_type": "compact_supplier_evidence",
                "kind": "text",
                "origin": str(path.resolve()),
                "content": web_text,
            }
        )
    image_facts = str(payload.get("image_facts") or "").strip()
    if image_facts:
        sources.append(
            {
                "source_id": "compact:images",
                "source_type": "compact_image_facts",
                "kind": "text",
                "origin": str(path.resolve()),
                "content": image_facts,
            }
        )
    return sources


def main() -> int:
    args = build_parser().parse_args()
    plan_payload = _read_object(args.fill_plan)
    live_fields = load_live_schema(args.live_schema)
    manifest = _read_object(args.resolver_manifest)
    product_url = str(manifest.get("primary_product_url") or "").strip()
    sources = _compact_sources(manifest)

    targets = required_targets(plan_payload, live_fields)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if not targets:
        result = {
            "schema_version": 1,
            "requested": 0,
            "ready": 0,
            "unresolved_count": 0,
            "overrides": [],
            "unresolved": [],
            "summary": "No unresolved required fields.",
        }
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"REQUIRED_COMPLETION requested=0 ready=0 unresolved=0 output={output}", flush=True)
        return 0

    provider = build_semantic_provider(_provider_config(manifest))
    request = build_required_completion_request(
        plan_payload,
        live_fields,
        product_url=product_url,
        grounded_sources=sources,
    )
    raw = provider.extract_json(request)
    result = parse_required_completion_response(
        raw,
        targets,
        live_fields,
        allowed_source_ids=[str(source.get("source_id") or "") for source in sources],
    )
    result["product_url"] = product_url
    result["evidence_source_ids"] = [str(source.get("source_id") or "") for source in sources]
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "REQUIRED_COMPLETION "
        f"requested={result['requested']} ready={result['ready']} "
        f"unresolved={result['unresolved_count']} output={output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProviderConfigurationError as exc:
        raise SystemExit(str(exc)) from exc
