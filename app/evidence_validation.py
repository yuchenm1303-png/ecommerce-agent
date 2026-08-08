from __future__ import annotations

from dataclasses import dataclass, field

from .business_fields import is_business_question
from .evidence_contract import (
    EvidenceContractError,
    EvidencePacket,
    ExtractedFact,
    ProductIdentity,
    assert_identity_compatible,
)
from .qa_catalog import QuestionCatalog, QuestionRecord
from .source_bundle import normalize_key


# Legacy evidence-packet validation remains available for non-production tools,
# but seller-operated data can never enter through an image/web/AI packet.
EXTERNAL_EVIDENCE_SOURCE_TYPES = {
    "manufacturer_doc",
    "supplier_doc",
    "product_image",
    "official_doc",
    "official_web",
    "supplier_web",
    "knowledge_base",
    "customer_file",
    "ai_synthesis",
}


class EvidenceValidationError(EvidenceContractError):
    pass


@dataclass(slots=True)
class EvidenceValidationResult:
    packet: EvidencePacket
    warnings: list[str] = field(default_factory=list)
    normalized_fact_count: int = 0


def _question_index(catalog: QuestionCatalog) -> dict[str, list[QuestionRecord]]:
    index: dict[str, list[QuestionRecord]] = {}
    for question in catalog.questions:
        index.setdefault(question.normalized_question, []).append(question)
    return index


def _match_fact_to_question(
    fact: ExtractedFact,
    catalog: QuestionCatalog,
) -> QuestionRecord:
    index = _question_index(catalog)
    matched: dict[tuple[str, int], QuestionRecord] = {}
    for name in (fact.key, *fact.aliases):
        normalized = normalize_key(name)
        if not normalized:
            continue
        for question in index.get(normalized, []):
            matched[(question.source_reference, question.row_number)] = question

    if not matched:
        raise EvidenceValidationError(
            f"抽取事实 {fact.key!r} 无法唯一对应当前 QA/实时问题；禁止把未请求的通用商品属性混入本商品。"
        )
    if len(matched) > 1:
        labels = ", ".join(sorted({item.question for item in matched.values()}))
        raise EvidenceValidationError(
            f"抽取事实 {fact.key!r} 无法唯一对应当前 QA/实时问题；同时匹配：{labels}。"
        )
    return next(iter(matched.values()))


def _normalize_fact(fact: ExtractedFact, question: QuestionRecord) -> ExtractedFact:
    return ExtractedFact(
        key=question.question,
        value=fact.value,
        source_type=fact.source_type,
        source_reference=fact.source_reference,
        confidence=fact.confidence,
        evidence_text=fact.evidence_text,
        aliases=(),
        note=fact.note,
    )


def validate_evidence_packet(
    packet: EvidencePacket,
    catalog: QuestionCatalog,
    *,
    expected_identity: ProductIdentity | None = None,
) -> EvidenceValidationResult:
    """Validate and canonicalize a legacy extractor packet."""

    if expected_identity is not None:
        assert_identity_compatible(expected_identity, packet.identity)

    normalized_facts: list[ExtractedFact] = []
    warnings = list(packet.warnings)
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()

    for fact in packet.facts:
        if fact.source_type not in EXTERNAL_EVIDENCE_SOURCE_TYPES:
            raise EvidenceValidationError(
                f"抽取事实 {fact.key!r} 使用了不允许的 source_type={fact.source_type!r}。"
                "图片/网页/AI packet 不能伪装成 structured/business/config/rule。"
            )

        question = _match_fact_to_question(fact, catalog)
        if is_business_question(question.question):
            raise EvidenceValidationError(
                f"问题 {question.question!r} 是经营字段；禁止从图片、网页或 AI packet 提供答案。"
            )

        normalized = _normalize_fact(fact, question)
        raw_values = (
            tuple(normalize_key(item) for item in normalized.value)
            if isinstance(normalized.value, tuple)
            else (normalize_key(normalized.value),)
        )
        fingerprint = (
            normalized.key,
            normalized.source_type,
            normalized.source_reference,
            raw_values,
        )
        if fingerprint in seen:
            warnings.append(
                f"duplicate fact ignored: {normalized.key} @ {normalized.source_reference}"
            )
            continue
        seen.add(fingerprint)
        normalized_facts.append(normalized)

    normalized_packet = EvidencePacket(
        identity=packet.identity,
        facts=normalized_facts,
        extractor=packet.extractor,
        warnings=warnings,
    )
    return EvidenceValidationResult(
        packet=normalized_packet,
        warnings=warnings,
        normalized_fact_count=len(normalized_facts),
    )
