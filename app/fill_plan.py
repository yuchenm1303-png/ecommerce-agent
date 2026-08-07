from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .qa_catalog import QuestionCatalog, QuestionRecord
from .question_matcher import AMBIGUOUS, MATCHED, UNMATCHED, MatchAudit, match_questions_to_fields
from .resolution_engine import ResolutionPolicy, ResolutionRecord, resolve_one
from .source_bundle import ProductSourceBundle


READY = "ready"
BLOCKED = "blocked"


@dataclass(slots=True)
class LiveFillPlanItem:
    attribute_key: str
    label: str
    section_heading: str
    required: bool
    action: str
    reason: str
    resolution: ResolutionRecord
    question_number: str = ""
    question: str = ""
    match_basis: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "attribute_key": self.attribute_key,
            "label": self.label,
            "section_heading": self.section_heading,
            "required": self.required,
            "action": self.action,
            "reason": self.reason,
            "question_number": self.question_number,
            "question": self.question,
            "match_basis": self.match_basis,
            "resolution": self.resolution.as_dict(),
        }


@dataclass(slots=True)
class LiveFillPlan:
    items: list[LiveFillPlanItem]
    match_audit: MatchAudit
    unmatched_questions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ready_count(self) -> int:
        return sum(1 for item in self.items if item.action == READY)

    @property
    def blocked_count(self) -> int:
        return sum(1 for item in self.items if item.action == BLOCKED)

    @property
    def required_blocked_count(self) -> int:
        return sum(
            1 for item in self.items if item.required and item.action == BLOCKED
        )

    @property
    def required_ready_count(self) -> int:
        return sum(
            1 for item in self.items if item.required and item.action == READY
        )

    def summary(self) -> dict[str, Any]:
        return {
            "live_field_count": len(self.items),
            "ready": self.ready_count,
            "blocked": self.blocked_count,
            "required_ready": self.required_ready_count,
            "required_blocked": self.required_blocked_count,
            "qa_matched": self.match_audit.matched_count,
            "qa_ambiguous": self.match_audit.ambiguous_count,
            "qa_unmatched": self.match_audit.unmatched_question_count,
            "unmatched_live_fields": len(self.match_audit.unmatched_fields),
            "safe_to_autofill_required_fields": self.required_blocked_count == 0,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "items": [item.as_dict() for item in self.items],
            "question_match_audit": self.match_audit.as_dict(),
            "unmatched_questions": self.unmatched_questions,
        }


def _matched_question_by_field_id(audit: MatchAudit) -> dict[int, tuple[QuestionRecord, str]]:
    output: dict[int, tuple[QuestionRecord, str]] = {}
    for match in audit.matches:
        if match.status != MATCHED or match.semantic_field is None:
            continue
        output[id(match.semantic_field)] = (match.question, match.match_basis)
    return output


def build_live_fill_plan(
    catalog: QuestionCatalog,
    semantic_fields: Iterable[dict[str, Any]],
    bundle: ProductSourceBundle,
    *,
    policy: ResolutionPolicy | None = None,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> LiveFillPlan:
    """Plan every live Makro field, not merely the questions in the QA sheet.

    QA matching contributes question metadata to product attributes. Live fields
    that are not in the customer QA sheet are still resolved directly from the
    evidence bundle; this is essential for seller/business fields such as SKU,
    price, MOQ, fulfilment and shipping configuration.
    """

    fields = list(semantic_fields)
    audit = match_questions_to_fields(catalog, fields, aliases=aliases)
    matched_by_field = _matched_question_by_field_id(audit)
    effective_policy = policy or ResolutionPolicy()
    items: list[LiveFillPlanItem] = []

    for field in fields:
        question_match = matched_by_field.get(id(field))
        question = question_match[0] if question_match else None
        match_basis = question_match[1] if question_match else ""
        resolution = resolve_one(
            field,
            bundle,
            policy=effective_policy,
            question=question,
        )
        action = READY if resolution.eligible_for_autofill else BLOCKED
        if action == READY:
            reason = "证据、置信度和字段约束均通过，可进入浏览器填写层。"
        elif question is None:
            reason = "实时 Makro 字段未匹配 QA；仅在显式结构化证据可解析时才允许自动填写。"
            if resolution.detail:
                reason += " " + resolution.detail
        else:
            reason = resolution.detail or "解析结果未通过自动填写安全门。"

        items.append(
            LiveFillPlanItem(
                attribute_key=str(field.get("attribute_key") or ""),
                label=str(field.get("label") or field.get("attribute_key") or ""),
                section_heading=str(field.get("section_heading") or ""),
                required=bool(field.get("required")),
                action=action,
                reason=reason,
                resolution=resolution,
                question_number=question.number if question else "",
                question=question.question if question else "",
                match_basis=match_basis,
            )
        )

    unmatched_questions = []
    for match in audit.matches:
        if match.status not in {UNMATCHED, AMBIGUOUS}:
            continue
        unmatched_questions.append(
            {
                "number": match.question.number,
                "question": match.question.question,
                "status": match.status,
                "detail": match.detail,
            }
        )

    return LiveFillPlan(
        items=items,
        match_audit=audit,
        unmatched_questions=unmatched_questions,
    )
