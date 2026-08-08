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
DECISION_CONTRACT_VERSION = 2
AI_DECISION_CACHE_VERSION = 2


class AIDecisionError(ValueError):
    pass


class JSONDecisionProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


def _stable_section(value: object) -> str:
    return normalize_key(re.sub(r"\([^)]*\)", " ", str(value or "").strip()))


def _clean_options(items: Iterable[object]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in items:
        value = (
            str(raw.get("text") or raw.get("value") or "").strip()
            if isinstance(raw, dict)
            else str(raw or "").strip()
        )
        key = normalize_key(value)
        if value and key and key not in seen:
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
                seen.add(key)
                output.append(value)
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
                seen.add(key)
                output.append(value)
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
    payload = field_contract(field)
    payload["section_heading"] = _stable_section(payload["section_heading"])
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "mf_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def indexed_fields(fields: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for item in fields:
        identifier = field_id(item)
        if identifier in output:
            raise AIDecisionError(
                "live schema 中存在完全相同且无法唯一寻址的字段；拒绝让 AI 猜目标："
                f" {item.get('section_heading')} / {item.get('label')}"
            )
        output[identifier] = item
    return output


def schema_digest(fields: Iterable[dict[str, Any]]) -> str:
    contracts = [{"field_id": field_id(item), **field_contract(item)} for item in fields]
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
        return cls(reference, evidence)

    def as_dict(self) -> dict[str, str]:
        return {"source_reference": self.source_reference, "evidence_text": self.evidence_text}


@dataclass(slots=True, frozen=True)
class DecisionAlternative:
    values: tuple[str, ...]
    qualifier: str = ""
    citations: tuple[DecisionCitation, ...] = ()
    reason: str = ""

    @classmethod
    def from_mapping(cls, payload: dict[str, Any], *, where: str) -> "DecisionAlternative":
        raw_values = payload.get("values") or []
        raw_citations = payload.get("citations") or []
        if not isinstance(raw_values, list) or not isinstance(raw_citations, list):
            raise AIDecisionError(f"{where}.values/citations 必须是数组。")
        return cls(
            values=tuple(str(value).strip() for value in raw_values if str(value).strip()),
            qualifier=str(payload.get("qualifier") or "").strip(),
            citations=tuple(
                DecisionCitation.from_mapping(item, where=f"{where}.citations[{index}]")
                for index, item in enumerate(raw_citations, start=1)
                if isinstance(item, dict)
            ),
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
            raise AIDecisionError(f"decisions[{index}].status={status!r}；必须是 {DECISION_STATUSES}。")

        raw_values = payload.get("values") or []
        raw_citations = payload.get("citations") or []
        raw_alternatives = payload.get("alternatives") or []
        raw_queries = payload.get("search_queries") or []
        if not all(isinstance(item, list) for item in (raw_values, raw_citations, raw_alternatives, raw_queries)):
            raise AIDecisionError(
                f"decisions[{index}] 的 values/citations/alternatives/search_queries 必须是数组。"
            )

        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise AIDecisionError(f"decisions[{index}].confidence 不是有效数字。") from exc
        if not 0.0 <= confidence <= 1.0:
            raise AIDecisionError(f"decisions[{index}].confidence 必须在 0..1。")

        queries: list[str] = []
        seen_queries: set[str] = set()
        for query in raw_queries:
            value = str(query).strip()
            key = value.casefold()
            if value and key not in seen_queries:
                seen_queries.add(key)
                queries.append(value[:300])

        return cls(
            field_id=identifier,
            status=status,
            values=[str(value).strip() for value in raw_values if str(value).strip()],
            qualifier=str(payload.get("qualifier") or "").strip(),
            confidence=confidence,
            citations=[
                DecisionCitation.from_mapping(
                    item,
                    where=f"decisions[{index}].citations[{citation_index}]",
                )
                for citation_index, item in enumerate(raw_citations, start=1)
                if isinstance(item, dict)
            ],
            alternatives=[
                DecisionAlternative.from_mapping(
                    item,
                    where=f"decisions[{index}].alternatives[{alt_index}]",
                )
                for alt_index, item in enumerate(raw_alternatives, start=1)
                if isinstance(item, dict)
            ],
            reason=str(payload.get("reason") or "").strip(),
            search_queries=queries[:2],
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
            raise AIDecisionError(f"AI decision contract_version={version} 不受支持。")
        raw_decisions = payload.get("decisions")
        warnings = payload.get("warnings") or []
        if not isinstance(raw_decisions, list) or not isinstance(warnings, list):
            raise AIDecisionError("AI decision packet 的 decisions/warnings 格式无效。")
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


# Compact model-output contract. Packet identity/digests are attached locally;
# the model no longer wastes output tokens echoing data that Python already knows.
AI_DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["field_id", "status"],
                "properties": {
                    "field_id": {"type": "string"},
                    "status": {"type": "string", "enum": list(DECISION_STATUSES)},
                    "values": {"type": "array", "items": {"type": "string"}},
                    "qualifier": {"type": "string"},
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["source_reference", "evidence_text"],
                            "properties": {
                                "source_reference": {"type": "string"},
                                "evidence_text": {"type": "string"},
                            },
                        },
                    },
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


AI_RESOLUTION_SYSTEM_INSTRUCTION = (
    "You are the product-listing resolver. Read all supplied product evidence jointly and fill the "
    "marketplace fields semantically. Translation, synonyms, counting, option matching, specification "
    "interpretation and conflict judgment are your job. Never invent unsupported product facts. "
    "Preserve the exact selected product/variant. Return compact JSON only."
)

AI_RESOLUTION_RULES = [
    "Return one compact decision for every target field_id; never invent a field_id.",
    "READY: one answer is strongly supported. Include values and the smallest useful citations.",
    "REVIEW: a plausible answer exists but identity/scope/evidence is insufficient for automatic entry; include candidate values/citations when available.",
    "Before READY, compare all grounded sources for the same attribute and scope. If two credible explicit values disagree and neither clearly supersedes the other, status MUST be CONFLICT, never READY or REVIEW.",
    "CONFLICT: include at least two cited alternatives and do not silently choose one conflicting value.",
    "MISSING: current evidence cannot answer. Leave values empty; add at most two focused search_queries if normal web research could answer it.",
    "For REVIEW or CONFLICT, also add search_queries when web research could resolve the uncertainty.",
    "BUSINESS_LOCKED: seller-operated price, stock, MOQ, fulfilment, shipping or listing status. Never infer these from product content.",
    "Citations may use only supplied source_id values. For text quote the short supporting excerpt; for images describe the exact visible evidence.",
    "Never infer No/False/Not included from absence; a negative value requires explicit negative evidence. Package/packaging dimensions and weight may answer only packaging fields, never product-body Width/Height/Depth/Weight. Manual language is not device UI language; product brand is not compatible vehicle brand; cabin/interior camera is not rear/back camera unless explicit evidence establishes it.",
    "If target options are supplied, return the exact marketplace option text when one clearly matches.",
    "If multi_value=false, return exactly one string in values. For a free-text single-value field that summarizes several supported features, combine them into one concise string instead of multiple array elements.",
    "If qualifier_options are supplied, put the magnitude/value only in values and put the exact unit once in qualifier; never return the unit as a second value or append it to a numeric value.",
    "Do not invent warranty or service terms; READY for warranty fields requires explicit warranty evidence.",
    "Do not use external web knowledge in this local pass.",
    "Omit confidence and reason when they add no value; omit alternatives except for conflicts; omit search_queries when no research is needed.",
]


def _target_field_payload(item: dict[str, Any]) -> dict[str, Any]:
    contract = field_contract(item)
    locked = is_business_question(contract["attribute_key"]) or is_business_question(contract["label"])
    payload: dict[str, Any] = {
        "field_id": field_id(item),
        "attribute_key": contract["attribute_key"],
        "label": contract["label"],
        "section_heading": contract["section_heading"],
        "required": contract["required"],
        "multi_value": contract["multi_value"],
        "business_locked": locked,
    }
    if contract["options"]:
        payload["options"] = contract["options"]
    if contract["qualifier_options"]:
        payload["qualifier_options"] = contract["qualifier_options"]
    if contract["help_text"]:
        payload["help_text"] = contract["help_text"]
    return payload


def build_ai_resolution_request(
    fields: Iterable[dict[str, Any]],
    grounding: GroundingCatalog,
    *,
    identity: ProductIdentity = ProductIdentity(),
) -> dict[str, Any]:
    field_list = list(fields)
    return {
        "task": "fill_marketplace_fields_from_local_product_evidence",
        "system_instruction": AI_RESOLUTION_SYSTEM_INSTRUCTION,
        "prompt_instruction": (
            "Fill the target fields from the complete local product evidence in one pass. "
            "Keep the JSON compact: output decisions, not an explanation of your process."
        ),
        "product_identity": {
            "sku": identity.sku,
            "model_number": identity.model_number,
            "brand": identity.brand,
        },
        "target_fields": [_target_field_payload(item) for item in field_list],
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
    external_sources: dict[str, str],
    warnings: list[str],
    field_identifier: str,
) -> list[DecisionCitation]:
    output: list[DecisionCitation] = []
    seen: set[tuple[str, str]] = set()
    for citation in citations:
        source = grounding.by_id(citation.source_reference)
        if source is not None:
            valid = _citation_is_grounded(citation, source)
        else:
            external_content = external_sources.get(citation.source_reference, "")
            wanted = _normalize_ws(citation.evidence_text)
            valid = bool(wanted) and wanted in _normalize_ws(external_content)
        if not valid:
            warnings.append(
                f"{field_identifier}: unknown/ungrounded citation {citation.source_reference!r} dropped"
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
    external_sources: dict[str, str] | None = None,
) -> AIDecisionPacket:
    """Validate only structural/provenance boundaries; product semantics stay with AI."""

    field_list = list(fields)
    by_id = indexed_fields(field_list)
    expected_schema = schema_digest(field_list)
    expected_sources = source_manifest_digest(grounding)
    assert_identity_compatible(expected_identity, packet.identity)
    if packet.schema_sha256 and packet.schema_sha256 != expected_schema:
        raise AIDecisionError("AI decision packet 的 live schema digest 与当前规划 schema 不一致。")
    if packet.source_manifest_sha256 and packet.source_manifest_sha256 != expected_sources:
        raise AIDecisionError("AI decision packet 的 source manifest digest 与当前商品资料不一致。")

    external = external_sources or {}
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
            external_sources=external,
            warnings=warnings,
            field_identifier=decision.field_id,
        )
        validated_alternatives: list[DecisionAlternative] = []
        for alternative in decision.alternatives:
            citations = _validated_citations(
                alternative.citations,
                grounding,
                external_sources=external,
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

        if decision.status == READY and (not decision.values or not decision.citations):
            warnings.append(
                f"{decision.field_id}: READY downgraded to REVIEW because value/citation is missing"
            )
            decision.status = REVIEW
        elif decision.status == CONFLICT:
            usable = [alt for alt in decision.alternatives if alt.values and alt.citations]
            distinct = {
                tuple(normalize_key(value) for value in alternative.values)
                for alternative in usable
            }
            if len(distinct) < 2:
                warnings.append(f"{decision.field_id}: malformed CONFLICT downgraded to REVIEW")
                decision.status = REVIEW
        elif decision.status == MISSING:
            decision.values = []
            decision.qualifier = ""
            decision.citations = []
            decision.alternatives = []
        observed[decision.field_id] = decision

    for identifier, target in by_id.items():
        if identifier in observed:
            continue
        locked = is_business_question(str(target.get("attribute_key") or "")) or is_business_question(
            str(target.get("label") or "")
        )
        status = BUSINESS_LOCKED if locked else MISSING
        observed[identifier] = FieldDecision(
            field_id=identifier,
            status=status,
            reason="seller-operated field" if locked else "model omitted this target field",
        )
        warnings.append(f"model omitted field_id={identifier}; synthesized status={status}")

    return AIDecisionPacket(
        identity=packet.identity,
        schema_sha256=expected_schema,
        source_manifest_sha256=expected_sources,
        decisions=[observed[field_id(item)] for item in field_list],
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
    if not isinstance(raw, dict):
        raise AIDecisionError("AI model output 顶层必须是 JSON object。")
    raw_decisions = raw.get("decisions")
    if not isinstance(raw_decisions, list):
        raise AIDecisionError("AI model output 缺少 decisions 数组。")
    packet = AIDecisionPacket(
        identity=expected_identity,
        schema_sha256=schema_digest(fields),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=[
            FieldDecision.from_mapping(item, index=index)
            for index, item in enumerate(raw_decisions, start=1)
            if isinstance(item, dict)
        ],
        model_summary=str(raw.get("model_summary") or "").strip(),
        warnings=[],
        extractor=str(raw.get("extractor") or provider_name).strip() or provider_name,
    )
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
        + "\nThe previous JSON response failed the structural contract. Return one corrected compact JSON object."
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
    max_repair_attempts: int = 0,
) -> AIResolutionRunResult:
    """Resolve one whole product. Default path is one call and no automatic full rerun."""

    if max_repair_attempts not in {0, 1}:
        raise ValueError("max_repair_attempts 必须是 0 或 1。")
    field_list = list(fields)
    indexed_fields(field_list)
    started = time.monotonic()
    cache_root = Path(cache_dir) if cache_dir is not None else None
    key = _cache_key(provider, cache_namespace, field_list, grounding, expected_identity)
    cache_path = cache_root / f"ai-decision-{key}.json" if cache_root is not None else None

    if cache_path is not None and cache_path.is_file():
        try:
            cached = AIDecisionPacket.from_mapping(json.loads(cache_path.read_text(encoding="utf-8")))
            validated = validate_ai_decision_packet(
                cached, field_list, grounding, expected_identity=expected_identity
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

    request = build_ai_resolution_request(field_list, grounding, identity=expected_identity)
    calls = 0
    repairs = 0
    last_error: Exception | None = None
    for attempt in range(max_repair_attempts + 1):
        try:
            calls += 1
            raw = provider.extract_json(request)
        except JSONTaskResponseError as exc:
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
    target.write_text(json.dumps(packet.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _embedded_external_sources(payload: dict[str, Any]) -> dict[str, str]:
    raw_sources = payload.get("web_sources") or []
    if not isinstance(raw_sources, list):
        raise AIDecisionError("web_sources 必须是数组。")
    output: dict[str, str] = {}
    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        reference = str(item.get("source_reference") or "").strip()
        url = str(item.get("url") or "").strip()
        content = str(item.get("content") or "").strip()
        if not reference or not url or not content:
            raise AIDecisionError("embedded web source 缺少 source_reference/url/content。")
        if reference in output:
            raise AIDecisionError(f"embedded web source_reference 重复：{reference}")
        output[reference] = content
    return output


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
        external_sources=_embedded_external_sources(payload),
    )
