from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .ai_decisions import (
    AIDecisionPacket,
    FieldDecision,
    field_contract,
    field_id,
    schema_digest,
    source_manifest_digest,
    validate_ai_decision_packet,
)
from .business_fields import is_business_question
from .product_profile import JSONTaskProvider, ProductProfile, profile_digest
from .semantic_grounding import GroundingCatalog


FIELD_MAPPING_CONTRACT_VERSION = 5
FIELD_MAPPING_CACHE_VERSION = 5


MAPPING_SYSTEM_INSTRUCTION = (
    "You are the local product-field resolver. Read the compact grounded PRODUCT_PROFILE "
    "and answer the supplied marketplace fields directly. Product semantics belong to you, "
    "not to Python. Use only the supplied local profile and its original citations; do not "
    "invent facts or use outside knowledge. Return compact JSON only."
)

MAPPING_RULES = [
    "Answer only the supplied target field_id values.",
    "READY means the local Product Profile is sufficient to answer this exact field for the selected variant. Include at least one underlying original citation from PRODUCT_PROFILE.",
    "Use REVIEW when a plausible answer exists but the local evidence is not sufficient for automatic entry; include focused search_queries that Web Research can use.",
    "Use CONFLICT when credible local sources genuinely disagree; preserve the competing cited alternatives instead of choosing silently.",
    "Use MISSING when the local profile does not establish the value; include focused search_queries when web research could reasonably help.",
    "Treat the selected variant as the listing target. Do not silently replace selected-variant facts with generic product-family facts.",
    "Do not infer No/False/Unsupported/Not included from absence. A negative value needs evidence that actually states that negative claim.",
    "Keep packaging dimensions/weight separate from product-body dimensions/weight and mount dimensions; preserve dimension axes exactly as written. Do not swap length/width/height/depth, cabin/rear, manual/UI language, or product/vehicle compatibility.",
    "If the field has marketplace options, use the exact option text only when it matches the supported meaning.",
    "If multi_value=false, return one value. If qualifier_options exist, put the magnitude in values and the unit in qualifier.",
    "Citations must use underlying original source_reference values present inside PRODUCT_PROFILE facts, never the derived product-profile source id.",
    "Do not use external web knowledge in this local pass.",
]


MAPPING_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["field_id", "status"],
                "properties": {
                    "field_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["ready", "review", "conflict", "missing"],
                    },
                    "values": {"type": "array", "items": {"type": "string"}},
                    "qualifier": {"type": "string"},
                    "confidence": {"type": "number"},
                    "citations": {"type": "array"},
                    "alternatives": {"type": "array"},
                    "reason": {"type": "string"},
                    "search_queries": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "model_summary": {"type": "string"},
    },
    "required": ["decisions"],
}


def _field_is_business(field: dict[str, Any]) -> bool:
    contract = field_contract(field)
    return is_business_question(contract["attribute_key"]) or is_business_question(
        contract["label"]
    )


def _target_payload(field: dict[str, Any]) -> dict[str, Any]:
    contract = field_contract(field)
    payload: dict[str, Any] = {
        "field_id": field_id(field),
        "attribute_key": contract["attribute_key"],
        "label": contract["label"],
        "section_heading": contract["section_heading"],
        "required": contract["required"],
        "multi_value": contract["multi_value"],
    }
    if contract["options"]:
        payload["options"] = contract["options"]
    if contract["qualifier_options"]:
        payload["qualifier_options"] = contract["qualifier_options"]
    if contract["help_text"]:
        payload["help_text"] = contract["help_text"]
    return payload


def _profile_source(profile: ProductProfile) -> dict[str, Any]:
    return {
        "source_id": "product-profile:derived",
        "source_type": "derived_product_profile",
        "kind": "text",
        "origin": "product-profile.json",
        "content": json.dumps(
            {
                "summary": profile.summary,
                "facts": [fact.as_dict() for fact in profile.facts],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def build_field_mapping_request(
    batch_fields: Iterable[dict[str, Any]],
    profile: ProductProfile,
) -> dict[str, Any]:
    fields = list(batch_fields)
    return {
        "task": "map_product_profile_to_marketplace_fields",
        "system_instruction": MAPPING_SYSTEM_INSTRUCTION,
        "prompt_instruction": (
            "Use the local Product Profile to answer this small field batch once. "
            "If local evidence cannot establish a field, mark it REVIEW/MISSING and give "
            "focused search_queries instead of guessing. Return one decision for every "
            "target field and no prose outside JSON."
        ),
        "product_identity": {
            "sku": profile.identity.sku,
            "model_number": profile.identity.model_number,
            "brand": profile.identity.brand,
        },
        "target_fields": [_target_payload(field) for field in fields],
        "rules": list(MAPPING_RULES),
        "grounded_sources": [_profile_source(profile)],
        "json_contract": MAPPING_JSON_SCHEMA,
    }


def mapping_contract_digest() -> str:
    raw = json.dumps(
        {
            "version": FIELD_MAPPING_CONTRACT_VERSION,
            "system": MAPPING_SYSTEM_INSTRUCTION,
            "rules": MAPPING_RULES,
            "schema": MAPPING_JSON_SCHEMA,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_key(
    provider: JSONTaskProvider,
    cache_namespace: str,
    batch_fields: list[dict[str, Any]],
    profile: ProductProfile,
) -> str:
    payload = {
        "cache_version": FIELD_MAPPING_CACHE_VERSION,
        "contract_sha256": mapping_contract_digest(),
        "provider": provider.name,
        "cache_namespace": cache_namespace,
        "profile_sha256": profile_digest(profile),
        "batch_schema_sha256": schema_digest(batch_fields),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _BatchRun:
    index: int
    decisions: list[FieldDecision]
    model_calls: int
    cache_hit: bool
    warning: str = ""


def _run_batch(
    provider: JSONTaskProvider,
    batch_index: int,
    batch_fields: list[dict[str, Any]],
    profile: ProductProfile,
    grounding: GroundingCatalog,
    *,
    cache_dir: Path | None,
    cache_namespace: str,
) -> _BatchRun:
    key = _cache_key(provider, cache_namespace, batch_fields, profile)
    cache_path = cache_dir / f"field-map-{key}.json" if cache_dir is not None else None
    if cache_path is not None and cache_path.is_file():
        try:
            cached = AIDecisionPacket.from_mapping(
                json.loads(cache_path.read_text(encoding="utf-8"))
            )
            validated = validate_ai_decision_packet(
                cached,
                batch_fields,
                grounding,
                expected_identity=profile.identity,
            )
            return _BatchRun(batch_index, validated.decisions, 0, True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    try:
        raw = provider.extract_json(build_field_mapping_request(batch_fields, profile))
        raw_decisions = raw.get("decisions") if isinstance(raw, dict) else None
        if not isinstance(raw_decisions, list):
            raise ValueError("field mapping AI output 缺少 decisions 数组")
        packet = AIDecisionPacket(
            identity=profile.identity,
            schema_sha256=schema_digest(batch_fields),
            source_manifest_sha256=source_manifest_digest(grounding),
            decisions=[
                FieldDecision.from_mapping(item, index=index)
                for index, item in enumerate(raw_decisions, start=1)
                if isinstance(item, dict)
            ],
            model_summary=str(raw.get("model_summary") or "").strip(),
            warnings=[],
            extractor=str(raw.get("extractor") or provider.name).strip() or provider.name,
        )
        validated = validate_ai_decision_packet(
            packet,
            batch_fields,
            grounding,
            expected_identity=profile.identity,
        )
    except Exception as exc:
        return _BatchRun(
            batch_index,
            [],
            1,
            False,
            warning=f"mapping batch {batch_index} failed: {exc}",
        )

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp.write_text(
            json.dumps(validated.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(cache_path)
    return _BatchRun(batch_index, validated.decisions, 1, False)


def _mechanical_batches(
    fields: list[dict[str, Any]],
    batch_size: int,
) -> list[list[dict[str, Any]]]:
    return [
        fields[index : index + batch_size]
        for index in range(0, len(fields), batch_size)
    ]


@dataclass(slots=True)
class FieldMappingRunResult:
    packet: AIDecisionPacket
    model_calls: int
    cache_hits: int
    batch_count: int
    failed_batches: int
    elapsed_seconds: float


def run_field_mapping(
    provider: JSONTaskProvider,
    fields: Iterable[dict[str, Any]],
    profile: ProductProfile,
    grounding: GroundingCatalog,
    *,
    batch_size: int = 12,
    concurrency: int = 4,
    cache_dir: str | Path | None = None,
    cache_namespace: str = "",
) -> FieldMappingRunResult:
    if not 1 <= int(batch_size) <= 30:
        raise ValueError("batch_size 必须在 1..30。")
    if not 1 <= int(concurrency) <= 12:
        raise ValueError("concurrency 必须在 1..12。")

    started = time.monotonic()
    field_list = list(fields)
    non_business = [field for field in field_list if not _field_is_business(field)]
    batches = _mechanical_batches(non_business, int(batch_size))
    cache_root = Path(cache_dir) if cache_dir is not None else None

    runs: list[_BatchRun] = []
    if batches:
        workers = min(int(concurrency), len(batches))
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="field-map",
        ) as executor:
            futures = {
                executor.submit(
                    _run_batch,
                    provider,
                    index,
                    batch,
                    profile,
                    grounding,
                    cache_dir=cache_root,
                    cache_namespace=cache_namespace,
                ): index
                for index, batch in enumerate(batches, start=1)
            }
            for future in as_completed(futures):
                runs.append(future.result())
    runs.sort(key=lambda item: item.index)

    decisions: list[FieldDecision] = []
    warnings: list[str] = []
    for run in runs:
        decisions.extend(run.decisions)
        if run.warning:
            warnings.append(run.warning)

    candidate = AIDecisionPacket(
        identity=profile.identity,
        schema_sha256=schema_digest(field_list),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=decisions,
        model_summary="Answered Makro fields from the grounded local Product Profile.",
        warnings=warnings,
        extractor=f"{profile.extractor}+parallel-local-field-resolve".strip("+"),
    )
    validated = validate_ai_decision_packet(
        candidate,
        field_list,
        grounding,
        expected_identity=profile.identity,
    )
    return FieldMappingRunResult(
        packet=validated,
        model_calls=sum(run.model_calls for run in runs),
        cache_hits=sum(1 for run in runs if run.cache_hit),
        batch_count=len(batches),
        failed_batches=sum(1 for run in runs if run.warning),
        elapsed_seconds=time.monotonic() - started,
    )
