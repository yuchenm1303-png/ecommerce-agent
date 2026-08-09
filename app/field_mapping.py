from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

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
from .evidence_contract import ProductIdentity
from .semantic_grounding import GroundingCatalog


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


FIELD_MAPPING_CONTRACT_VERSION = 8
FIELD_MAPPING_CACHE_VERSION = 8


MAPPING_SYSTEM_INSTRUCTION = (
    "You directly fill marketplace fields from the supplied exact supplier-page evidence. "
    "There is no intermediate product profile, review model, or Python semantic resolver. "
    "Read the structured page rows, embedded variant data, rendered page text, full-page screenshot "
    "and captured product/detail images yourself. Preserve real source conflicts and leave unsupported "
    "fields missing. Return JSON only."
)


MAPPING_RULES = [
    "Answer every supplied target field_id exactly once with READY, CONFLICT, or MISSING.",
    "Use READY only when the supplied exact-page evidence supports that exact Makro field. Cite an existing source_reference; evidence_text may be a concise faithful paraphrase and need not copy the source verbatim.",
    "Use CONFLICT when supplied text/images genuinely give different values for the same exact field. Inspect all supplied image evidence as well as text; every alternative needs its own source citation.",
    "Use MISSING when the value is not established. Do not guess from typical products, nearby facts, absence, class conventions, or general knowledge.",
    "Treat attribute_key, label, section_heading, help_text, context_text, options and qualifier_options together as the Makro field meaning. Do not invent a second taxonomy.",
    "Keep scope exact: packaging vs product body vs mount; cabin/interior vs rear/back; manual/documentation language vs device UI language; product brand vs compatible vehicle brand.",
    "Preserve dimension axes literally. Source length maps to Makro Length; source width/breadth maps to Makro Breadth; source height maps to Makro Height. Never swap or rotate axes to make values fit.",
    "Structured page rows and embedded key/value data are high-signal raw evidence. If they explicitly name length/width/height, preserve those names exactly.",
    "Do not turn a conflict into a confident statement elsewhere. Generated Description/Keywords/Sales Package may use only supported, non-conflicting facts.",
    "Never infer No/False/Unsupported/Not included from absence. A negative value requires explicit evidence.",
    "If options exist, return exact option text only when evidence supports it. If multi_value=false return one value.",
    "If qualifier_options exist, put the magnitude in values and an exact allowed unit in qualifier. If qualifier_options are empty, qualifier MUST be empty. Use a fixed unit shown in context_text/help_text by converting the magnitude to that unit; if no fixed unit is established, return MISSING rather than inventing a qualifier.",
    "Embedded variant matrices may contain several options. Do not mix facts from different variants unless the page itself establishes they apply to the current product generally.",
    "Do not use external web knowledge in this local pass. Seller-operated price, stock, MOQ, fulfilment, shipping and listing-status fields are not product research questions.",
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
                    "status": {"type": "string", "enum": ["ready", "conflict", "missing"]},
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
            "Read the supplied exact product-page evidence and fill this small Makro field batch directly. "
            "Use the raw structured rows and images before resorting to MISSING. Preserve any genuine text/image conflict. "
            "There is no later local review stage; unsupported values must be MISSING for Web to research."
        ),
        "product_identity": {
            "source_product_url": product_url.strip(),
        },
        "target_fields": [_target_payload(field) for field in fields],
        "rules": list(MAPPING_RULES),
        "grounded_sources": grounding.as_request_list(),
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
    key = _cache_key(
        provider,
        cache_namespace,
        batch_fields,
        grounding,
        expected_identity,
        product_url,
    )
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

    try:
        raw = provider.extract_json(
            build_field_mapping_request(
                batch_fields,
                grounding,
                expected_identity=expected_identity,
                product_url=product_url,
            )
        )
        raw_decisions = raw.get("decisions") if isinstance(raw, dict) else None
        if not isinstance(raw_decisions, list):
            raise ValueError("local field fill AI output 缺少 decisions 数组")
        packet = AIDecisionPacket(
            identity=expected_identity,
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
            expected_identity=expected_identity,
        )
    except Exception as exc:
        return _BatchRun(
            batch_index,
            [],
            1,
            False,
            warning=f"local field batch {batch_index} failed: {exc}",
        )

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp.write_text(json.dumps(validated.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(cache_path)
    return _BatchRun(batch_index, validated.decisions, 1, False)


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
