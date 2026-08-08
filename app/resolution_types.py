from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


RESOLVED = "resolved"
NEEDS_REVIEW = "needs_review"
MISSING = "missing"
CONFLICT = "conflict"


@dataclass(slots=True)
class ResolvedAnswer:
    """Browser-facing executable answer shape with no product semantics."""

    attribute_key: str
    label: str
    status: str
    answer: str | None = None
    answer_values: list[str] = field(default_factory=list)
    qualifier: str | None = None
    source_type: str | None = None
    source_reference: str | None = None
    evidence: str | None = None
    confidence: float = 0.0
    option_match: list[dict[str, str]] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "attribute_key": self.attribute_key,
            "label": self.label,
            "status": self.status,
            "answer": self.answer,
            "answer_values": list(self.answer_values),
            "qualifier": self.qualifier,
            "source_type": self.source_type,
            "source_reference": self.source_reference,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "option_match": list(self.option_match),
            "detail": self.detail,
        }


@dataclass(slots=True)
class ResolutionRecord:
    """Planner/report view of one live field decision."""

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
            "answer_values": list(self.answer_values),
            "qualifier": self.qualifier,
            "confidence": self.confidence,
            "source_type": self.source_type,
            "source_reference": self.source_reference,
            "evidence": self.evidence,
            "detail": self.detail,
            "eligible_for_autofill": self.eligible_for_autofill,
            "preview_eligible": self.preview_eligible,
            "gate_reason": self.gate_reason,
            "provenance": list(self.provenance),
            "question_number": self.question_number,
            "question_explanation": self.question_explanation,
            "question_category": self.question_category,
            "question_unit": self.question_unit,
            "question_options": list(self.question_options),
        }
