from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .answer_resolver import (
    BUSINESS_ALLOWED_SOURCE_TYPES,
    BUSINESS_ATTRIBUTE_ALIASES,
    CONFLICT,
    MISSING,
    NEEDS_REVIEW,
    RESOLVED,
    ResolvedAnswer,
    resolve_field,
)
from .evidence_validation import is_business_question
from .fact_validators import validate_resolved_answer
from .qa_catalog import QuestionCatalog, QuestionRecord
from .source_bundle import ProductSourceBundle, normalize_key


GATE_LOW_CONFIDENCE = "low_confidence"
GATE_MISSING_SOURCE_REFERENCE = "missing_source_reference"
GATE_FIELD_CONSTRAINT = "field_constraint"
GATE_BUSINESS_SOURCE = "business_source"


@dataclass(slots=True, frozen=True)
class ResolutionPolicy:
    auto_fill_min_confidence: float = 0.85
    ai_auto_fill_min_confidence: float = 0.92
    require_source_reference: bool = True
    validate_field_constraints: bool = True


@dataclass(slots=True)
class ResolutionRecord:
    attribute_key: str
    label: str
    status: str
    answer: str | None
    answer_values: list[str]
    qualifier: str | None
    confidence: float
    source_type: str | None
    source_reference: str | None
    evidence: str | None
    detail: str
    eligible_for_autofill: bool
    preview_eligible: bool = False
    gate_reason: str = ""
    provenance: list[dict[str, Any]] = field(default_factory=list)
    question_number: str = ""
    question_explanation: str = ""
    question_category: str = ""
    question_unit: str = ""
    question_options: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "attribute_key": self.attribute_key,
            "label": self.label,
            "status": self.status,
            "answer": self.answer,
            "answer_values": self.answer_values,
            "qualifier": self.qualifier,
            "confidence": self.confidence,
            "source_type": self.source_type,
            "source_reference": self.source_reference,
            "evidence": self.evidence,
            "detail": self.detail,
            "eligible_for_autofill": self.eligible_for_autofill,
            "preview_eligible": self.preview_eligible,
            "gate_reason": self.gate_reason,
            "provenance": self.provenance,
            "question_number": self.question_number,
            "question_explanation": self.question_explanation,
            "question_category": self.question_category,
            "question_unit": self.question_unit,
            "question_options": self.question_options,
        }


def _default_candidate_keys(field: dict[str, Any]) -> list[str]:
    key = str(field.get("attribute_key") or "")
    label = str(field.get("label") or "")
    values = [key, label]
    values.extend(BUSINESS_ATTRIBUTE_ALIASES.get(key, ()))
    return [item for item in values if item]


def _matched_evidence_keys(
    semantic_field: dict[str, Any],
    question: QuestionRecord | None,
) -> list[str]:
    """Use the deterministic QA match as the evidence namespace.

    Once matcher has selected one QA question for a live field, a reused/broken
    DOM attribute_key must not pull in evidence from a different question. Real
    Makro has exposed label=Length with attribute_key=height; using both keys
    made Height evidence contaminate Length. Business fields retain their
    reviewed aliases because those aliases represent explicit seller inputs.
    """

    if question is None:
        return _default_candidate_keys(semantic_field)

    values = [question.question]
    attribute_key = str(semantic_field.get("attribute_key") or "")
    if attribute_key in BUSINESS_ATTRIBUTE_ALIASES:
        values.append(attribute_key)
        values.extend(BUSINESS_ATTRIBUTE_ALIASES[attribute_key])

    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = normalize_key(value)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _provenance(
    field: dict[str, Any],
    bundle: ProductSourceBundle,
    *,
    evidence_keys: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    keys = list(evidence_keys) if evidence_keys is not None else _default_candidate_keys(field)
    candidates = bundle.candidates(keys)
    return [
        {
            "key": item.key,
            "value": list(item.value) if isinstance(item.value, tuple) else item.value,
            "source_type": item.source_type,
            "source_reference": item.source_reference,
            "priority": item.priority,
            "confidence": item.confidence,
            "evidence_text": item.evidence_text,
            "note": item.note,
        }
        for item in sorted(
            candidates,
            key=lambda value: (value.priority, -value.confidence, value.source_reference),
        )
    ]


def _confidence_gate(
    answer: ResolvedAnswer,
    policy: ResolutionPolicy,
) -> tuple[str, bool, str, str]:
    if answer.status != RESOLVED:
        return answer.status, False, answer.detail, f"resolver_{answer.status}"

    threshold = (
        policy.ai_auto_fill_min_confidence
        if answer.source_type == "ai_synthesis"
        else policy.auto_fill_min_confidence
    )
    if answer.confidence < threshold:
        return (
            NEEDS_REVIEW,
            False,
            f"证据置信度 {answer.confidence:.2f} 低于自动填写阈值 {threshold:.2f}。",
            GATE_LOW_CONFIDENCE,
        )
    if policy.require_source_reference and not (answer.source_reference or "").strip():
        return (
            NEEDS_REVIEW,
            False,
            "resolved 候选缺少可追溯 source_reference。",
            GATE_MISSING_SOURCE_REFERENCE,
        )
    return RESOLVED, True, answer.detail, ""


def _resolution_field(
    semantic_field: dict[str, Any],
    question: QuestionRecord | None,
) -> dict[str, Any]:
    """Expose the deterministic QA identity without trusting a reused DOM key."""

    if question is None:
        return semantic_field

    enriched = dict(semantic_field)
    enriched["label"] = question.question
    live_key = str(semantic_field.get("attribute_key") or "")
    if (
        live_key not in BUSINESS_ATTRIBUTE_ALIASES
        and normalize_key(live_key) != normalize_key(question.question)
    ):
        # Resolver-only copy: field constraints/section stay from the real live
        # field, but semantic scope follows the matched QA instead of a bogus DOM
        # key such as Length -> height.
        enriched["attribute_key"] = question.question
    return enriched


def _is_business_field(
    semantic_field: dict[str, Any],
    question: QuestionRecord | None,
) -> bool:
    names = [
        str(semantic_field.get("attribute_key") or ""),
        str(semantic_field.get("label") or ""),
    ]
    if question is not None:
        names.append(question.question)
    return any(is_business_question(name) for name in names if name)


def resolve_one(
    semantic_field: dict[str, Any],
    bundle: ProductSourceBundle,
    *,
    policy: ResolutionPolicy | None = None,
    fallback: Any | None = None,
    question: QuestionRecord | None = None,
) -> ResolutionRecord:
    policy = policy or ResolutionPolicy()
    lookup_field = _resolution_field(semantic_field, question)
    evidence_keys = _matched_evidence_keys(semantic_field, question)
    answer = resolve_field(
        lookup_field,
        bundle,
        fallback=fallback,
        evidence_keys=evidence_keys,
    )
    # Reports and browser plans must always expose the real live Makro identity.
    answer.attribute_key = str(semantic_field.get("attribute_key") or answer.attribute_key)
    answer.label = str(semantic_field.get("label") or answer.label)

    business_source_rejected = (
        _is_business_field(semantic_field, question)
        and answer.status == RESOLVED
        and answer.source_type not in BUSINESS_ALLOWED_SOURCE_TYPES
    )

    constraints_ok = not policy.validate_field_constraints
    if business_source_rejected:
        status = NEEDS_REVIEW
        eligible = False
        constraints_ok = False
        detail = (
            "经营字段只能来自 structured/business/config/rule；"
            f"当前来源 {answer.source_type or 'unknown'} 不允许驱动真实填写。"
        )
        gate_reason = GATE_BUSINESS_SOURCE
    elif answer.status == RESOLVED and policy.validate_field_constraints:
        validation = validate_resolved_answer(semantic_field, answer)
        constraints_ok = validation.valid
        if not validation.valid:
            status = NEEDS_REVIEW
            eligible = False
            detail = validation.detail
            gate_reason = GATE_FIELD_CONSTRAINT
        else:
            status, eligible, detail, gate_reason = _confidence_gate(answer, policy)
    else:
        status, eligible, detail, gate_reason = _confidence_gate(answer, policy)

    # Review preview is deliberately narrower than "has an answer". A candidate
    # is previewable only when the resolver produced a structurally valid
    # resolved value and the *sole* gate preventing autofill is confidence.
    preview_eligible = (
        status == NEEDS_REVIEW
        and gate_reason == GATE_LOW_CONFIDENCE
        and constraints_ok
        and bool(answer.answer_values)
        and (
            not policy.require_source_reference
            or bool((answer.source_reference or "").strip())
        )
    )

    return ResolutionRecord(
        attribute_key=answer.attribute_key,
        label=answer.label,
        status=status,
        answer=answer.answer,
        answer_values=list(answer.answer_values),
        qualifier=answer.qualifier,
        confidence=answer.confidence,
        source_type=answer.source_type,
        source_reference=answer.source_reference,
        evidence=answer.evidence,
        detail=detail,
        eligible_for_autofill=eligible,
        preview_eligible=preview_eligible,
        gate_reason=gate_reason,
        provenance=_provenance(lookup_field, bundle, evidence_keys=evidence_keys),
        question_number=(
            question.number if question else str(semantic_field.get("question_number") or "")
        ),
        question_explanation=(
            question.explanation
            if question
            else str(semantic_field.get("question_explanation") or "")
        ),
        question_category=(
            question.category
            if question
            else str(semantic_field.get("section_heading") or "")
        ),
        question_unit=(
            question.unit if question else str(semantic_field.get("question_unit") or "")
        ),
        question_options=(list(question.options) if question else []),
    )


def resolve_live_fields(
    semantic_fields: Iterable[dict[str, Any]],
    bundle: ProductSourceBundle,
    *,
    policy: ResolutionPolicy | None = None,
    fallback: Any | None = None,
) -> list[ResolutionRecord]:
    return [
        resolve_one(field, bundle, policy=policy, fallback=fallback)
        for field in semantic_fields
    ]


def resolve_catalog(
    catalog: QuestionCatalog,
    bundle: ProductSourceBundle,
    *,
    policy: ResolutionPolicy | None = None,
    fallback: Any | None = None,
) -> list[ResolutionRecord]:
    records: list[ResolutionRecord] = []
    for question in catalog.questions:
        records.append(
            resolve_one(
                question.as_semantic_field(),
                bundle,
                policy=policy,
                fallback=fallback,
                question=question,
            )
        )
    return records


def summarize_resolution(records: Iterable[ResolutionRecord]) -> dict[str, Any]:
    items = list(records)
    counts = {RESOLVED: 0, NEEDS_REVIEW: 0, MISSING: 0, CONFLICT: 0}
    eligible = 0
    preview_eligible = 0
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
        eligible += int(item.eligible_for_autofill)
        preview_eligible += int(item.preview_eligible)
    return {
        "total": len(items),
        "resolved": counts.get(RESOLVED, 0),
        "needs_review": counts.get(NEEDS_REVIEW, 0),
        "missing": counts.get(MISSING, 0),
        "conflict": counts.get(CONFLICT, 0),
        "eligible_for_autofill": eligible,
        "preview_eligible": preview_eligible,
        "blocked": len(items) - eligible,
    }
