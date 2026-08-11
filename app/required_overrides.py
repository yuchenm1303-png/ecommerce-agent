from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .ai_decisions import (
    READY as AI_READY,
    FieldDecision,
    field_id,
    field_options,
    field_qualifier_options,
)
from .fill_plan import (
    BLOCKED,
    READY,
    LiveFillPlan,
    LiveFillPlanItem,
    _apply_business_relations,
    _hard_guard_values,
)
from .resolution_types import RESOLVED


FALLBACK_TEXT_VALUE = "N/A"
FALLBACK_NUMERIC_VALUE = "1"
FALLBACK_SOURCE_REFERENCE = "system:required-placeholder"
_OPTION_PLACEHOLDERS = {
    "select",
    "select one",
    "choose",
    "choose one",
    "please select",
    "-- select --",
}
_NUMERIC_HINT = re.compile(
    r"(?:^|\b)(?:price|cost|qty|quantity|stock|weight|length|width|height|depth|volume|capacity|"
    r"size|moq|minimum order|warranty|power|voltage|current|frequency|diameter|thickness|"
    r"count|number of|pack size)(?:\b|$)|"
    r"(?:^|\s)(?:kg|g|mg|cm|mm|ml|l|m|w|v|hz|mah|wh|gb|mb|tb)(?:\s|$)",
    re.IGNORECASE,
)


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


def _usable_option(values: Iterable[str]) -> str:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    for value in cleaned:
        if value.casefold() not in _OPTION_PLACEHOLDERS:
            return value
    return cleaned[0] if cleaned else ""


def _looks_numeric(field: dict[str, Any]) -> bool:
    text = " | ".join(
        str(field.get(key) or "")
        for key in ("attribute_key", "label", "help_text", "context_text")
    )
    return bool(_NUMERIC_HINT.search(text))


def required_fallback_override(field: dict[str, Any]) -> dict[str, Any]:
    """Build one deterministic, non-AI fallback for a required Makro field.

    The normal Resolver gets first chance. This helper is used only for required
    fields that remain BLOCKED at real-execution time. It never calls a model or
    the network:
    - option/radio/select -> first usable live Makro option;
    - numeric/unit field -> ``1`` and the first usable live qualifier when present;
    - remaining free text -> ``N/A``.

    The production executor still rebinds the value to the current live field and
    runs the existing Makro option/unit hard guards before any browser write.
    """

    options = field_options(field)
    option = _usable_option(options)
    if option:
        return {
            "field_id": field_id(field),
            "values": [option],
            "source_type": "fallback",
            "reason": "deterministic first valid Makro option for unresolved required field",
        }

    qualifiers = field_qualifier_options(field)
    qualifier = _usable_option(qualifiers)
    if qualifier:
        return {
            "field_id": field_id(field),
            "values": [FALLBACK_NUMERIC_VALUE],
            "qualifier": qualifier,
            "source_type": "fallback",
            "reason": "deterministic numeric placeholder with first valid Makro qualifier",
        }

    value = FALLBACK_NUMERIC_VALUE if _looks_numeric(field) else FALLBACK_TEXT_VALUE
    return {
        "field_id": field_id(field),
        "values": [value],
        "source_type": "fallback",
        "reason": "deterministic placeholder for unresolved required field",
    }


def _source_metadata(override: dict[str, Any]) -> tuple[str, str, float, str]:
    source_type = str(override.get("source_type") or "user").strip().casefold()
    if source_type == "user":
        return (
            "user",
            "user:required-field-input",
            1.0,
            "Explicit value supplied by the user for an unresolved required Makro field.",
        )
    if source_type == "fallback":
        return (
            "fallback",
            FALLBACK_SOURCE_REFERENCE,
            0.0,
            "Deterministic non-AI placeholder used only because the required field remained unresolved.",
        )
    raise RequiredOverrideError(f"不支持 required override source_type={source_type!r}。")


def apply_required_overrides(
    plan: LiveFillPlan,
    semantic_fields: Iterable[dict[str, Any]],
    overrides: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Promote unresolved required fields using user or deterministic fallback values.

    No AI/search pass exists here. User-entered values remain optional and take
    precedence in the GUI; otherwise the GUI writes a deterministic fallback.
    Both paths bind only required BLOCKED fields and are revalidated against the
    current live Makro option/unit hard guards. Existing READY items are never
    replaced here.
    """

    fields = list(semantic_fields)
    fields_by_id = {field_id(field): field for field in fields}
    items_by_identity = {_item_identity(item): item for item in plan.items}
    applied: list[str] = []
    source_counts = {"user": 0, "fallback": 0}

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
                    "deterministic fallback for unresolved required field"
                    if source_type == "fallback"
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
            "deterministic required-field fallback"
            if source_type == "fallback"
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
            "未解决的 Makro 必填项已使用非 AI 的固定兜底值。"
            if source_type == "fallback"
            else "用户补充了 Resolver 未能确定的 Makro 必填值。"
        )
        applied.append(identifier)
        source_counts[source_type] += 1

    _apply_business_relations(plan.items)
    return {
        "applied": len(applied),
        "field_ids": applied,
        "sources": source_counts,
    }
