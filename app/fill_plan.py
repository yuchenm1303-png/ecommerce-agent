from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .answer_resolver import NEEDS_REVIEW
from .qa_catalog import QuestionCatalog, QuestionRecord
from .question_matcher import AMBIGUOUS, MATCHED, UNMATCHED, MatchAudit, match_questions_to_fields
from .resolution_engine import ResolutionPolicy, ResolutionRecord, resolve_one
from .source_bundle import ProductSourceBundle, normalize_key


READY = "ready"
BLOCKED = "blocked"
GATE_UNMATCHED_LIVE_FIELD = "unmatched_live_field"
GATE_CROSS_FIELD_RULE = "cross_field_business_rule"
GATE_SHARED_SYNTHESIS_BINDING = "ambiguous_shared_synthesis_binding"

UNMATCHED_LIVE_AUTOFILL_SOURCE_TYPES = {"structured", "business", "config", "rule"}


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
    def preview_eligible_count(self) -> int:
        return sum(1 for item in self.items if item.resolution.preview_eligible)

    @property
    def required_blocked_count(self) -> int:
        return sum(1 for item in self.items if item.required and item.action == BLOCKED)

    @property
    def required_ready_count(self) -> int:
        return sum(1 for item in self.items if item.required and item.action == READY)

    @property
    def required_preview_eligible_count(self) -> int:
        return sum(
            1 for item in self.items if item.required and item.resolution.preview_eligible
        )

    def summary(self) -> dict[str, Any]:
        return {
            "live_field_count": len(self.items),
            "ready": self.ready_count,
            "blocked": self.blocked_count,
            "preview_eligible": self.preview_eligible_count,
            "required_ready": self.required_ready_count,
            "required_blocked": self.required_blocked_count,
            "required_preview_eligible": self.required_preview_eligible_count,
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


def _decimal_answer(item: LiveFillPlanItem) -> Decimal | None:
    values = item.resolution.answer_values
    if not values:
        return None
    try:
        return Decimal(values[0].strip())
    except (InvalidOperation, AttributeError):
        return None


def _block_items(items: list[LiveFillPlanItem], keys: tuple[str, ...], detail: str) -> None:
    for item in items:
        if item.attribute_key not in keys:
            continue
        item.action = BLOCKED
        item.reason = detail
        item.resolution.status = NEEDS_REVIEW
        item.resolution.eligible_for_autofill = False
        item.resolution.preview_eligible = False
        item.resolution.gate_reason = GATE_CROSS_FIELD_RULE
        item.resolution.detail = detail


def _gate_unmatched_live_resolution(
    resolution: ResolutionRecord,
    *,
    question: QuestionRecord | None,
) -> str | None:
    if question is not None:
        return None
    if not resolution.eligible_for_autofill and not resolution.preview_eligible:
        return None

    source_type = str(resolution.source_type or "")
    if source_type in UNMATCHED_LIVE_AUTOFILL_SOURCE_TYPES:
        return None

    detail = (
        "实时 Makro 字段未匹配 QA，且候选证据来自 "
        f"source_type={source_type or 'unknown'}；为避免同名 attribute_key/label 串字段，"
        "未匹配 live field 只允许 structured/business/config/rule 明确输入自动填写或预览。"
    )
    resolution.status = NEEDS_REVIEW
    resolution.eligible_for_autofill = False
    resolution.preview_eligible = False
    resolution.gate_reason = GATE_UNMATCHED_LIVE_FIELD
    resolution.detail = detail
    return detail


def _apply_cross_field_business_rules(items: list[LiveFillPlanItem]) -> None:
    by_key = {item.attribute_key: item for item in items}

    mrp = by_key.get("mrp")
    selling = by_key.get("flipkart_selling_price")
    if mrp and selling and mrp.action == READY and selling.action == READY:
        mrp_value = _decimal_answer(mrp)
        selling_value = _decimal_answer(selling)
        if mrp_value is not None and selling_value is not None and selling_value > mrp_value:
            _block_items(
                items,
                ("mrp", "flipkart_selling_price"),
                f"价格关系无效：Selling Price={selling_value} 高于 Base Price/MRP={mrp_value}。",
            )

    minimum = by_key.get("minimum_order_quantity")
    maximum = by_key.get("max_order_quantity_allowed")
    if minimum and maximum and minimum.action == READY and maximum.action == READY:
        min_value = _decimal_answer(minimum)
        max_value = _decimal_answer(maximum)
        if min_value is not None and max_value is not None and min_value > max_value:
            _block_items(
                items,
                ("minimum_order_quantity", "max_order_quantity_allowed"),
                f"MOQ 关系无效：MinOQ={min_value} 高于 MaxOQ={max_value}。",
            )


def _synthesis_fingerprint(item: LiveFillPlanItem) -> tuple[str, str, tuple[str, ...]] | None:
    """Return the source/value fingerprint for a low-confidence AI binding.

    The same generic cited statement must not authorize several different QA/live
    fields merely because a model can map it to each one. This is the exact class
    of error seen when one generic ``120°`` specification was assigned to both
    Interior and Exterior Field of View.
    """

    record = item.resolution
    if not record.preview_eligible or record.source_type != "ai_synthesis":
        return None
    reference = str(record.source_reference or "").strip()
    evidence = normalize_key(record.evidence or "")
    values = tuple(normalize_key(value) for value in record.answer_values)
    if not reference or not evidence or not values:
        return None
    return reference, evidence, values


def _block_ambiguous_shared_synthesis(items: list[LiveFillPlanItem]) -> None:
    groups: dict[tuple[str, str, tuple[str, ...]], list[LiveFillPlanItem]] = {}
    for item in items:
        fingerprint = _synthesis_fingerprint(item)
        if fingerprint is not None:
            groups.setdefault(fingerprint, []).append(item)

    for group in groups.values():
        distinct_targets = {
            (
                normalize_key(item.question or item.label),
                normalize_key(item.section_heading),
                normalize_key(item.attribute_key),
            )
            for item in group
        }
        if len(distinct_targets) <= 1:
            continue
        labels = ", ".join(item.question or item.label or item.attribute_key for item in group)
        detail = (
            "同一条 ai_synthesis 证据和同一答案被绑定到多个不同字段："
            f"{labels}。字段归属不唯一，禁止进入 preview/persist；需更精确证据。"
        )
        for item in group:
            item.action = BLOCKED
            item.reason = detail
            item.resolution.status = NEEDS_REVIEW
            item.resolution.eligible_for_autofill = False
            item.resolution.preview_eligible = False
            item.resolution.gate_reason = GATE_SHARED_SYNTHESIS_BINDING
            item.resolution.detail = detail


def build_live_fill_plan(
    catalog: QuestionCatalog,
    semantic_fields: Iterable[dict[str, Any]],
    bundle: ProductSourceBundle,
    *,
    policy: ResolutionPolicy | None = None,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> LiveFillPlan:
    """Plan every live Makro field with fail-closed question/evidence binding."""

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
        unmatched_gate_detail = _gate_unmatched_live_resolution(
            resolution,
            question=question,
        )
        action = READY if resolution.eligible_for_autofill else BLOCKED
        if action == READY:
            reason = "证据、置信度和字段约束均通过，可进入浏览器填写层。"
        elif unmatched_gate_detail:
            reason = unmatched_gate_detail
        elif resolution.preview_eligible:
            reason = (
                "候选值通过字段结构、选项/单位和 provenance 检查，但仅因置信度门槛未达到自动填写标准；"
                "仅允许在显式人工 review 模式中使用。"
            )
        elif question is None:
            reason = "实时 Makro 字段未匹配 QA；仅显式结构化证据可授权自动填写。"
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

    _apply_cross_field_business_rules(items)
    _block_ambiguous_shared_synthesis(items)

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
