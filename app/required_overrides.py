from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .ai_decisions import READY as AI_READY, FieldDecision, field_id
from .fill_plan import (
    BLOCKED,
    READY,
    LiveFillPlan,
    LiveFillPlanItem,
    _apply_business_relations,
    _hard_guard_values,
)
from .resolution_types import RESOLVED


class RequiredOverrideError(ValueError):
    """Raised when a required-field completion value cannot be bound safely."""


def load_required_overrides(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = payload.get("overrides") if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise RequiredOverrideError("required overrides 必须是数组或包含 overrides 数组的 JSON。")
    return [item for item in raw if isinstance(item, dict)]


def _item_identity(item: LiveFillPlanItem) -> tuple[str, str, str]:
    return (item.attribute_key, item.label, item.section_heading)


def _field_identity(field: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(field.get("attribute_key") or ""),
        str(field.get("label") or field.get("attribute_key") or ""),
        str(field.get("section_heading") or ""),
    )


def _source_metadata(override: dict[str, Any]) -> tuple[str, str, float, str]:
    source_type = str(override.get("source_type") or "user").strip().casefold()
    if source_type not in {"user", "model"}:
        raise RequiredOverrideError(f"不支持 required override source_type={source_type!r}。")

    if source_type == "user":
        return (
            "user",
            "user:required-field-input",
            1.0,
            "Explicit value supplied by the user for an unresolved required Makro field.",
        )

    source_reference = str(
        override.get("source_reference") or "model-inference:required-completion"
    ).strip()
    if source_reference != "model-inference:required-completion":
        raise RequiredOverrideError("AI 必填补齐只能使用 required-completion provenance。")
    try:
        confidence = float(override.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6
    confidence = max(0.0, min(0.82, confidence))
    reason = str(override.get("reason") or "").strip()
    evidence = (
        "Targeted model completion for a required Makro field after the normal Resolver pass."
        + (f" Reason: {reason}" if reason else "")
    )
    return ("model", source_reference, confidence, evidence)


def apply_required_overrides(
    plan: LiveFillPlan,
    semantic_fields: Iterable[dict[str, Any]],
    overrides: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Promote only unresolved required fields using guarded completion values.

    Manual values remain the strongest fallback. The GUI may also persist values
    from its one targeted required-completion model pass. Both paths bind only
    required BLOCKED fields and are revalidated against the current live Makro
    option/unit hard guards. Existing READY items are never replaced here.
    """

    fields = list(semantic_fields)
    fields_by_id = {field_id(field): field for field in fields}
    items_by_identity = {_item_identity(item): item for item in plan.items}
    applied: list[str] = []
    source_counts = {"user": 0, "model": 0}

    for index, override in enumerate(overrides, start=1):
        identifier = str(override.get("field_id") or "").strip()
        if not identifier:
            raise RequiredOverrideError(f"override[{index}] 缺少 field_id。")
        live_field = fields_by_id.get(identifier)
        if live_field is None:
            raise RequiredOverrideError(f"override[{index}] field_id={identifier} 不属于当前 live schema。")

        item = items_by_identity.get(_field_identity(live_field))
        if item is None:
            raise RequiredOverrideError(f"override[{index}] 无法绑定到当前 Fill Plan：{identifier}")
        if not item.required:
            raise RequiredOverrideError(f"{item.label} 不是 required 字段；补充值只用于未解决必填项。")
        if item.action != BLOCKED:
            raise RequiredOverrideError(f"{item.label} 已经是 READY；拒绝用补充值覆盖现有决策。")

        raw_values = override.get("values")
        if raw_values is None:
            raw_values = [override.get("value")]
        if not isinstance(raw_values, list):
            raise RequiredOverrideError(f"{item.label} 的 values 必须是数组。")
        values = [str(value).strip() for value in raw_values if str(value or "").strip()]
        if not values:
            raise RequiredOverrideError(f"{item.label} 的补充值为空。")

        source_type, source_reference, confidence, evidence = _source_metadata(override)
        if source_type == "model":
            lowered = [value.casefold().strip() for value in values]
            if any(
                value.startswith("placeholder")
                or value in {"unknown", "n/a", "na", "tbd", "not applicable"}
                for value in lowered
            ):
                raise RequiredOverrideError(f"{item.label}: 拒绝 AI placeholder/unknown 必填值。")

        reason = str(override.get("reason") or "").strip()
        decision = FieldDecision(
            field_id=identifier,
            status=AI_READY,
            values=values,
            qualifier=str(override.get("qualifier") or "").strip(),
            confidence=confidence,
            reason=(
                reason
                or (
                    "targeted AI completion for unresolved required field"
                    if source_type == "model"
                    else "explicit user value for unresolved required field"
                )
            ),
        )
        canonical_values, qualifier, hard_error = _hard_guard_values(live_field, decision)
        if hard_error:
            raise RequiredOverrideError(f"{item.label}: {hard_error}")

        record = item.resolution
        record.status = RESOLVED
        record.answer = " + ".join(canonical_values)
        record.answer_values = canonical_values
        record.qualifier = qualifier or None
        record.confidence = confidence
        record.source_type = source_type
        record.source_reference = source_reference
        record.evidence = evidence
        record.detail = (
            "targeted AI required-field completion"
            if source_type == "model"
            else "explicit user input"
        )
        record.eligible_for_autofill = True
        record.preview_eligible = False
        record.gate_reason = ""
        record.provenance = [
            {
                "source_reference": source_reference,
                "evidence_text": evidence,
                "source_type": source_type,
                "confidence": confidence,
            }
        ]
        item.action = READY
        item.reason = (
            "AI 在真实填写前的必填补齐阶段给出了通过当前 Makro hard guard 的值。"
            if source_type == "model"
            else "用户补充了 Resolver 未能确定的 Makro 必填值。"
        )
        applied.append(identifier)
        source_counts[source_type] += 1

    # Completion values still obey the existing cross-field price/MOQ
    # relationships. This is a mechanical hard boundary, not another semantic
    # decision layer.
    _apply_business_relations(plan.items)
    return {
        "applied": len(applied),
        "field_ids": applied,
        "sources": source_counts,
    }
