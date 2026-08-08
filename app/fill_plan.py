from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .ai_decisions import (
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
from .business_fields import (
    BUSINESS_ALLOWED_SOURCE_TYPES,
    BUSINESS_ATTRIBUTE_ALIASES,
    is_business_question,
)
from .fact_validators import validate_resolved_answer
from .resolution_types import (
    CONFLICT as RESOLVER_CONFLICT,
    MISSING as RESOLVER_MISSING,
    NEEDS_REVIEW,
    RESOLVED,
    ResolvedAnswer,
    ResolutionRecord,
)
from .source_bundle import ProductSourceBundle, SourceEvidence, normalize_key


READY = "ready"
BLOCKED = "blocked"
GATE_AI_REVIEW = "ai_review"
GATE_AI_CONFLICT = "ai_conflict"
GATE_AI_MISSING = "ai_missing"
GATE_BUSINESS_LOCKED = "business_locked"
GATE_HARD_FIELD_CONSTRAINT = "hard_field_constraint"
GATE_CROSS_FIELD_RULE = "cross_field_business_rule"
GATE_BUSINESS_CONFLICT = "business_conflict"


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
        return sum(item.action == READY for item in self.items)

    @property
    def blocked_count(self) -> int:
        return sum(item.action == BLOCKED for item in self.items)

    @property
    def preview_eligible_count(self) -> int:
        return sum(item.resolution.preview_eligible for item in self.items)

    @property
    def required_blocked_count(self) -> int:
        return sum(item.required and item.action == BLOCKED for item in self.items)

    @property
    def required_ready_count(self) -> int:
        return sum(item.required and item.action == READY for item in self.items)

    @property
    def required_preview_eligible_count(self) -> int:
        return sum(
            item.required and item.resolution.preview_eligible for item in self.items
        )

    def summary(self) -> dict[str, Any]:
        gate_counts: dict[str, int] = {}
        for item in self.items:
            gate = item.resolution.gate_reason or "ready"
            gate_counts[gate] = gate_counts.get(gate, 0) + 1
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
    if not item.resolution.answer_values:
        return None
    try:
        return Decimal(item.resolution.answer_values[0].strip())
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
    """Apply only deterministic seller-operating invariants."""

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
    """Validate marketplace control shape without interpreting product semantics."""

    values = list(decision.values)
    if not bool(field.get("multi_value")) and len(values) > 1:
        return values, decision.qualifier, "单值 Makro 字段收到多个 values。"

    options = field_options(field)
    if options:
        canonical: list[str] = []
        for value in values:
            matched = _exact_option(value, options)
            if matched is None:
                return values, decision.qualifier, (
                    f"value={value!r} 不等于当前 Makro 的唯一有效 option。"
                )
            canonical.append(matched)
        values = canonical

    qualifier = decision.qualifier.strip()
    qualifier_options = field_qualifier_options(field)
    if qualifier:
        if not qualifier_options:
            return values, qualifier, "返回了 qualifier，但当前 Makro 字段没有 qualifier 控件。"
        matched = _exact_option(qualifier, qualifier_options)
        if matched is None:
            return values, qualifier, (
                f"qualifier={qualifier!r} 不等于当前 Makro 的唯一有效单位。"
            )
        qualifier = matched

    if decision.status in {AI_READY, REVIEW} and not values:
        return values, qualifier, "决策没有可执行 value。"
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


def _apply_hard_field_validation(field: dict[str, Any], record: ResolutionRecord) -> None:
    if record.status != RESOLVED or not record.eligible_for_autofill:
        return
    validation = validate_resolved_answer(
        field,
        ResolvedAnswer(
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
        ),
    )
    if validation.valid:
        return
    record.status = NEEDS_REVIEW
    record.eligible_for_autofill = False
    record.preview_eligible = False
    record.gate_reason = GATE_HARD_FIELD_CONSTRAINT
    record.detail = validation.detail


def _decision_record(field: dict[str, Any], decision: FieldDecision) -> ResolutionRecord:
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
        question_category=str(field.get("section_heading") or ""),
        question_unit=" | ".join(field_qualifier_options(field)),
        question_options=field_options(field),
    )
    _apply_hard_field_validation(field, record)
    return record


def _is_business_field(field: dict[str, Any]) -> bool:
    return is_business_question(str(field.get("attribute_key") or "")) or is_business_question(
        str(field.get("label") or "")
    )


def _raw_business_values(candidate: SourceEvidence) -> list[str]:
    if isinstance(candidate.value, tuple):
        return [str(value).strip() for value in candidate.value if str(value).strip()]
    value = str(candidate.value).strip()
    return [value] if value else []


def _record_base(field: dict[str, Any]) -> dict[str, Any]:
    return {
        "attribute_key": str(field.get("attribute_key") or ""),
        "label": str(field.get("label") or field.get("attribute_key") or ""),
        "question_category": str(field.get("section_heading") or ""),
        "question_unit": " | ".join(field_qualifier_options(field)),
        "question_options": field_options(field),
    }


def _business_record(
    field: dict[str, Any],
    business_bundle: ProductSourceBundle,
) -> ResolutionRecord:
    """Resolve seller-operated fields only from explicit trusted seller data."""

    base = _record_base(field)
    attribute_key = base["attribute_key"]
    label = base["label"]
    keys = [attribute_key, label, *BUSINESS_ATTRIBUTE_ALIASES.get(attribute_key, ())]
    candidates = [
        candidate
        for candidate in business_bundle.candidates(keys)
        if candidate.source_type in BUSINESS_ALLOWED_SOURCE_TYPES
    ]

    if not candidates:
        return ResolutionRecord(
            **base,
            status=RESOLVER_MISSING,
            answer=None,
            answer_values=[],
            qualifier=None,
            confidence=0.0,
            source_type=None,
            source_reference=None,
            evidence=None,
            detail="经营字段没有 structured/business/config/rule 明确输入。",
            eligible_for_autofill=False,
            preview_eligible=False,
            gate_reason=GATE_BUSINESS_LOCKED,
        )

    grouped: dict[tuple[str, ...], list[SourceEvidence]] = {}
    for candidate in candidates:
        values = _raw_business_values(candidate)
        fingerprint = tuple(normalize_key(value) for value in values)
        if fingerprint:
            grouped.setdefault(fingerprint, []).append(candidate)

    if not grouped:
        return ResolutionRecord(
            **base,
            status=RESOLVER_MISSING,
            answer=None,
            answer_values=[],
            qualifier=None,
            confidence=0.0,
            source_type=None,
            source_reference=None,
            evidence=None,
            detail="经营字段显式输入为空。",
            eligible_for_autofill=False,
            preview_eligible=False,
            gate_reason=GATE_BUSINESS_LOCKED,
        )

    if len(grouped) > 1:
        return ResolutionRecord(
            **base,
            status=RESOLVER_CONFLICT,
            answer=None,
            answer_values=[],
            qualifier=None,
            confidence=max(candidate.confidence for candidate in candidates),
            source_type=None,
            source_reference=None,
            evidence=None,
            detail="多个显式 seller/business 输入互相冲突；禁止自动选择。",
            eligible_for_autofill=False,
            preview_eligible=False,
            gate_reason=GATE_BUSINESS_CONFLICT,
            provenance=[
                {
                    "key": candidate.key,
                    "value": _raw_business_values(candidate),
                    "source_type": candidate.source_type,
                    "source_reference": candidate.source_reference,
                    "confidence": candidate.confidence,
                    "evidence_text": candidate.evidence_text,
                }
                for candidate in candidates
            ],
        )

    agreeing = next(iter(grouped.values()))
    selected = sorted(
        agreeing,
        key=lambda candidate: (
            candidate.priority,
            -candidate.confidence,
            candidate.source_reference,
        ),
    )[0]
    structural = FieldDecision(
        field_id=field_id(field),
        status=AI_READY,
        values=_raw_business_values(selected),
        confidence=selected.confidence,
    )
    values, qualifier, hard_error = _hard_guard_values(field, structural)
    record = ResolutionRecord(
        **base,
        status=RESOLVED if hard_error is None else NEEDS_REVIEW,
        answer=" + ".join(values) if values else None,
        answer_values=values,
        qualifier=qualifier or None,
        confidence=selected.confidence,
        source_type=selected.source_type,
        source_reference=selected.source_reference,
        evidence=selected.evidence_text or None,
        detail=hard_error or "explicit seller/business input",
        eligible_for_autofill=hard_error is None,
        preview_eligible=False,
        gate_reason="" if hard_error is None else GATE_HARD_FIELD_CONSTRAINT,
        provenance=[
            {
                "key": candidate.key,
                "value": _raw_business_values(candidate),
                "source_type": candidate.source_type,
                "source_reference": candidate.source_reference,
                "confidence": candidate.confidence,
                "evidence_text": candidate.evidence_text,
            }
            for candidate in agreeing
        ],
    )
    _apply_hard_field_validation(field, record)
    return record


def build_live_fill_plan(
    decision_packet: AIDecisionPacket,
    semantic_fields: Iterable[dict[str, Any]],
    business_bundle: ProductSourceBundle,
) -> LiveFillPlan:
    """Convert AI field decisions into executable browser work.

    AI owns product semantics. Local code only protects seller-operated fields,
    validates live-control shape, and enforces deterministic operating invariants.
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
        business_field = _is_business_field(field)

        if business_field:
            resolution = _business_record(field, business_bundle)
            action = READY if resolution.eligible_for_autofill else BLOCKED
            reason = (
                "显式 seller/business 数据通过硬约束。"
                if action == READY
                else resolution.detail
            )
        else:
            decision = decisions.get(identifier)
            if decision is None:
                decision = FieldDecision(
                    field_id=identifier,
                    status=MISSING,
                    reason="decision packet 缺少该 live field",
                )
                warnings.append(f"missing decision for field_id={identifier}")
            resolution = _decision_record(field, decision)
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
                match_basis="business-explicit" if business_field else "ai-field-id",
            )
        )

    _apply_cross_field_business_rules(items)
    return LiveFillPlan(items=items, warnings=warnings)
