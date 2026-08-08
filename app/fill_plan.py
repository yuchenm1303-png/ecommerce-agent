from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .ai_decisions import (
    BUSINESS_LOCKED,
    CONFLICT,
    MISSING,
    READY as AI_READY,
    REVIEW,
    AIDecisionPacket,
    FieldDecision,
    field_id,
    field_options,
    field_qualifier_options,
)
from .answer_resolver import CONFLICT as RESOLVER_CONFLICT
from .answer_resolver import MISSING as RESOLVER_MISSING
from .answer_resolver import NEEDS_REVIEW, RESOLVED, ResolvedAnswer
from .evidence_validation import is_business_question
from .fact_validators import validate_resolved_answer
from .resolution_engine import ResolutionPolicy, ResolutionRecord, resolve_one
from .source_bundle import ProductSourceBundle, normalize_key


READY = "ready"
BLOCKED = "blocked"
GATE_AI_REVIEW = "ai_review"
GATE_AI_CONFLICT = "ai_conflict"
GATE_AI_MISSING = "ai_missing"
GATE_BUSINESS_LOCKED = "business_locked"
GATE_HARD_FIELD_CONSTRAINT = "hard_field_constraint"
GATE_CROSS_FIELD_RULE = "cross_field_business_rule"


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
    match_basis: str = "ai-field-id"

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
    warnings: list[str] = field(default_factory=list)

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
        gate_counts: dict[str, int] = {}
        for item in self.items:
            key = item.resolution.gate_reason or "ready"
            gate_counts[key] = gate_counts.get(key, 0) + 1
        return {
            "live_field_count": len(self.items),
            "ready": self.ready_count,
            "blocked": self.blocked_count,
            "preview_eligible": self.preview_eligible_count,
            "required_ready": self.required_ready_count,
            "required_blocked": self.required_blocked_count,
            "required_preview_eligible": self.required_preview_eligible_count,
            "safe_to_autofill_required_fields": self.required_blocked_count == 0,
            "gate_counts": gate_counts,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "items": [item.as_dict() for item in self.items],
            "warnings": list(self.warnings),
        }


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


def _apply_cross_field_business_rules(items: list[LiveFillPlanItem]) -> None:
    """Keep only deterministic seller-operating invariants in local code."""

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


def _exact_option(value: str, options: list[str]) -> str | None:
    wanted = normalize_key(value)
    matches = [option for option in options if normalize_key(option) == wanted]
    return matches[0] if len(matches) == 1 else None


def _hard_guard_values(
    field: dict[str, Any],
    decision: FieldDecision,
) -> tuple[list[str], str, str | None]:
    """Validate only marketplace control shape, never product semantics."""

    values = list(decision.values)
    if not bool(field.get("multi_value")) and len(values) > 1:
        return values, decision.qualifier, "单值 Makro 字段收到多个 AI values。"

    options = field_options(field)
    if options:
        canonical: list[str] = []
        for value in values:
            matched = _exact_option(value, options)
            if matched is None:
                return values, decision.qualifier, (
                    f"AI value={value!r} 不等于当前 Makro 的唯一有效 option。"
                )
            canonical.append(matched)
        values = canonical

    qualifier = decision.qualifier.strip()
    qualifier_options = field_qualifier_options(field)
    if qualifier:
        if not qualifier_options:
            return values, qualifier, "AI 返回 qualifier，但当前 Makro 字段没有 qualifier 控件。"
        canonical_qualifier = _exact_option(qualifier, qualifier_options)
        if canonical_qualifier is None:
            return values, qualifier, (
                f"AI qualifier={qualifier!r} 不等于当前 Makro 的唯一有效单位。"
            )
        qualifier = canonical_qualifier

    if decision.status in {AI_READY, REVIEW} and not values:
        return values, qualifier, "AI 决策没有可执行 value。"
    return values, qualifier, None


def _citation_provenance(decision: FieldDecision) -> list[dict[str, Any]]:
    return [
        {
            "source_reference": citation.source_reference,
            "evidence_text": citation.evidence_text,
            "source_type": "grounded_source",
            "confidence": decision.confidence,
        }
        for citation in decision.citations
    ]


def _decision_record(
    field: dict[str, Any],
    decision: FieldDecision,
) -> ResolutionRecord:
    values, qualifier, hard_error = _hard_guard_values(field, decision)
    label = str(field.get("label") or field.get("attribute_key") or "")
    attribute_key = str(field.get("attribute_key") or "")
    first_reference = decision.citations[0].source_reference if decision.citations else None
    evidence = " | ".join(citation.evidence_text for citation in decision.citations)

    if decision.status == AI_READY:
        status = RESOLVED
        eligible = hard_error is None
        preview = False
        gate = "" if eligible else GATE_HARD_FIELD_CONSTRAINT
    elif decision.status == REVIEW:
        status = NEEDS_REVIEW
        eligible = False
        preview = hard_error is None and bool(values) and bool(decision.citations)
        gate = GATE_AI_REVIEW if hard_error is None else GATE_HARD_FIELD_CONSTRAINT
    elif decision.status == CONFLICT:
        status = RESOLVER_CONFLICT
        eligible = False
        preview = False
        gate = GATE_AI_CONFLICT
    else:
        status = RESOLVER_MISSING
        eligible = False
        preview = False
        gate = GATE_AI_MISSING

    detail = hard_error or decision.reason
    record = ResolutionRecord(
        attribute_key=attribute_key,
        label=label,
        status=status,
        answer=" + ".join(values) if values else None,
        answer_values=values,
        qualifier=qualifier or None,
        confidence=decision.confidence,
        source_type="ai_decision",
        source_reference=first_reference,
        evidence=evidence or None,
        detail=detail,
        eligible_for_autofill=eligible,
        preview_eligible=preview,
        gate_reason=gate,
        provenance=_citation_provenance(decision),
        question=label if False else None,
    )
    # ResolutionRecord has no `question` field; the dummy expression above is
    # intentionally avoided by constructing only declared fields below.
    return record


def _decision_record_safe(
    field: dict[str, Any],
    decision: FieldDecision,
) -> ResolutionRecord:
    """Construct the AI record and apply numeric/GTIN/maxlength hard guards."""

    values, qualifier, hard_error = _hard_guard_values(field, decision)
    label = str(field.get("label") or field.get("attribute_key") or "")
    attribute_key = str(field.get("attribute_key") or "")
    first_reference = decision.citations[0].source_reference if decision.citations else None
    evidence = " | ".join(citation.evidence_text for citation in decision.citations)

    if decision.status == AI_READY:
        status = RESOLVED
        eligible = hard_error is None
        preview = False
        gate = "" if eligible else GATE_HARD_FIELD_CONSTRAINT
    elif decision.status == REVIEW:
        status = NEEDS_REVIEW
        eligible = False
        preview = hard_error is None and bool(values) and bool(decision.citations)
        gate = GATE_AI_REVIEW if hard_error is None else GATE_HARD_FIELD_CONSTRAINT
    elif decision.status == CONFLICT:
        status = RESOLVER_CONFLICT
        eligible = False
        preview = False
        gate = GATE_AI_CONFLICT
    else:
        status = RESOLVER_MISSING
        eligible = False
        preview = False
        gate = GATE_AI_MISSING

    record = ResolutionRecord(
        attribute_key=attribute_key,
        label=label,
        status=status,
        answer=" + ".join(values) if values else None,
        answer_values=values,
        qualifier=qualifier or None,
        confidence=decision.confidence,
        source_type="ai_decision",
        source_reference=first_reference,
        evidence=evidence or None,
        detail=hard_error or decision.reason,
        eligible_for_autofill=eligible,
        preview_eligible=preview,
        gate_reason=gate,
        provenance=_citation_provenance(decision),
        question_number="",
        question_explanation="",
        question_category=str(field.get("section_heading") or ""),
        question_unit=" | ".join(field_qualifier_options(field)),
        question_options=field_options(field),
    )

    if record.status == RESOLVED and record.eligible_for_autofill:
        execution_answer = ResolvedAnswer(
            attribute_key=record.attribute_key,
            label=record.label,
            status=RESOLVED,
            answer=record.answer,
            answer_values=list(record.answer_values),
            qualifier=record.qualifier,
            source_type=record.source_type,
            source_reference=record.source_reference,
            evidence=record.evidence,
            confidence=record.confidence,
            detail=record.detail,
        )
        validation = validate_resolved_answer(field, execution_answer)
        if not validation.valid:
            record.status = NEEDS_REVIEW
            record.eligible_for_autofill = False
            record.preview_eligible = False
            record.gate_reason = GATE_HARD_FIELD_CONSTRAINT
            record.detail = validation.detail
    return record


def _is_business_field(field: dict[str, Any]) -> bool:
    return is_business_question(str(field.get("attribute_key") or "")) or is_business_question(
        str(field.get("label") or "")
    )


def _business_record(
    field: dict[str, Any],
    business_bundle: ProductSourceBundle,
) -> ResolutionRecord:
    """Resolve seller-operated fields only from explicit deterministic inputs."""

    return resolve_one(
        field,
        business_bundle,
        policy=ResolutionPolicy(),
        question=None,
    )


def build_live_fill_plan(
    decision_packet: AIDecisionPacket,
    semantic_fields: Iterable[dict[str, Any]],
    business_bundle: ProductSourceBundle,
) -> LiveFillPlan:
    """Build the executable plan from AI decisions plus thin hard guards.

    Product semantics are not re-interpreted here. AI owns product understanding;
    local code owns only seller-operated fields, live control shape and deterministic
    cross-field operating invariants.
    """

    fields = list(semantic_fields)
    decisions = {decision.field_id: decision for decision in decision_packet.decisions}
    items: list[LiveFillPlanItem] = []
    warnings = list(decision_packet.warnings)

    for field in fields:
        identifier = field_id(field)
        label = str(field.get("label") or field.get("attribute_key") or "")
        attribute_key = str(field.get("attribute_key") or "")
        section = str(field.get("section_heading") or "")
        required = bool(field.get("required"))

        if _is_business_field(field):
            resolution = _business_record(field, business_bundle)
            action = READY if resolution.eligible_for_autofill else BLOCKED
            if action == READY:
                reason = "显式 seller/business 数据通过硬约束。"
            else:
                resolution.preview_eligible = False
                resolution.gate_reason = GATE_BUSINESS_LOCKED
                reason = (
                    "经营字段禁止 AI 推断；需要 structured/business/config/rule 明确数据。 "
                    + (resolution.detail or "")
                ).strip()
        else:
            decision = decisions.get(identifier)
            if decision is None:
                decision = FieldDecision(
                    field_id=identifier,
                    status=MISSING,
                    reason="decision packet 缺少该 live field",
                )
                warnings.append(f"missing decision for field_id={identifier}")
            resolution = _decision_record_safe(field, decision)
            action = READY if resolution.eligible_for_autofill else BLOCKED
            if action == READY:
                reason = "AI 字段决策、grounded citations 与 Makro 硬约束均通过。"
            elif resolution.preview_eligible:
                reason = "AI 给出可执行候选，但自身判断为 REVIEW；只允许显式人工 review。"
            else:
                reason = resolution.detail or "AI 决策未通过执行硬约束。"

        items.append(
            LiveFillPlanItem(
                attribute_key=attribute_key,
                label=label,
                section_heading=section,
                required=required,
                action=action,
                reason=reason,
                resolution=resolution,
                question=label,
                match_basis="ai-field-id" if not _is_business_field(field) else "business-explicit",
            )
        )

    _apply_cross_field_business_rules(items)
    return LiveFillPlan(items=items, warnings=warnings)
