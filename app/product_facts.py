from __future__ import annotations

import hashlib
import json
import re
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
from .compact_evidence import CompactEvidence
from .evidence_contract import ProductIdentity
from .listing_content_policy import CONTENT_POLICY_VERSION, GLOBAL_CONTENT_RULES, field_content_policy
from .semantic_grounding import GroundingCatalog
from .source_bundle import normalize_key


PRODUCT_FACT_CONTRACT_VERSION = 4
PRODUCT_FACT_CACHE_VERSION = 7


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


SYSTEM_INSTRUCTION = (
    "Resolve compact product evidence into grounded marketplace facts. "
    "Return only directly supported READY facts and genuine CONFLICT facts; JSON only."
)

RULES = [
    "Inspect all evidence and all target fields together before assigning any fact.",
    "A field may appear at most once. Omit unsupported fields; omission means MISSING.",
    "READY requires an explicit value, exact attribute meaning and matching physical scope.",
    "Two incompatible direct values for the same target and scope require CONFLICT.",
    "If multi_value=false, READY must contain exactly one value string. When several compatible facets belong in a free-text field, combine them into one concise string; incompatible alternatives require CONFLICT instead of multiple READY values.",
    "qualifier is only the marketplace unit/qualifier, never explanation, scope commentary, confidence text or field description. If qualifier_options exist, use one exact allowed qualifier. If qualifier_options are absent, qualifier must be empty unless context_text explicitly renders a fixed physical unit for the value input.",
    "For a numeric target with a qualifier option or fixed unit rendered in context_text, return the bare finite number in values and the unit in qualifier; never embed the unit token inside the numeric value.",
    "Do not infer negative values, compatibility, contents, quantity, identity or seller details from absence or convention.",
    "Keep packaging, product body, mount and lens scopes separate; keep physical axes separate.",
    "Keep front, cabin and rear cameras separate; keep documentation language and device capability separate.",
    "Use only citation aliases shown in brackets and return aliases without brackets.",
    "Never answer seller-operated business fields.",
    "When a target includes content_policy, follow it after the evidence and live-field contract; content policy never makes unsupported product claims READY.",
    *GLOBAL_CONTENT_RULES,
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


def _json_schema(fields: list[dict[str, Any]]) -> dict[str, Any]:
    identifiers = [field_id(field) for field in fields]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "field_id": {"type": "string", "enum": identifiers},
                        "status": {"type": "string", "enum": [READY, CONFLICT]},
                        "values": {"type": "array", "items": {"type": "string"}},
                        "qualifier": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "citations": {"type": "array", "items": _CITATION_SCHEMA},
                        "alternatives": {"type": "array", "items": _ALTERNATIVE_SCHEMA},
                    },
                    "required": [
                        "field_id",
                        "status",
                        "values",
                        "qualifier",
                        "confidence",
                        "citations",
                        "alternatives",
                    ],
                },
            },
            "model_summary": {"type": "string"},
        },
        "required": ["facts", "model_summary"],
    }


def _target(field: dict[str, Any]) -> dict[str, Any]:
    contract = field_contract(field)
    output: dict[str, Any] = {
        "field_id": field_id(field),
        "attribute_key": contract["attribute_key"],
        "label": contract["label"],
        "section_heading": contract["section_heading"],
        "required": contract["required"],
        "multi_value": contract["multi_value"],
    }
    for key in ("options", "qualifier_options", "help_text", "context_text"):
        if contract.get(key):
            output[key] = contract[key]
    content_policy = field_content_policy(field)
    if content_policy:
        output["content_policy"] = content_policy
    return output


def _is_business(field: dict[str, Any]) -> bool:
    contract = field_contract(field)
    return is_business_question(contract["attribute_key"]) or is_business_question(contract["label"])


def build_product_fact_request(
    fields: Iterable[dict[str, Any]],
    compact_evidence: CompactEvidence,
    *,
    product_url: str = "",
) -> dict[str, Any]:
    targets = [field for field in fields if not _is_business(field)]
    return {
        "task": "resolve_compact_product_facts",
        "system_instruction": SYSTEM_INSTRUCTION,
        "prompt_instruction": (
            "Return only fields with direct evidence. For READY, values and citations must be non-empty and "
            "alternatives empty. For CONFLICT, values and citations must be empty and alternatives must contain "
            "at least two distinct grounded values. Check every source for disagreement before READY. "
            "Follow the target field's multi_value/options/qualifier contract and content_policy exactly."
        ),
        "product_identity": {"source_product_url": product_url.strip()},
        "target_fields": [_target(field) for field in targets],
        "all_marketplace_fields": [],
        "rules": list(RULES),
        "grounded_sources": compact_evidence.request_sources(),
        "json_contract": _json_schema(targets),
        "strict_json_schema": True,
    }


def _contract_digest() -> str:
    raw = json.dumps(
        {
            "version": PRODUCT_FACT_CONTRACT_VERSION,
            "content_policy_version": CONTENT_POLICY_VERSION,
            "system": SYSTEM_INSTRUCTION,
            "rules": RULES,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_key(
    provider: JSONTaskProvider,
    fields: list[dict[str, Any]],
    grounding: GroundingCatalog,
    compact_evidence: CompactEvidence,
    product_url: str,
    cache_namespace: str,
) -> str:
    raw = json.dumps(
        {
            "cache_version": PRODUCT_FACT_CACHE_VERSION,
            "contract": _contract_digest(),
            "provider": provider.name,
            "namespace": cache_namespace,
            "schema": schema_digest(fields),
            "sources": source_manifest_digest(grounding),
            "compact": compact_evidence.sha256,
            "product_url": product_url.strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _compact_line_index(compact_evidence: CompactEvidence) -> dict[str, list[tuple[str, str]]]:
    output: dict[str, list[tuple[str, str]]] = {"compact:web": [], "compact:images": []}
    for aggregate, text in (
        ("compact:web", compact_evidence.web_text),
        ("compact:images", compact_evidence.image_facts),
    ):
        for line in text.splitlines():
            match = re.match(r"\[([^\]]+)\]\s*(.+)", line.strip())
            if match is not None:
                output[aggregate].append((match.group(1), match.group(2)))
    return output


def _resolve_aggregate_reference(
    reference: str,
    evidence_text: str,
    aliases: dict[str, str],
    line_index: dict[str, list[tuple[str, str]]],
) -> str:
    wanted = normalize_key(evidence_text)
    if not wanted:
        return reference
    matches: list[str] = []
    for alias, content in line_index.get(reference, []):
        candidate = normalize_key(content)
        if wanted in candidate or candidate in wanted:
            if alias in aliases:
                matches.append(aliases[alias])
    return matches[0] if len(set(matches)) == 1 else reference


def _expand_aliases(
    raw: Any,
    aliases: dict[str, str],
    line_index: dict[str, list[tuple[str, str]]],
) -> None:
    if isinstance(raw, dict):
        reference = raw.get("source_reference")
        if isinstance(reference, str):
            normalized = reference.strip().strip("[]").strip()
            if normalized in aliases:
                raw["source_reference"] = aliases[normalized]
            elif normalized in line_index:
                raw["source_reference"] = _resolve_aggregate_reference(
                    normalized,
                    str(raw.get("evidence_text") or ""),
                    aliases,
                    line_index,
                )
        for value in raw.values():
            _expand_aliases(value, aliases, line_index)
    elif isinstance(raw, list):
        for value in raw:
            _expand_aliases(value, aliases, line_index)


def _packet_from_response(
    raw: Any,
    fields: list[dict[str, Any]],
    grounding: GroundingCatalog,
    compact_evidence: CompactEvidence,
    provider_name: str,
) -> AIDecisionPacket:
    if not isinstance(raw, dict) or not isinstance(raw.get("facts"), list):
        raise ValueError("product fact response requires a facts array")
    _expand_aliases(
        raw,
        compact_evidence.citation_aliases,
        _compact_line_index(compact_evidence),
    )
    allowed = {field_id(field) for field in fields if not _is_business(field)}
    packaging_targets = {
        field_contract(field)["attribute_key"]: field_id(field)
        for field in fields
        if "price, stock and shipping" in field_contract(field)["section_heading"].casefold()
        and field_contract(field)["attribute_key"] in {"length", "breadth", "height", "weight"}
    }
    for item in raw["facts"]:
        if not isinstance(item, dict) or str(item.get("status") or "").casefold() != READY:
            continue
        cited_keys = {
            match.group(1).casefold()
            for citation in item.get("citations") or []
            if isinstance(citation, dict)
            for match in [
                re.match(
                    r"\s*(length|breadth|height|weight)\s*=",
                    str(citation.get("evidence_text") or ""),
                    flags=re.IGNORECASE,
                )
            ]
            if match is not None
        }
        if len(cited_keys) == 1:
            canonical_key = next(iter(cited_keys))
            if canonical_key in packaging_targets:
                item["field_id"] = packaging_targets[canonical_key]
    seen: set[str] = set()
    decisions: list[FieldDecision] = []
    for index, item in enumerate(raw["facts"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"facts[{index}] must be an object")
        identifier = str(item.get("field_id") or "").strip()
        if identifier not in allowed:
            raise ValueError(f"facts[{index}] has unknown field_id={identifier!r}")
        if identifier in seen:
            raise ValueError(f"duplicate fact field_id={identifier}")
        seen.add(identifier)
        decisions.append(
            FieldDecision.from_mapping(
                {
                    "field_id": identifier,
                    "status": item.get("status"),
                    "values": item.get("values") or [],
                    "qualifier": item.get("qualifier") or "",
                    "confidence": item.get("confidence", 0.0),
                    "citations": item.get("citations") or [],
                    "alternatives": item.get("alternatives") or [],
                    "search_queries": [],
                },
                index=index,
            )
        )
    packet = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256=schema_digest(fields),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=decisions,
        model_summary=str(raw.get("model_summary") or "").strip(),
        warnings=[],
        extractor=f"{provider_name}+global-product-facts",
    )
    validated = validate_ai_decision_packet(packet, fields, grounding)
    contracts = {field_id(field): field_contract(field) for field in fields}
    for decision in validated.decisions:
        if decision.status != READY:
            continue
        contract = contracts[decision.field_id]
        attribute_key = contract["attribute_key"]
        evidence = " | ".join(citation.evidence_text.casefold() for citation in decision.citations)
        unsafe_reason = ""
        if attribute_key in {"exterior_field_of_view", "interior_field_of_view"}:
            markers = (
                ("exterior", "outside", "front", "车前", "前摄", "外摄")
                if attribute_key == "exterior_field_of_view"
                else ("interior", "inside", "cabin", "车内", "内摄", "座舱")
            )
            if not any(marker in evidence for marker in markers):
                unsafe_reason = "field-of-view evidence did not identify the target camera scope"
        product_dimensions = {"depth", "height", "width", "weight_with_bracket"}
        is_product_description_dimension = (
            attribute_key in product_dimensions
            and "additional description" in contract["section_heading"].casefold()
        )
        if is_product_description_dimension and re.search(
            r"scope\s*=\s*packaging|\(packaging\)|scope\s*=\s*mount|\(mount\)",
            evidence,
        ):
            unsafe_reason = "product dimension cited packaging or mount scope"
        if unsafe_reason:
            decision.status = MISSING
            decision.values = []
            decision.qualifier = ""
            decision.confidence = 0.0
            decision.citations = []
            decision.alternatives = []
            decision.reason = unsafe_reason
            validated.warnings.append(f"{decision.field_id}: {unsafe_reason}")
    return validated


@dataclass(slots=True)
class ProductFactRunResult:
    packet: AIDecisionPacket
    model_calls: int
    cache_hit: bool
    fact_count: int
    failed: bool
    elapsed_seconds: float
    warning: str = ""
    batch_count: int = 0
    cache_hits: int = 0
    failed_batches: int = 0


@dataclass(slots=True)
class _BatchResult:
    index: int
    packet: AIDecisionPacket | None
    model_calls: int
    cache_hit: bool
    warning: str = ""


def _run_batch(
    provider: JSONTaskProvider,
    index: int,
    fields: list[dict[str, Any]],
    grounding: GroundingCatalog,
    compact_evidence: CompactEvidence,
    *,
    product_url: str,
    cache_dir: Path | None,
    cache_namespace: str,
) -> _BatchResult:
    key = _cache_key(
        provider,
        fields,
        grounding,
        compact_evidence,
        product_url,
        cache_namespace,
    )
    cache_path = cache_dir / f"product-facts-batch-{key}.json" if cache_dir is not None else None
    if cache_path is not None and cache_path.is_file():
        try:
            cached = AIDecisionPacket.from_mapping(json.loads(cache_path.read_text(encoding="utf-8")))
            packet = validate_ai_decision_packet(cached, fields, grounding)
            return _BatchResult(index, packet, 0, True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    try:
        raw = provider.extract_json(
            build_product_fact_request(fields, compact_evidence, product_url=product_url)
        )
        packet = _packet_from_response(raw, fields, grounding, compact_evidence, provider.name)
    except Exception as exc:
        return _BatchResult(index, None, 1, False, str(exc))

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temporary.write_text(json.dumps(packet.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(cache_path)
    return _BatchResult(index, packet, 1, False)


def run_product_facts(
    provider: JSONTaskProvider,
    fields: Iterable[dict[str, Any]],
    grounding: GroundingCatalog,
    compact_evidence: CompactEvidence,
    *,
    product_url: str = "",
    batch_size: int = 12,
    concurrency: int = 4,
    cache_dir: str | Path | None = None,
    cache_namespace: str = "",
) -> ProductFactRunResult:
    started = time.monotonic()
    field_list = list(fields)
    if int(batch_size) < 1:
        raise ValueError("product fact batch_size must be >= 1")
    if int(concurrency) < 1:
        raise ValueError("product fact concurrency must be >= 1")
    target_fields = [field for field in field_list if not _is_business(field)]
    batches = [
        target_fields[start : start + int(batch_size)]
        for start in range(0, len(target_fields), int(batch_size))
    ]
    cache_root = Path(cache_dir) if cache_dir is not None else None
    results: list[_BatchResult] = []
    if batches:
        with ThreadPoolExecutor(max_workers=min(int(concurrency), len(batches))) as executor:
            futures = [
                executor.submit(
                    _run_batch,
                    provider,
                    index,
                    batch,
                    grounding,
                    compact_evidence,
                    product_url=product_url,
                    cache_dir=cache_root,
                    cache_namespace=cache_namespace,
                )
                for index, batch in enumerate(batches)
            ]
            for future in as_completed(futures):
                results.append(future.result())
    results.sort(key=lambda item: item.index)
    resolved: list[FieldDecision] = []
    warnings: list[str] = []
    for result in results:
        if result.packet is None:
            warnings.append(f"product fact batch {result.index + 1} failed: {result.warning}")
            continue
        resolved.extend(
            decision
            for decision in result.packet.decisions
            if decision.status in {READY, CONFLICT}
        )
        warnings.extend(
            warning
            for warning in result.packet.warnings
            if not warning.startswith("AI stage omitted field_id=")
        )
    combined = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256=schema_digest(field_list),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=resolved,
        model_summary="Mechanically batched compact product evidence mapping.",
        warnings=warnings,
        extractor=f"{provider.name}+batched-product-facts",
    )
    packet = validate_ai_decision_packet(combined, field_list, grounding)
    count = sum(decision.status in {READY, CONFLICT} for decision in packet.decisions)
    failed_batches = sum(result.packet is None for result in results)
    model_calls = sum(result.model_calls for result in results)
    cache_hits = sum(result.cache_hit for result in results)
    return ProductFactRunResult(
        packet=packet,
        model_calls=model_calls,
        cache_hit=bool(results) and cache_hits == len(results),
        fact_count=count,
        failed=bool(results) and failed_batches == len(results),
        elapsed_seconds=time.monotonic() - started,
        warning=" | ".join(warnings),
        batch_count=len(batches),
        cache_hits=cache_hits,
        failed_batches=failed_batches,
    )
