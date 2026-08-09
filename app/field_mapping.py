from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from .ai_decisions import (
    CONFLICT,
    MISSING,
    READY,
    AIDecisionPacket,
    FieldDecision,
    field_contract,
    field_id,
    schema_digest,
    source_manifest_digest,
    validate_ai_decision_packet,
)
from .business_fields import is_business_question
from .evidence_contract import ProductIdentity
from .semantic_grounding import GroundingCatalog


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


FIELD_MAPPING_CONTRACT_VERSION = 10
FIELD_MAPPING_CACHE_VERSION = 10


MAPPING_SYSTEM_INSTRUCTION = (
    "You directly fill marketplace fields from the supplied exact supplier-page evidence. "
    "There is no intermediate product profile, review model, or Python semantic resolver. "
    "Read structured page rows, embedded variant data, rendered page text, the full-page screenshot "
    "and captured product/detail images yourself. Preserve real source conflicts and leave unsupported "
    "fields missing. The response shape is schema-constrained: classify each target into exactly one "
    "of ready, conflicts, or missing."
)


MAPPING_RULES = [
    "Place every supplied target field_id exactly once in ready, conflicts, or missing.",
    "ready is for a single supported field value and requires at least one existing source_reference citation.",
    "conflicts is for genuine different values that each answer the same exact target field and requires at least two cited alternatives.",
    "Every READY value and every CONFLICT alternative must be supported by direct target-specific evidence. Do not use evidence for one attribute as the value of another attribute; unit conversion and exact option mapping are the only mechanical transformations allowed.",
    "missing is only for a value not established by the supplied exact-page evidence; do not guess from typical products, nearby facts, absence, class conventions, or general knowledge.",
    "Treat attribute_key, label, section_heading, help_text, context_text, options and qualifier_options together as the Makro field meaning. Do not invent a second taxonomy.",
    "Keep scope exact: packaging vs product body vs mount; cabin/interior vs rear/back; manual/documentation language vs device UI language; product brand vs compatible vehicle brand.",
    "For structured key/value rows, preserve the row key as the attribute identity. A value from a differently named row must not be reassigned merely because it is adjacent or numerically plausible.",
    "Preserve dimension axes literally. Source length maps to Makro Length; source width/breadth maps to Makro Breadth; source height maps to Makro Height. Never swap or rotate axes to make values fit.",
    "Before declaring CONFLICT, verify all alternatives have the same semantic/quantity type as the target field; nearby facts of another type are not conflicting alternatives.",
    "Generated Description/Keywords/Sales Package may use only supported, non-conflicting facts.",
    "Never infer No/False/Unsupported/Not included from absence. A negative value requires explicit evidence.",
    "If options exist, return exact option text only when evidence supports it. If multi_value=false return one value.",
    "If qualifier_options exist, put the magnitude in values and an exact allowed unit in qualifier. If qualifier_options are empty, qualifier MUST be empty. Use a fixed unit shown in context_text/help_text by converting the magnitude to that unit; if no fixed unit is established, classify the field as missing.",
    "Embedded variant matrices may contain several options. Do not mix facts from different variants unless the page itself establishes they apply to the current product generally.",
    "Do not use external web knowledge in this local pass. Seller-operated price, stock, MOQ, fulfilment, shipping and listing-status fields are not product research questions.",
]


_CITATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_reference": {"type": "string", "minLength": 1},
        "evidence_text": {"type": "string", "minLength": 1},
    },
    "required": ["source_reference", "evidence_text"],
}

_READY_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "field_id": {"type": "string", "minLength": 1},
        "values": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "qualifier": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "citations": {"type": "array", "minItems": 1, "items": _CITATION_SCHEMA},
    },
    "required": ["field_id", "values", "qualifier", "confidence", "citations"],
}

_ALTERNATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "values": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "qualifier": {"type": "string"},
        "citations": {"type": "array", "minItems": 1, "items": _CITATION_SCHEMA},
    },
    "required": ["values", "qualifier", "citations"],
}

_CONFLICT_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "field_id": {"type": "string", "minLength": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "alternatives": {"type": "array", "minItems": 2, "items": _ALTERNATIVE_SCHEMA},
    },
    "required": ["field_id", "confidence", "alternatives"],
}

_MISSING_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "field_id": {"type": "string", "minLength": 1},
        "search_queries": {
            "type": "array",
            "maxItems": 2,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": ["field_id", "search_queries"],
}

MAPPING_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ready": {"type": "array", "items": _READY_ITEM_SCHEMA},
        "conflicts": {"type": "array", "items": _CONFLICT_ITEM_SCHEMA},
        "missing": {"type": "array", "items": _MISSING_ITEM_SCHEMA},
        "model_summary": {"type": "string"},
    },
    "required": ["ready", "conflicts", "missing", "model_summary"],
}


def _field_is_business(field: dict[str, Any]) -> bool:
    contract = field_contract(field)
    return is_business_question(contract["attribute_key"]) or is_business_question(contract["label"])


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
    for key in ("options", "qualifier_options", "help_text", "context_text"):
        if contract.get(key):
            payload[key] = contract[key]
    return payload


def build_field_mapping_request(
    batch_fields: Iterable[dict[str, Any]],
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
    product_url: str = "",
) -> dict[str, Any]:
    fields = list(batch_fields)
    return {
        "task": "fill_marketplace_fields_from_exact_product_evidence",
        "system_instruction": MAPPING_SYSTEM_INSTRUCTION,
        "prompt_instruction": (
            "Read the supplied exact product-page evidence and classify this Makro field batch directly into the "
            "schema-defined ready/conflicts/missing collections. Treat each target field definition independently. "
            "Use atomic structured rows and images before missing; preserve genuine conflicts only when both values "
            "directly answer the same target field."
        ),
        "product_identity": {"source_product_url": product_url.strip()},
        "target_fields": [_target_payload(field) for field in fields],
        "rules": list(MAPPING_RULES),
        "grounded_sources": grounding.as_request_list(),
        "json_contract": MAPPING_JSON_SCHEMA,
        "strict_json_schema": True,
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
    grounding: GroundingCatalog,
    expected_identity: ProductIdentity,
    product_url: str,
) -> str:
    payload = {
        "cache_version": FIELD_MAPPING_CACHE_VERSION,
        "contract_sha256": mapping_contract_digest(),
        "provider": provider.name,
        "cache_namespace": cache_namespace,
        "source_manifest_sha256": source_manifest_digest(grounding),
        "identity": {
            "sku": expected_identity.sku,
            "model_number": expected_identity.model_number,
            "brand": expected_identity.brand,
        },
        "product_url": product_url.strip(),
        "batch_schema_sha256": schema_digest(batch_fields),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _typed_decisions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ValueError("local field fill AI output 不是 JSON object")
    if not all(isinstance(raw.get(key), list) for key in ("ready", "conflicts", "missing")):
        raise ValueError("local field fill AI output 缺少 ready/conflicts/missing 数组")

    output: list[dict[str, Any]] = []
    for item in raw.get("ready") or []:
        if isinstance(item, dict):
            output.append(
                {
                    "field_id": item.get("field_id"),
                    "status": READY,
                    "values": list(item.get("values") or []),
                    "qualifier": str(item.get("qualifier") or ""),
                    "confidence": item.get("confidence", 0.0),
                    "citations": list(item.get("citations") or []),
                    "alternatives": [],
                    "reason": "",
                    "search_queries": [],
                }
            )
    for item in raw.get("conflicts") or []:
        if isinstance(item, dict):
            output.append(
                {
                    "field_id": item.get("field_id"),
                    "status": CONFLICT,
                    "values": [],
                    "qualifier": "",
                    "confidence": item.get("confidence", 0.0),
                    "citations": [],
                    "alternatives": list(item.get("alternatives") or []),
                    "reason": "",
                    "search_queries": [],
                }
            )
    for item in raw.get("missing") or []:
        if isinstance(item, dict):
            output.append(
                {
                    "field_id": item.get("field_id"),
                    "status": MISSING,
                    "values": [],
                    "qualifier": "",
                    "confidence": 0.0,
                    "citations": [],
                    "alternatives": [],
                    "reason": "",
                    "search_queries": list(item.get("search_queries") or []),
                }
            )
    return output


def _validate_partition(raw_decisions: list[dict[str, Any]], batch_fields: list[dict[str, Any]]) -> None:
    expected = [field_id(field) for field in batch_fields]
    expected_set = set(expected)
    observed = [str(item.get("field_id") or "").strip() for item in raw_decisions]
    duplicates = sorted({identifier for identifier in observed if identifier and observed.count(identifier) > 1})
    unknown = sorted({identifier for identifier in observed if identifier and identifier not in expected_set})
    omitted = [identifier for identifier in expected if identifier not in observed]
    blank_count = sum(1 for identifier in observed if not identifier)
    if duplicates or unknown or omitted or blank_count:
        parts: list[str] = []
        if duplicates:
            parts.append("duplicate field_id=" + ",".join(duplicates))
        if unknown:
            parts.append("unknown field_id=" + ",".join(unknown))
        if omitted:
            parts.append("omitted field_id=" + ",".join(omitted))
        if blank_count:
            parts.append(f"blank field_id count={blank_count}")
        raise ValueError("; ".join(parts))


def _validated_model_output(
    raw: Any,
    batch_fields: list[dict[str, Any]],
    grounding: GroundingCatalog,
    expected_identity: ProductIdentity,
    provider_name: str,
) -> AIDecisionPacket:
    raw_decisions = _typed_decisions(raw)
    _validate_partition(raw_decisions, batch_fields)
    packet = AIDecisionPacket(
        identity=expected_identity,
        schema_sha256=schema_digest(batch_fields),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=[
            FieldDecision.from_mapping(item, index=index)
            for index, item in enumerate(raw_decisions, start=1)
        ],
        model_summary=str(raw.get("model_summary") or "").strip(),
        warnings=[],
        extractor=str(raw.get("extractor") or provider_name).strip() or provider_name,
    )
    return validate_ai_decision_packet(
        packet,
        batch_fields,
        grounding,
        expected_identity=expected_identity,
    )


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
    grounding: GroundingCatalog,
    expected_identity: ProductIdentity,
    product_url: str,
    *,
    cache_dir: Path | None,
    cache_namespace: str,
) -> _BatchRun:
    key = _cache_key(provider, cache_namespace, batch_fields, grounding, expected_identity, product_url)
    cache_path = cache_dir / f"field-map-{key}.json" if cache_dir is not None else None
    if cache_path is not None and cache_path.is_file():
        try:
            cached = AIDecisionPacket.from_mapping(json.loads(cache_path.read_text(encoding="utf-8")))
            validated = validate_ai_decision_packet(
                cached,
                batch_fields,
                grounding,
                expected_identity=expected_identity,
            )
            return _BatchRun(batch_index, validated.decisions, 0, True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    request = build_field_mapping_request(
        batch_fields,
        grounding,
        expected_identity=expected_identity,
        product_url=product_url,
    )
    model_calls = 0
    try:
        raw = provider.extract_json(request)
        model_calls += 1
    except Exception as exc:
        return _BatchRun(
            batch_index,
            [],
            model_calls or 1,
            False,
            warning=f"local field batch {batch_index} failed: {exc}",
        )

    try:
        validated = _validated_model_output(
            raw,
            batch_fields,
            grounding,
            expected_identity,
            provider.name,
        )
    except Exception as first_exc:
        repair_request = dict(request)
        repair_request["validation_error"] = (
            "The previous response did not assign each supplied field_id exactly once. "
            f"Fix only the response structure and field-to-evidence mapping: {first_exc}"
        )
        try:
            repaired_raw = provider.extract_json(repair_request)
            model_calls += 1
            validated = _validated_model_output(
                repaired_raw,
                batch_fields,
                grounding,
                expected_identity,
                provider.name,
            )
        except Exception as repair_exc:
            return _BatchRun(
                batch_index,
                [],
                model_calls,
                False,
                warning=(
                    f"local field batch {batch_index} failed after one structural repair: "
                    f"{repair_exc}"
                ),
            )

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp.write_text(json.dumps(validated.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(cache_path)
    return _BatchRun(batch_index, validated.decisions, model_calls, False)


def _mechanical_batches(fields: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [fields[index : index + batch_size] for index in range(0, len(fields), batch_size)]


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
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
    product_url: str = "",
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
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="local-field") as executor:
            futures = {
                executor.submit(
                    _run_batch,
                    provider,
                    index,
                    batch,
                    grounding,
                    expected_identity,
                    product_url,
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
        identity=expected_identity,
        schema_sha256=schema_digest(field_list),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=decisions,
        model_summary="Exact local product-page evidence directly filled the first-pass Makro field table.",
        warnings=warnings,
        extractor=f"{provider.name}+parallel-local-fill".strip("+"),
    )
    validated = validate_ai_decision_packet(
        candidate,
        field_list,
        grounding,
        expected_identity=expected_identity,
    )
    return FieldMappingRunResult(
        packet=validated,
        model_calls=sum(run.model_calls for run in runs),
        cache_hits=sum(1 for run in runs if run.cache_hit),
        batch_count=len(batches),
        failed_batches=sum(1 for run in runs if run.warning),
        elapsed_seconds=time.monotonic() - started,
    )
