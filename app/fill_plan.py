from __future__ import annotations

import re
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
from .hard_field_validators import is_numeric_semantic_field, validate_resolved_answer
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

    @property
    def question_number(self) -> str:
        return ""

    @property
    def question(self) -> str:
        return self.label

    @property
    def match_basis(self) -> str:
        return "ai-field-id"

    def as_dict(self) -> dict[str, Any]:
        return {
            "attribute_key": self.attribute_key,
            "label": self.label,
            "section_heading": self.section_heading,
            "required": self.required,
            "action": self.action,
            "reason": self.reason,
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
        return sum(item.required and item.resolution.preview_eligible for item in self.items)

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


def _exact_option(value: str, options: list[str]) -> str | None:
    wanted = normalize_key(value)
    matches = [option for option in options if normalize_key(option) == wanted]
    return matches[0] if len(matches) == 1 else None


def _unit_context_texts(live_field: dict[str, Any]) -> list[str]:
    """Return only field-local wording suitable for deterministic unit matching.

    The broad context captured around a Makro attribute can include sibling
    attributes and their units. Using that entire block as unit identity caused a
    Length field to see a neighbouring mass unit. Prefer direct field/control
    metadata and accept compact control context only when it is genuinely local.
    """

    output: list[str] = []
    seen: set[str] = set()

    def add(value: object, *, compact_only: bool = False) -> None:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text or (compact_only and len(text) > 80):
            return
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            output.append(text)

    add(live_field.get("help_text"))
    add(live_field.get("context_text"), compact_only=True)
    for control in live_field.get("controls") or []:
        if not isinstance(control, dict):
            continue
        for key in ("help_text", "placeholder", "aria_label"):
            add(control.get(key))
        add(control.get("context_text"), compact_only=True)
    return output


def _fixed_qualifier_rendered(live_field: dict[str, Any], qualifier: str) -> bool:
    """Return True only when the exact unit token is visibly local to this field."""

    token = qualifier.strip()
    if not token:
        return False
    pattern = re.compile(
        rf"(?<![0-9A-Za-z_]){re.escape(token)}(?![0-9A-Za-z_])",
        re.IGNORECASE,
    )
    return any(pattern.search(text) for text in _unit_context_texts(live_field))


_UNIT_SCALE: dict[str, tuple[str, Decimal]] = {
    "mg": ("mass", Decimal("0.001")),
    "g": ("mass", Decimal("1")),
    "kg": ("mass", Decimal("1000")),
    "oz": ("mass", Decimal("28.349523125")),
    "lbs": ("mass", Decimal("453.59237")),
    "mm": ("length", Decimal("1")),
    "cm": ("length", Decimal("10")),
    "m": ("length", Decimal("1000")),
    "inch": ("length", Decimal("25.4")),
}


def _fixed_rendered_unit(live_field: dict[str, Any]) -> str:
    matches = [unit for unit in _UNIT_SCALE if _fixed_qualifier_rendered(live_field, unit)]
    return matches[0] if len(matches) == 1 else ""


def _convert_unit_values(values: list[str], source: str, target: str) -> list[str] | None:
    source_kind, source_scale = _UNIT_SCALE[source]
    target_kind, target_scale = _UNIT_SCALE[target]
    if source_kind != target_kind:
        return None
    output: list[str] = []
    for value in values:
        try:
            converted = Decimal(value.strip()) * source_scale / target_scale
        except (InvalidOperation, ValueError):
            return None
        rendered = format(converted.normalize(), "f")
        output.append(rendered.rstrip("0").rstrip(".") if "." in rendered else rendered)
    return output


def _inline_qualifier_values(values: list[str], qualifier: str) -> list[str]:
    """Losslessly serialize value+unit into one free-text Makro control.

    When Makro exposes no separate qualifier selector and the primary control is
    ordinary text, keeping the unit in a detached ``qualifier`` is an artificial
    representation mismatch. Fold it into the value instead; numeric controls
    never use this path.
    """

    rendered_qualifier = re.sub(r"[_\s]+", " ", qualifier.strip()).strip()
    if not rendered_qualifier:
        return values
    suffix_key = re.sub(r"[^0-9a-z]+", "", rendered_qualifier.casefold())
    output: list[str] = []
    for raw in values:
        value = str(raw).strip()
        value_key = re.sub(r"[^0-9a-z]+", "", value.casefold())
        if suffix_key and value_key.endswith(suffix_key):
            output.append(value)
        else:
            output.append(f"{value} {rendered_qualifier}".strip())
    return output


def _hard_guard_values(
    live_field: dict[str, Any],
    decision: FieldDecision,
) -> tuple[list[str], str, str | None]:
    values = list(decision.values)
    if not bool(live_field.get("multi_value")) and len(values) > 1:
        return values, decision.qualifier, "单值 Makro 字段收到多个 values。"

    options = field_options(live_field)
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
    qualifiers = field_qualifier_options(live_field)
    if qualifier:
        if not qualifiers:
            # Free-text Makro controls can faithfully carry the approved unit in
            # the same box. Do this before any fixed-unit inference so nearby UI
            # wording cannot spuriously turn a valid text answer into a conflict.
            if not options and not is_numeric_semantic_field(live_field):
                values = _inline_qualifier_values(values, qualifier)
                qualifier = ""
            elif _fixed_qualifier_rendered(live_field, qualifier):
                qualifier = ""
            else:
                source_unit = qualifier.casefold()
                target_unit = _fixed_rendered_unit(live_field)
                if source_unit in _UNIT_SCALE and target_unit:
                    converted = _convert_unit_values(values, source_unit, target_unit)
                    if converted is not None:
                        values = converted
                        qualifier = ""
                    else:
                        return values, qualifier, "qualifier 与 Makro 固定单位不兼容。"
                else:
                    return values, qualifier, "返回了 qualifier，但当前 Makro 字段既没有 qualifier 控件，也没有显示相同的固定单位。"
        else:
            matched = _exact_option(qualifier, qualifiers)
            if matched is None:
                return values, qualifier, (
                    f"qualifier={qualifier!r} 不等于当前 Makro 的唯一有效单位。"
                )
            qualifier = matched

    if decision.status in {AI_READY, REVIEW} and not values:
        return values, qualifier, "决策没有可执行 value。"
    return values, qualifier, None


def _provenance(decision: FieldDecision) -> list[dict[str, Any]]:
    return [
        {
            "source_reference": citation.source_reference,
            "evidence_text": citation.evidence_text,
            "source_type": "grounded_source",
            "confidence": decision.confidence,
        }
        for citation in decision.citations
    ]


def _record_base(live_field: dict[str, Any]) -> dict[str, Any]:
    return {
        "attribute_key": str(live_field.get("attribute_key") or ""),
        "label": str(live_field.get("label") or live_field.get("attribute_key") or ""),
        "question_category": str(live_field.get("section_heading") or ""),
        "question_unit": " | ".join(field_qualifier_options(live_field)),
        "question_options": field_options(live_field),
    }


def _apply_hard_field_validation(live_field: dict[str, Any], record: ResolutionRecord) -> None:
    if record.status != RESOLVED or not record.eligible_for_autofill:
        return
    result = validate_resolved_answer(
        live_field,
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
    if result.valid:
        return
    record.status = NEEDS_REVIEW
    record.eligible_for_autofill = False
    record.preview_eligible = False
    record.gate_reason = GATE_HARD_FIELD_CONSTRAINT
    record.detail = result.detail


def _decision_record(live_field: dict[str, Any], decision: FieldDecision) -> ResolutionRecord:
    values, qualifier, hard_error = _hard_guard_values(live_field, decision)
    base = _record_base(live_field)
    source_reference = decision.citations[0].source_reference if decision.citations else None
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
        **base,
        status=status,
        answer=" + ".join(values) if values else None,
        answer_values=values,
        qualifier=qualifier or None,
        confidence=decision.confidence,
        source_type="ai_decision",
        source_reference=source_reference,
        evidence=evidence or None,
        detail=hard_error or decision.reason,
        eligible_for_autofill=eligible,
        preview_eligible=preview,
        gate_reason=gate,
        provenance=_provenance(decision),
    )
    _apply_hard_field_validation(live_field, record)
    return record


def _is_business_field(live_field: dict[str, Any]) -> bool:
    return is_business_question(str(live_field.get("attribute_key") or "")) or is_business_question(
        str(live_field.get("label") or "")
    )


def _business_values(candidate: SourceEvidence) -> list[str]:
    if isinstance(candidate.value, tuple):
        return [str(value).strip() for value in candidate.value if str(value).strip()]
    value = str(candidate.value).strip()
    return [value] if value else []


def _business_record(
    live_field: dict[str, Any],
    business_bundle: ProductSourceBundle,
) -> ResolutionRecord:
    base = _record_base(live_field)
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
        values = _business_values(candidate)
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
                    "value": _business_values(candidate),
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
        field_id=field_id(live_field),
        status=AI_READY,
        values=_business_values(selected),
        confidence=selected.confidence,
    )
    values, qualifier, hard_error = _hard_guard_values(live_field, structural)
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
                "value": _business_values(candidate),
                "source_type": candidate.source_type,
                "source_reference": candidate.source_reference,
                "confidence": candidate.confidence,
                "evidence_text": candidate.evidence_text,
            }
            for candidate in agreeing
        ],
    )
    _apply_hard_field_validation(live_field, record)
    return record


def _decimal_answer(item: LiveFillPlanItem) -> Decimal | None:
    if not item.resolution.answer_values:
        return None
    try:
        return Decimal(item.resolution.answer_values[0].strip())
    except (InvalidOperation, AttributeError):
        return None


def _block(items: list[LiveFillPlanItem], keys: tuple[str, ...], detail: str) -> None:
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


def _apply_business_relations(items: list[LiveFillPlanItem]) -> None:
    by_key = {item.attribute_key: item for item in items}
    mrp = by_key.get("mrp")
    selling = by_key.get("flipkart_selling_price")
    if mrp and selling and mrp.action == READY and selling.action == READY:
        mrp_value, selling_value = _decimal_answer(mrp), _decimal_answer(selling)
        if mrp_value is not None and selling_value is not None and selling_value > mrp_value:
            _block(
                items,
                ("mrp", "flipkart_selling_price"),
                f"价格关系无效：Selling Price={selling_value} 高于 Base Price/MRP={mrp_value}。",
            )

    minimum = by_key.get("minimum_order_quantity")
    maximum = by_key.get("max_order_quantity_allowed")
    if minimum and maximum and minimum.action == READY and maximum.action == READY:
        min_value, max_value = _decimal_answer(minimum), _decimal_answer(maximum)
        if min_value is not None and max_value is not None and min_value > max_value:
            _block(
                items,
                ("minimum_order_quantity", "max_order_quantity_allowed"),
                f"MOQ 关系无效：MinOQ={min_value} 高于 MaxOQ={max_value}。",
            )


def build_live_fill_plan(
    decision_packet: AIDecisionPacket,
    semantic_fields: Iterable[dict[str, Any]],
    business_bundle: ProductSourceBundle,
) -> LiveFillPlan:
    """Turn AI decisions into browser work without locally re-solving product meaning."""

    fields = list(semantic_fields)
    decisions = {decision.field_id: decision for decision in decision_packet.decisions}
    warnings = list(decision_packet.warnings)
    items: list[LiveFillPlanItem] = []

    for live_field in fields:
        identifier = field_id(live_field)
        label = str(live_field.get("label") or live_field.get("attribute_key") or "")
        attribute_key = str(live_field.get("attribute_key") or "")
        section = str(live_field.get("section_heading") or "")
        required = bool(live_field.get("required"))

        if _is_business_field(live_field):
            resolution = _business_record(live_field, business_bundle)
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
            resolution = _decision_record(live_field, decision)
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
            )
        )

    _apply_business_relations(items)
    return LiveFillPlan(items=items, warnings=warnings)
