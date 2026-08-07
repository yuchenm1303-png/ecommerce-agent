from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .answer_resolver import (
    BUSINESS_ATTRIBUTE_ALIASES,
    CONFLICT,
    MISSING,
    NEEDS_REVIEW,
    RESOLVED,
    ResolvedAnswer,
    resolve_field,
)
from .fact_validators import validate_resolved_answer
from .qa_catalog import QuestionCatalog, QuestionRecord
from .source_bundle import ProductSourceBundle


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
            "provenance": self.provenance,
            "question_number": self.question_number,
            "question_explanation": self.question_explanation,
            "question_category": self.question_category,
            "question_unit": self.question_unit,
            "question_options": self.question_options,
        }


def _candidate_keys(field: dict[str, Any]) -> list[str]:
    key = str(field.get("attribute_key") or "")
    label = str(field.get("label") or "")
    values = [key, label]
    values.extend(BUSINESS_ATTRIBUTE_ALIASES.get(key, ()))
    return [item for item in values if item]


def _provenance(field: dict[str, Any], bundle: ProductSourceBundle) -> list[dict[str, Any]]:
    candidates = bundle.candidates(_candidate_keys(field))
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


def _confidence_gate(answer: ResolvedAnswer, policy: ResolutionPolicy) -> tuple[str, bool, str]:
    if answer.status != RESOLVED:
        return answer.status, False, answer.detail

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
        )
    if policy.require_source_reference and not (answer.source_reference or "").strip():
        return NEEDS_REVIEW, False, "resolved 候选缺少可追溯 source_reference。"
    return RESOLVED, True, answer.detail


def _resolution_field(
    semantic_field: dict[str, Any],
    question: QuestionRecord | None,
) -> dict[str, Any]:
    """Expose a deterministically matched QA label as an evidence lookup key.

    When the QA-to-Makro matcher used an explicit alias, evidence is naturally
    stored under the QA question label while the live control keeps its Makro
    label. Reusing the question as the temporary resolver label makes both the
    question label and the stable attribute_key candidates without fuzzy search.
    The real Makro label is restored on the returned ResolutionRecord.
    """

    if question is None or question.question == str(semantic_field.get("label") or ""):
        return semantic_field
    enriched = dict(semantic_field)
    enriched["label"] = question.question
    return enriched


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
    answer = resolve_field(lookup_field, bundle, fallback=fallback)
    # Reports and browser plans must always expose the real live Makro label.
    answer.label = str(semantic_field.get("label") or answer.label)
    status, eligible, detail = _confidence_gate(answer, policy)

    if status == RESOLVED and policy.validate_field_constraints:
        validation = validate_resolved_answer(semantic_field, answer)
        if not validation.valid:
            status = NEEDS_REVIEW
            eligible = False
            detail = validation.detail

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
        provenance=_provenance(lookup_field, bundle),
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
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
        eligible += int(item.eligible_for_autofill)
    return {
        "total": len(items),
        "resolved": counts.get(RESOLVED, 0),
        "needs_review": counts.get(NEEDS_REVIEW, 0),
        "missing": counts.get(MISSING, 0),
        "conflict": counts.get(CONFLICT, 0),
        "eligible_for_autofill": eligible,
        "blocked": len(items) - eligible,
    }
