from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

from .business_fields import is_business_question
from .evidence_contract import ProductIdentity, assert_identity_compatible
from .providers.errors import JSONTaskResponseError
from .semantic_grounding import GroundedSource, GroundingCatalog, IMAGE_KIND, TEXT_KIND
from .source_bundle import normalize_key


READY = "ready"
REVIEW = "review"
CONFLICT = "conflict"
MISSING = "missing"
BUSINESS_LOCKED = "business_locked"
DECISION_STATUSES = (READY, REVIEW, CONFLICT, MISSING, BUSINESS_LOCKED)
DECISION_CONTRACT_VERSION = 1
AI_DECISION_CACHE_VERSION = 1


class AIDecisionError(ValueError):
    pass


class JSONDecisionProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


def _stable_section(value: object) -> str:
    text = re.sub(r"\([^)]*\)", " ", str(value or "").strip())
    return normalize_key(text)


def _clean_options(items: Iterable[object]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in items:
        if isinstance(raw, dict):
            value = str(raw.get("text") or raw.get("value") or "").strip()
        else:
            value = str(raw or "").strip()
        key = normalize_key(value)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def field_options(field: dict[str, Any]) -> list[str]:
    output = _clean_options(field.get("options") or [])
    seen = {normalize_key(value) for value in output}
    for control in field.get("controls") or []:
        if str(control.get("name") or "").endswith("_qualifier"):
            continue
        for value in _clean_options(control.get("options") or []):
            key = normalize_key(value)
            if key not in seen:
                output.append(value)
                seen.add(key)
    return output


def field_qualifier_options(field: dict[str, Any]) -> list[str]:
    output = _clean_options(field.get("qualifier_options") or [])
    seen = {normalize_key(value) for value in output}
    for control in field.get("controls") or []:
        if not str(control.get("name") or "").endswith("_qualifier"):
            continue
        for value in _clean_options(control.get("options") or []):
            key = normalize_key(value)
            if key not in seen:
                output.append(value)
                seen.add(key)
    return output


def field_contract(field: dict[str, Any]) -> dict[str, Any]:
    return {
        "attribute_key": str(field.get("attribute_key") or "").strip(),
        "label": str(field.get("label") or "").strip(),
        "section_heading": str(field.get("section_heading") or "").strip(),
        "required": bool(field.get("required")),
        "multi_value": bool(field.get("multi_value")),
        "options": field_options(field),
        "qualifier_options": field_qualifier_options(field),
        "help_text": str(field.get("help_text") or "").strip(),
    }


def field_id(field: dict[str, Any]) -> str:
    """Return a deterministic structural id for one live Makro field."""

    payload = field_contract(field)
    payload["section_heading"] = _stable_section(payload["section_heading"])
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "mf_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def indexed_fields(fields: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for field in fields:
        identifier = field_id(field)
        if identifier in output:
            raise AIDecisionError(
                "live schema 中存在完全相同且无法唯一寻址的字段；拒绝让 AI 猜目标："
                f" {field.get('section_heading')} / {field.get('label')}"
            )
        output[identifier] = field
    return output


def schema_digest(fields: Iterable[dict[str, Any]]) -> str:
    contracts = [{"field_id": field_id(field), **field_contract(field)} for field in fields]
    contracts.sort(key=lambda item: item["field_id"])
    raw = json.dumps(contracts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def source_manifest_digest(grounding: GroundingCatalog) -> str:
    payload = [
        {
            "source_id": source.source_id,
            "source_type": source.source_type,
            "kind": source.kind,
            "sha256": source.sha256,
        }
        for source in grounding.sources
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True, frozen=True)
class DecisionCitation:
    source_reference: str
    evidence_text: str

    @classmethod
    def from_mapping(cls, payload: dict[str, Any], *, where: str) -> "DecisionCitation":
        reference = str(payload.get("source_reference") or "").strip()
        evidence = str(payload.get("evidence_text") or payload.get("evidence") or "").strip()
        if not reference:
            raise AIDecisionError(f"{where} 缺少 source_reference。")
        if not evidence:
            raise AIDecisionError(f"{where} 缺少 evidence_text。")
        return cls(source_reference=reference, evidence_text=evidence)

    def as_dict(self) -> dict[str, str]:
        return {
            "source_reference": self.source_reference,
            "evidence_text": self.evidence_text,
        }


@dataclass(slots=True, frozen=True)
class DecisionAlternative:
    values: tuple[str, ...]
    qualifier: str = ""
    citations: tuple[DecisionCitation, ...] = ()
    reason: str = ""

    @classmethod
    def from_mapping(cls, payload: dict[str, Any], *, where: str) -> "DecisionAlternative":
        raw_values = payload.get("values") or []
        if not isinstance(raw_values, list):
            raise AIDecisionError(f"{where}.values 必须是数组。")
        values = tuple(str(value).strip() for value in raw_values if str(value).strip())
        raw_citations = payload.get("citations") or []
        if not isinstance(raw_citations, list):
            raise AIDecisionError(f"{where}.citations 必须是数组。")
        citations = tuple(
            DecisionCitation.from_mapping(item, where=f"{where}.citations[{index}]")
            for index, item in enumerate(raw_citations, start=1)
            if isinstance(item, dict)
        )
        return cls(
            values=values,
            qualifier=str(payload.get("qualifier") or "").strip(),
            citations=citations,
            reason=str(payload.get("reason") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "values": list(self.values),
            "qualifier": self.qualifier,
            "citations": [citation.as_dict() for citation in self.citations],
            "reason": self.reason,
        }


@dataclass(slots=True)
class FieldDecision:
    field_id: str
    status: str
    values: list[str] = field(default_factory=list)
    qualifier: str = ""
    confidence: float = 0.0
    citations: list[DecisionCitation] = field(default_factory=list)
    alternatives: list[DecisionAlternative] = field(default_factory=list)
    reason: str = ""
    search_queries: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any], *, index: int) -> "FieldDecision":
        identifier = str(payload.get("field_id") or "").strip()
        status = str(payload.get("status") or "").strip().casefold()
        if not identifier:
            raise AIDecisionError(f"decisions[{index}] 缺少 field_id。")
        if status not in DECISION_STATUSES:
            raise AIDecisionError(
                f"decisions[{index}].status={status!r}；必须是 {DECISION_STATUSES}。"
            )
        raw_values = payload.get("values") or []
        if not isinstance(raw_values, list):
            raise AIDecisionError(f"decisions[{index}].values 必须是数组。")
        values = [str(value).strip() for value in raw_values if str(value).strip()]
        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise AIDecisionError(f"decisions[{index}].confidence 不是有效数字。") from exc
        if not 0.0 <= confidence <= 1.0:
            raise AIDecisionError(f"decisions[{index}].confidence 必须在 0..1。")
        raw_citations = payload.get("citations") or []
        if not isinstance(raw_citations, list):
            raise AIDecisionError(f"decisions[{index}].citations 必须是数组。")
        citations = [
            DecisionCitation.from_mapping(
                item,
                where=f"decisions[{index}].citations[{citation_index}]",
            )
            for citation_index, item in enumerate(raw_citations, start=1)
            if isinstance(item, dict)
        ]
        raw_alternatives = payload.get("alternatives") or []
        if not isinstance(raw_alternatives, list):
            raise AIDecisionError(f"decisions[{index}].alternatives 必须是数组。")
        alternatives = [
            DecisionAlternative.from_mapping(
                item,
                where=f"decisions[{index}].alternatives[{alt_index}]",
            )
            for alt_index, item in enumerate(raw_alternatives, start=1)
            if isinstance(item, dict)
        ]
        raw_queries = payload.get("search_queries") or []
        if not isinstance(raw_queries, list):
            raise AIDecisionError(f"decisions[{index}].search_queries 必须是数组。")
        queries: list[str] = []
        seen_queries: set[str] = set()
        for query in raw_queries:
            value = str(query).strip()
            key = value.casefold()
            if not value or key in seen_queries:
                continue
            seen_queries.add(key)
            queries.append(value[:300])
        return cls(
            field_id=identifier,
            status=status,
            values=values,
            qualifier=str(payload.get("qualifier") or "").strip(),
            confidence=confidence,
            citations=citations,
            alternatives=alternatives,
            reason=str(payload.get("reason") or "").strip(),
            search_queries=queries[:3],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "status": self.status,
            "values": list(self.values),
            "qualifier": self.qualifier,
            "confidence": self.confidence,
            "citations": [citation.as_dict() for citation in self.citations],
            "alternatives": [alternative.as_dict() for alternative in self.alternatives],
            "reason": self.reason,
            "search_queries": list(self.search_queries),
        }


@dataclass(slots=True)
class AIDecisionPacket:
    identity: ProductIdentity
    schema_sha256: str
    source_manifest_sha256: str
    decisions: list[FieldDecision]
    model_summary: str = ""
    warnings: list[str] = field(default_factory=list)
    extractor: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": DECISION_CONTRACT_VERSION,
            "extractor": self.extractor,
            "product_identity": {
                "sku": self.identity.sku,
                "model_number": self.identity.model_number,
                "brand": self.identity.brand,
            },
            "schema_sha256": self.schema_sha256,
            "source_manifest_sha256": self.source_manifest_sha256,
            "decisions": [decision.as_dict() for decision in self.decisions],
            "model_summary": self.model_summary,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "AIDecisionPacket":
        if not isinstance(payload, dict):
            raise AIDecisionError("AI decision packet 顶层必须是 object。")
        try:
            version = int(payload.get("contract_version", DECISION_CONTRACT_VERSION))
        except (TypeError, ValueError) as exc:
            raise AIDecisionError("AI decision contract_version 无效。") from exc
        if version != DECISION_CONTRACT_VERSION:
            raise AIDecisionError(
                f"AI decision contract_version={version} 不受支持。"
            )
        raw_decisions = payload.get("decisions")
        if not isinstance(raw_decisions, list):
            raise AIDecisionError("AI decision packet 缺少 decisions 数组。")
        warnings = payload.get("warnings") or []
        if not isinstance(warnings, list):
            raise AIDecisionError("warnings 必须是数组。")
        return cls(
            identity=ProductIdentity.from_mapping(payload.get("product_identity")),
            schema_sha256=str(payload.get("schema_sha256") or "").strip(),
            source_manifest_sha256=str(payload.get("source_manifest_sha256") or "").strip(),
            decisions=[
                FieldDecision.from_mapping(item, index=index)
                for index, item in enumerate(raw_decisions, start=1)
                if isinstance(item, dict)
            ],
            model_summary=str(payload.get("model_summary") or "").strip(),
            warnings=[str(item) for item in warnings],
            extractor=str(payload.get("extractor") or "").strip(),
        )


AI_DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "contract_version": {"type": "integer", "enum": [DECISION_CONTRACT_VERSION]},
        "product_identity": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "sku": {"type": "string"},
                "model_number": {"type": "string"},
                "brand": {"type": "string"},
            },
            "required": ["sku", "model_number", "brand"],
        },
        "schema_sha256": {"type": "string"},
        "source_manifest_sha256": {"type": "string"},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "field_id": {"type": "string"},
                    "status": {"type": "string", "enum": list(DECISION_STATUSES)},
                    "values": {"type": "array", "items": {"type": "string"}},
                    "qualifier": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "source_reference": {"type": "string"},
                                "evidence_text": {"type": "string"},
                            },
                            "required": ["source_reference", "evidence_text"],
                        },
                    },
                    "alternatives": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "values": {"type": "array", "items": {"type": "string"}},
                                "qualifier": {"type": "string"},
                                "citations": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {
                                            "source_reference": {"type": "string"},
                                            "evidence_text": {"type": "string"},
                                        },
                                        "required": ["source_reference", "evidence_text"],
                                    },
                                },
                                "reason": {"type": "string"},
                            },
                            "required": ["values", "qualifier", "citations", "reason"],
                        },
                    },
                    "reason": {"type": "string"},
                    "search_queries": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "field_id",
                    "status",
                    "values",
                    "qualifier",
                    "confidence",
                    "citations",
                    "alternatives",
                    "reason",
                    "search_queries",
                ],
            },
        },
        "model_summary": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "contract_version",
        "product_identity",
        "schema_sha256",
        "source_manifest_sha256",
        "decisions",
        "model_summary",
        "warnings",
    ],
}


AI_RESOLUTION_SYSTEM_INSTRUCTION = (
    "You are the primary product-listing resolver. Understand the product semantically and decide "
    "the best answer for every target marketplace field from the supplied sources. Natural-language "
    "translation, synonym understanding, counting, straightforward unit interpretation and mapping a "
    "source specification to the marketplace field are your job, not local Python rules. Never invent "
    "a product fact that is unsupported by the supplied sources. Preserve exact variant identity."
)

AI_RESOLUTION_RULES = [
    "Return exactly one decision for every target field_id and never invent a field_id.",
    "Use all grounded sources jointly. You may combine agreeing sources and reason across languages.",
    "READY means the supplied evidence supports one best answer strongly enough for automatic listing entry.",
    "REVIEW means there is a plausible answer but evidence, scope, identity or interpretation is not strong enough for automatic entry.",
    "CONFLICT means credible supplied sources support materially incompatible answers for the same target field. Preserve the competing alternatives and their citations instead of choosing silently.",
    "MISSING means the supplied sources do not contain enough evidence. Suggest up to three focused web search queries when normal product research could reasonably answer it.",
    "BUSINESS_LOCKED is mandatory for seller-operated fields such as price, stock, MOQ, fulfilment, shipping or listing status. Never infer those from product content.",
    "For READY, REVIEW and CONFLICT, cite only supplied source ids. Text citations should quote the smallest useful source excerpt; image citations should describe the exact visible evidence.",
    "Do not treat lack of mention as evidence for No/False/Not included.",
    "Do not confuse packaging dimensions with product dimensions, manual language with device UI language, product brand with compatible vehicle brand, or cabin camera with rear camera unless the sources actually establish that relationship.",
    "When an option list is supplied, return the marketplace option text exactly when one option clearly matches the evidence.",
    "Do not use unstated web knowledge in this pass. If external research is needed, mark MISSING or REVIEW and emit focused search_queries.",
]


def _target_field_payload(field: dict[str, Any]) -> dict[str, Any]:
    contract = field_contract(field)
    locked = is_business_question(contract["attribute_key"]) or is_business_question(contract["label"])
    return {
        "field_id": field_id(field),
        **contract,
        "business_locked": locked,
    }


def build_ai_resolution_request(
    fields: Iterable[dict[str, Any]],
    grounding: GroundingCatalog,
    *,
    identity: ProductIdentity = ProductIdentity(),
) -> dict[str, Any]:
    field_list = list(fields)
    return {
        "task": "resolve_all_live_marketplace_fields_from_product_sources",
        "system_instruction": AI_RESOLUTION_SYSTEM_INSTRUCTION,
        "prompt_instruction": (
            "Resolve the whole product in one pass. Semantic interpretation belongs to you; local code "
            "will only verify structural safety, citations and marketplace control constraints."
        ),
        "product_identity": {
            "sku": identity.sku,
            "model_number": identity.model_number,
            "brand": identity.brand,
        },
        "schema_sha256": schema_digest(field_list),
        "source_manifest_sha256": source_manifest_digest(grounding),
        "target_fields": [_target_field_payload(field) for field in field_list],
        "rules": list(AI_RESOLUTION_RULES),
        "grounded_sources": grounding.as_request_list(),
        "json_contract": AI_DECISION_JSON_SCHEMA,
    }


def _normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _citation_is_grounded(citation: DecisionCitation, source: GroundedSource) -> bool:
    if source.kind == IMAGE_KIND:
        return bool(citation.evidence_text.strip())
    if source.kind != TEXT_KIND:
        return False
    wanted = _normalize_ws(citation.evidence_text)
    return bool(wanted) and wanted in _normalize_ws(source.content)


def _validated_citations(
    citations: Iterable[DecisionCitation],
    grounding: GroundingCatalog,
    *,
    warnings: list[str],
    field_identifier: str,
) -> list[DecisionCitation]:
    output: list[DecisionCitation] = []
    seen: set[tuple[str, str]] = set()
    for citation in citations:
        source = grounding.by_id(citation.source_reference)
        if source is None:
            warnings.append(
                f"{field_identifier}: unknown citation source {citation.source_reference!r} dropped"
            )
            continue
        if not _citation_is_grounded(citation, source):
            warnings.append(
                f"{field_identifier}: ungrounded citation {citation.source_reference!r} dropped"
            )
            continue
        fingerprint = (citation.source_reference, _normalize_ws(citation.evidence_text))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        output.append(citation)
    return output


def validate_ai_decision_packet(
    packet: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
) -> AIDecisionPacket:
    """Validate hard boundaries only; product semantics stay with AI."""

    field_list = list(fields)
    by_id = indexed_fields(field_list)
    expected_schema = schema_digest(field_list)
    expected_sources = source_manifest_digest(grounding)
    assert_identity_compatible(expected_identity, packet.identity)

    if packet.schema_sha256 and packet.schema_sha256 != expected_schema:
        raise AIDecisionError("AI decision packet 的 live schema digest 与当前规划 schema 不一致。")
    if packet.source_manifest_sha256 and packet.source_manifest_sha256 != expected_sources:
        raise AIDecisionError("AI decision packet 的 source manifest digest 与当前商品资料不一致。")

    warnings = list(packet.warnings)
    observed: dict[str, FieldDecision] = {}
    for decision in packet.decisions:
        if decision.field_id not in by_id:
            warnings.append(f"unknown field_id ignored: {decision.field_id}")
            continue
        if decision.field_id in observed:
            raise AIDecisionError(f"AI decision field_id 重复：{decision.field_id}")

        target = by_id[decision.field_id]
        locked = is_business_question(str(target.get("attribute_key") or "")) or is_business_question(
            str(target.get("label") or "")
        )
        if locked:
            decision.status = BUSINESS_LOCKED
            decision.values = []
            decision.qualifier = ""
            decision.citations = []
            decision.alternatives = []
            decision.search_queries = []
            decision.reason = decision.reason or "seller-operated field; requires explicit business data"
            observed[decision.field_id] = decision
            continue

        decision.citations = _validated_citations(
            decision.citations,
            grounding,
            warnings=warnings,
            field_identifier=decision.field_id,
        )
        validated_alternatives: list[DecisionAlternative] = []
        for alternative in decision.alternatives:
            citations = _validated_citations(
                alternative.citations,
                grounding,
                warnings=warnings,
                field_identifier=decision.field_id,
            )
            validated_alternatives.append(
                DecisionAlternative(
                    values=alternative.values,
                    qualifier=alternative.qualifier,
                    citations=tuple(citations),
                    reason=alternative.reason,
                )
            )
        decision.alternatives = validated_alternatives

        if decision.status == READY:
            if not decision.values or not decision.citations:
                warnings.append(
                    f"{decision.field_id}: READY downgraded to REVIEW because value/citation is missing"
                )
                decision.status = REVIEW
        elif decision.status == CONFLICT:
            usable = [
                alternative
                for alternative in decision.alternatives
                if alternative.values and alternative.citations
            ]
            distinct = {
                tuple(normalize_key(value) for value in alternative.values)
                for alternative in usable
            }
            if len(distinct) < 2:
                warnings.append(
                    f"{decision.field_id}: malformed CONFLICT downgraded to REVIEW"
                )
                decision.status = REVIEW
        elif decision.status == MISSING:
            decision.values = []
            decision.qualifier = ""
            decision.citations = []
            decision.alternatives = []

        observed[decision.field_id] = decision

    for identifier, field in by_id.items():
        if identifier in observed:
            continue
        locked = is_business_question(str(field.get("attribute_key") or "")) or is_business_question(
            str(field.get("label") or "")
        )
        status = BUSINESS_LOCKED if locked else MISSING
        observed[identifier] = FieldDecision(
            field_id=identifier,
            status=status,
            reason="seller-operated field" if locked else "model omitted this target field",
        )
        warnings.append(f"model omitted field_id={identifier}; synthesized status={status}")

    ordered = [observed[field_id(field)] for field in field_list]
    return AIDecisionPacket(
        identity=packet.identity,
        schema_sha256=expected_schema,
        source_manifest_sha256=expected_sources,
        decisions=ordered,
        model_summary=packet.model_summary,
        warnings=warnings,
        extractor=packet.extractor,
    )


def decision_contract_digest() -> str:
    payload = {
        "contract_version": DECISION_CONTRACT_VERSION,
        "system": AI_RESOLUTION_SYSTEM_INSTRUCTION,
        "rules": AI_RESOLUTION_RULES,
        "schema": AI_DECISION_JSON_SCHEMA,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_key(
    provider: JSONDecisionProvider,
    cache_namespace: str,
    fields: list[dict[str, Any]],
    grounding: GroundingCatalog,
    identity: ProductIdentity,
) -> str:
    payload = {
        "cache_version": AI_DECISION_CACHE_VERSION,
        "decision_contract_sha256": decision_contract_digest(),
        "provider": provider.name,
        "cache_namespace": cache_namespace,
        "identity": {
            "sku": identity.sku,
            "model_number": identity.model_number,
            "brand": identity.brand,
        },
        "schema_sha256": schema_digest(fields),
        "source_manifest_sha256": source_manifest_digest(grounding),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class AIResolutionRunResult:
    packet: AIDecisionPacket
    model_calls: int
    cache_hit: bool
    repair_attempts: int
    elapsed_seconds: float


def _validate_model_response(
    raw: dict[str, Any],
    *,
    provider_name: str,
    fields: list[dict[str, Any]],
    grounding: GroundingCatalog,
    expected_identity: ProductIdentity,
) -> AIDecisionPacket:
    packet = AIDecisionPacket.from_mapping(raw)
    packet.extractor = packet.extractor or provider_name
    return validate_ai_decision_packet(
        packet,
        fields,
        grounding,
        expected_identity=expected_identity,
    )


def _repair_instruction(request: dict[str, Any], error: Exception) -> dict[str, Any]:
    repaired = dict(request)
    repaired["validation_error"] = str(error)
    repaired["prompt_instruction"] = (
        str(request.get("prompt_instruction") or "")
        + "\nYour prior response was received but failed the JSON/decision structural safety contract. "
        "Return one corrected complete packet; do not add unsupported product facts."
    )
    return repaired


def run_ai_resolution(
    provider: JSONDecisionProvider,
    fields: Iterable[dict[str, Any]],
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
    cache_dir: str | Path | None = None,
    cache_namespace: str = "",
    max_repair_attempts: int = 1,
) -> AIResolutionRunResult:
    """Resolve one whole product.

    Normal path is exactly one multimodal model call. A second call is permitted
    only when a model response was actually received but its JSON/decision
    structure is invalid. Transport/API/local-input failures are never semantic
    repair retries.
    """

    if max_repair_attempts not in {0, 1}:
        raise ValueError("max_repair_attempts 必须是 0 或 1。")
    field_list = list(fields)
    indexed_fields(field_list)
    started = time.monotonic()
    cache_root = Path(cache_dir) if cache_dir is not None else None
    key = _cache_key(
        provider,
        cache_namespace,
        field_list,
        grounding,
        expected_identity,
    )
    cache_path = cache_root / f"ai-decision-{key}.json" if cache_root is not None else None

    if cache_path is not None and cache_path.is_file():
        try:
            cached = AIDecisionPacket.from_mapping(
                json.loads(cache_path.read_text(encoding="utf-8"))
            )
            validated = validate_ai_decision_packet(
                cached,
                field_list,
                grounding,
                expected_identity=expected_identity,
            )
            return AIResolutionRunResult(
                packet=validated,
                model_calls=0,
                cache_hit=True,
                repair_attempts=0,
                elapsed_seconds=time.monotonic() - started,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    request = build_ai_resolution_request(
        field_list,
        grounding,
        identity=expected_identity,
    )
    calls = 0
    repairs = 0
    last_error: Exception | None = None

    for attempt in range(max_repair_attempts + 1):
        try:
            calls += 1
            raw = provider.extract_json(request)
        except JSONTaskResponseError as exc:
            # A remote model response existed but its text/JSON envelope was
            # unusable. This is the only provider-side failure eligible for one
            # structural correction call.
            last_error = exc
        except Exception as exc:
            raise AIDecisionError(
                f"AI provider failed before a usable response; no semantic repair attempted: {exc}"
            ) from exc
        else:
            try:
                validated = _validate_model_response(
                    raw,
                    provider_name=provider.name,
                    fields=field_list,
                    grounding=grounding,
                    expected_identity=expected_identity,
                )
            except (AIDecisionError, ValueError, TypeError) as exc:
                last_error = exc
            else:
                if cache_path is not None:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
                    temp.write_text(
                        json.dumps(validated.as_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    temp.replace(cache_path)
                return AIResolutionRunResult(
                    packet=validated,
                    model_calls=calls,
                    cache_hit=False,
                    repair_attempts=repairs,
                    elapsed_seconds=time.monotonic() - started,
                )

        if attempt >= max_repair_attempts:
            break
        repairs += 1
        request = _repair_instruction(request, last_error or AIDecisionError("invalid response"))

    raise AIDecisionError(f"AI product resolution failed after structural validation: {last_error}")


def write_ai_decision_packet(packet: AIDecisionPacket, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(packet.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def load_ai_decision_packet(
    path: str | Path,
    fields: Iterable[dict[str, Any]],
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
) -> AIDecisionPacket:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    packet = AIDecisionPacket.from_mapping(payload)
    return validate_ai_decision_packet(
        packet,
        fields,
        grounding,
        expected_identity=expected_identity,
    )
