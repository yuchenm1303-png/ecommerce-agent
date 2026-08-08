from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .answer_resolver import BUSINESS_ATTRIBUTE_ALIASES
from .evidence_contract import (
    EvidenceContractError,
    EvidencePacket,
    ExtractedFact,
    ProductIdentity,
    assert_identity_compatible,
)
from .qa_catalog import QuestionCatalog
from .source_bundle import normalize_key


BUSINESS_FACT_KEYS = {
    normalize_key(item)
    for key, aliases in BUSINESS_ATTRIBUTE_ALIASES.items()
    for item in (key, *aliases)
}
BUSINESS_FACT_KEYS.update(
    normalize_key(item)
    for item in (
        "stock",
        "stock quantity",
        "available stock",
        "inventory",
        "inventory quantity",
        "quantity in stock",
    )
)


@dataclass(slots=True)
class EvidenceValidationResult:
    packet: EvidencePacket
    warnings: list[str] = field(default_factory=list)


def is_business_question(value: str) -> bool:
    return normalize_key(value) in BUSINESS_FACT_KEYS


def _allowed_questions(catalog: QuestionCatalog) -> dict[str, str]:
    return {
        normalize_key(item.question): item.question
        for item in catalog.questions
        if not is_business_question(item.question)
    }


def _normalize_fact_key(fact: ExtractedFact, allowed: dict[str, str]) -> str:
    candidates = [fact.key, *fact.aliases]
    matched = {
        allowed[normalize_key(item)]
        for item in candidates
        if normalize_key(item) in allowed
    }
    if len(matched) != 1:
        raise EvidenceContractError(
            f"外部事实 {fact.key!r} 无法唯一对应客户/实时问题；matched={sorted(matched)!r}"
        )
    return next(iter(matched))


def _canonical_fact(fact: ExtractedFact, canonical_key: str) -> ExtractedFact:
    return ExtractedFact(
        key=canonical_key,
        value=fact.value,
        source_type=fact.source_type,
        source_reference=fact.source_reference,
        confidence=fact.confidence,
        evidence_text=fact.evidence_text,
        aliases=(),
        note=fact.note,
    )


def _identity_from_fact(fact: ExtractedFact) -> ProductIdentity:
    key = normalize_key(fact.key)
    value = fact.value if isinstance(fact.value, str) else ""
    if not value:
        return ProductIdentity()
    if key in {normalize_key("SKU"), normalize_key("SKU ID")}:
        return ProductIdentity(sku=value)
    if key in {normalize_key("Model Number"), normalize_key("Model")}:
        return ProductIdentity(model_number=value)
    if key in {normalize_key("Brand"), normalize_key("Brand Name")}:
        return ProductIdentity(brand=value)
    return ProductIdentity()


def _merge_identity(base: ProductIdentity, facts: Iterable[ExtractedFact]) -> ProductIdentity:
    sku = base.sku
    model = base.model_number
    brand = base.brand
    for fact in facts:
        candidate = _identity_from_fact(fact)
        if candidate.sku:
            if sku and normalize_key(sku) != normalize_key(candidate.sku):
                raise EvidenceContractError("同一个 evidence packet 中 SKU 自相矛盾。")
            sku = sku or candidate.sku
        if candidate.model_number:
            if model and normalize_key(model) != normalize_key(candidate.model_number):
                raise EvidenceContractError("同一个 evidence packet 中 Model Number 自相矛盾。")
            model = model or candidate.model_number
        if candidate.brand:
            if brand and normalize_key(brand) != normalize_key(candidate.brand):
                raise EvidenceContractError("同一个 evidence packet 中 Brand 自相矛盾。")
            brand = brand or candidate.brand
    return ProductIdentity(sku=sku, model_number=model, brand=brand)


def validate_evidence_packet(
    packet: EvidencePacket,
    catalog: QuestionCatalog,
    *,
    expected_identity: ProductIdentity | None = None,
) -> EvidenceValidationResult:
    """Validate untrusted image/web/AI extraction against allowed product questions.

    Seller-controlled business fields (including stock/inventory) are never
    admitted through this semantic packet path.
    """

    allowed = _allowed_questions(catalog)
    canonical_facts: list[ExtractedFact] = []
    warnings = list(packet.warnings)
    for fact in packet.facts:
        if is_business_question(fact.key) or any(
            is_business_question(alias) for alias in fact.aliases
        ):
            raise EvidenceContractError(
                f"外部 evidence packet 禁止提供经营字段：{fact.key!r}。"
            )
        canonical_key = _normalize_fact_key(fact, allowed)
        canonical_facts.append(_canonical_fact(fact, canonical_key))

    observed_identity = _merge_identity(packet.identity, canonical_facts)
    if expected_identity is not None:
        assert_identity_compatible(expected_identity, observed_identity)

    return EvidenceValidationResult(
        packet=EvidencePacket(
            identity=observed_identity,
            facts=canonical_facts,
            extractor=packet.extractor,
            warnings=warnings,
        ),
        warnings=warnings,
    )
