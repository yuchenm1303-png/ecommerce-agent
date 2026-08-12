from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .business_fields import is_business_question
from .evidence_contract import ProductIdentity, assert_identity_compatible
from .semantic_grounding import GroundingCatalog
from .source_bundle import normalize_key


READY = "ready"
REVIEW = "review"
CONFLICT = "conflict"
MISSING = "missing"
BUSINESS_LOCKED = "business_locked"
DECISION_STATUSES = (READY, REVIEW, CONFLICT, MISSING, BUSINESS_LOCKED)
DECISION_CONTRACT_VERSION = 2


class AIDecisionError(ValueError):
    pass


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
    """Return only executable value options, never qualifier/unit options.

    Raw Makro semantic fields keep a convenience aggregate ``field.options``.
    For numeric+unit controls that aggregate can contain only the qualifier
    selector (cm/kg/...), which is not a legal value option. Prefer options from
    non-qualifier controls. If live controls prove this is a value input plus a
    qualifier control and the value input has no options, return no value options
    instead of falling back to the polluted aggregate list.

    This deliberately mirrors the live-schema contract so planning, field ids,
    required fallbacks and production hard guards all interpret the same field
    shape.
    """

    controls = [
        control
        for control in field.get("controls") or []
        if isinstance(control, dict)
    ]
    output: list[str] = []
    seen: set[str] = set()
    has_qualifier_control = False

    for control in controls:
        if str(control.get("name") or "").endswith("_qualifier"):
            has_qualifier_control = True
            continue
        for value in _clean_options(control.get("options") or []):
            key = normalize_key(value)
            if key not in seen:
                seen.add(key)
                output.append(value)

    if output:
        return output
    if controls and has_qualifier_control:
        return []
    return _clean_options(field.get("options") or [])


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
        "context_text": str(field.get("context_text") or "").strip(),
    }


def field_id(field: dict[str, Any]) -> str:
    # Context text is useful to the AI (for fixed units and nearby UI wording),
    # but it is presentation context rather than the stable field address.
    payload = field_contract(field)
    payload.pop("context_text", None)
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
        if not all(
            isinstance(item, list)
            for item in (raw_values, raw_citations, raw_alternatives, raw_queries)
        ):
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


def _normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _validated_citations(
    citations: Iterable[DecisionCitation],
    grounding: GroundingCatalog,
    *,
    external_sources: dict[str, str],
    warnings: list[str],
    field_identifier: str,
) -> list[DecisionCitation]:
    """Validate provenance addresses only; never re-judge AI semantics in Python.

    The AI is responsible for deciding whether a cited source supports a claim.
    Python only verifies that the cited local source exists, or that a Web source
    was actually persisted from the current search call.  Paraphrases are valid
    evidence text and are not required to be a literal substring of source text.
    """

    output: list[DecisionCitation] = []
    seen: set[tuple[str, str]] = set()
    for citation in citations:
        reference = citation.source_reference.strip()
        # Models often copy the visual ``[source-id]`` delimiter from compact
        # evidence.  Brackets are presentation, not part of the provenance ID.
        if reference.startswith("[") and reference.endswith("]"):
            reference = reference[1:-1].strip()
        valid = grounding.by_id(reference) is not None or reference in external_sources
        if not valid:
            warnings.append(
                f"{field_identifier}: unknown citation source_reference {reference!r} dropped"
            )
            continue
        fingerprint = (reference, _normalize_ws(citation.evidence_text))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        output.append(
            DecisionCitation(
                source_reference=reference,
                evidence_text=citation.evidence_text,
            )
        )
    return output


def _set_missing(decision: FieldDecision, reason: str) -> None:
    decision.status = MISSING
    decision.values = []
    decision.qualifier = ""
    decision.citations = []
    decision.alternatives = []
    decision.search_queries = list(decision.search_queries)
    decision.reason = reason


def validate_ai_decision_packet(
    packet: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
    external_sources: dict[str, str] | None = None,
) -> AIDecisionPacket:
    """Validate only deterministic execution boundaries.

    Product identity interpretation, evidence sufficiency, negative claims,
    dimensional axes, scope and all other product semantics belong to AI.
    Python checks only schema/source identity, provenance addresses, business
    locks and structural completeness.
    """

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
        locked = is_business_question(
            str(target.get("attribute_key") or "")
        ) or is_business_question(str(target.get("label") or ""))
        if locked:
            decision.status = BUSINESS_LOCKED
            decision.values = []
            decision.qualifier = ""
            decision.citations = []
            decision.alternatives = []
            decision.search_queries = []
            decision.reason = (
                decision.reason
                or "seller-operated field; requires explicit business data"
            )
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

        if decision.status == READY:
            meaningful_values = bool(decision.values) and all(
                re.search(r"[\w\d]", value, flags=re.UNICODE)
                for value in decision.values
            )
            if not meaningful_values or not decision.citations:
                warnings.append(
                    f"{decision.field_id}: malformed READY converted to MISSING because value/citation is absent"
                )
                _set_missing(decision, decision.reason or "READY lacked executable value/provenance")
        elif decision.status == CONFLICT:
            usable = [alt for alt in decision.alternatives if alt.values and alt.citations]
            distinct = {
                tuple(normalize_key(value) for value in alternative.values)
                for alternative in usable
            }
            if len(distinct) < 2:
                warnings.append(
                    f"{decision.field_id}: malformed CONFLICT converted to MISSING"
                )
                _set_missing(decision, decision.reason or "CONFLICT lacked two grounded alternatives")
        elif decision.status == REVIEW:
            if not decision.values or not decision.citations:
                warnings.append(
                    f"{decision.field_id}: malformed REVIEW converted to MISSING because value/citation is absent"
                )
                _set_missing(decision, decision.reason or "REVIEW lacked candidate value/provenance")
        elif decision.status == MISSING:
            _set_missing(decision, decision.reason)

        observed[decision.field_id] = decision

    for identifier, target in by_id.items():
        if identifier in observed:
            continue
        locked = is_business_question(
            str(target.get("attribute_key") or "")
        ) or is_business_question(str(target.get("label") or ""))
        status = BUSINESS_LOCKED if locked else MISSING
        observed[identifier] = FieldDecision(
            field_id=identifier,
            status=status,
            reason="seller-operated field" if locked else "AI stage omitted this target field",
        )
        warnings.append(
            f"AI stage omitted field_id={identifier}; synthesized status={status}"
        )

    return AIDecisionPacket(
        identity=packet.identity,
        schema_sha256=expected_schema,
        source_manifest_sha256=expected_sources,
        decisions=[observed[field_id(item)] for item in field_list],
        model_summary=packet.model_summary,
        warnings=warnings,
        extractor=packet.extractor,
    )


def write_ai_decision_packet(packet: AIDecisionPacket, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(packet.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
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
