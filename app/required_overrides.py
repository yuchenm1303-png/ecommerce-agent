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
    """Raised when an explicit user value cannot be bound safely."""


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


def apply_required_overrides(
    plan: LiveFillPlan,
    semantic_fields: Iterable[dict[str, Any]],
    overrides: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Promote only unresolved required fields using explicit user values.

    This is deliberately not another AI/resolver pass. The user supplies a value
    only after the normal resolver has failed to solve a required Makro field.
    The existing Makro option/unit hard guards still canonicalize the value, then
    the Fill Plan item becomes READY. READY items are never replaced here.
    """

    fields = list(semantic_fields)
    fields_by_id = {field_id(field): field for field in fields}
    items_by_identity = {_item_identity(item): item for item in plan.items}
    applied: list[str] = []

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
            raise RequiredOverrideError(f"{item.label} 不是 required 字段；用户补充只用于未解决必填项。")
        if item.action != BLOCKED:
            raise RequiredOverrideError(f"{item.label} 已经是 READY；拒绝用用户补充值覆盖现有决策。")

        raw_values = override.get("values")
        if raw_values is None:
            raw_values = [override.get("value")]
        if not isinstance(raw_values, list):
            raise RequiredOverrideError(f"{item.label} 的 values 必须是数组。")
        values = [str(value).strip() for value in raw_values if str(value or "").strip()]
        if not values:
            raise RequiredOverrideError(f"{item.label} 的用户补充值为空。")

        decision = FieldDecision(
            field_id=identifier,
            status=AI_READY,
            values=values,
            qualifier=str(override.get("qualifier") or "").strip(),
            confidence=1.0,
            reason="explicit user value for unresolved required field",
        )
        canonical_values, qualifier, hard_error = _hard_guard_values(live_field, decision)
        if hard_error:
            raise RequiredOverrideError(f"{item.label}: {hard_error}")

        record = item.resolution
        record.status = RESOLVED
        record.answer = " + ".join(canonical_values)
        record.answer_values = canonical_values
        record.qualifier = qualifier or None
        record.confidence = 1.0
        record.source_type = "user"
        record.source_reference = "user:required-field-input"
        record.evidence = "Explicit value supplied by the user for an unresolved required Makro field."
        record.detail = "explicit user input"
        record.eligible_for_autofill = True
        record.preview_eligible = False
        record.gate_reason = ""
        record.provenance = [
            {
                "source_reference": "user:required-field-input",
                "evidence_text": record.evidence,
                "source_type": "user",
                "confidence": 1.0,
            }
        ]
        item.action = READY
        item.reason = "用户补充了 Resolver 未能确定的 Makro 必填值。"
        applied.append(identifier)

    # User-entered business values still obey the already-existing cross-field
    # price/MOQ relationships. This is one existing hard boundary, not a second
    # semantic decision layer.
    _apply_business_relations(plan.items)
    return {"applied": len(applied), "field_ids": applied}
