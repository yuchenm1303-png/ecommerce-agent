from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .ai_decisions import field_id
from .fill_plan import BLOCKED, READY, LiveFillPlan, LiveFillPlanItem, _apply_business_relations
from .hard_field_validators import (
    executable_qualifier_options,
    executable_value_options,
    is_numeric_semantic_field,
    is_selection_semantic_field,
    validate_resolved_answer,
)
from .listing_content_policy import allow_required_fallback
from .live_schema import load_live_schema, schema_field_signature
from .resolution_types import RESOLVED, ResolvedAnswer


FALLBACK_TEXT_VALUE = "N/A"
FALLBACK_NUMERIC_VALUE = "1"
FALLBACK_SOURCE_REFERENCE = "system:required-placeholder"
REQUIRED_OVERRIDES_FILENAME = "required-overrides.json"

_OPTION_PLACEHOLDERS = {
    "select",
    "select one",
    "choose",
    "choose one",
    "please select",
    "-- select --",
}
_NUMERIC_NAME_HINT = re.compile(
    r"(?:^|\b)(?:price|cost|qty|quantity|stock|weight|length|width|height|depth|volume|capacity|"
    r"size|moq|minimum order|warranty|power|voltage|current|frequency|diameter|thickness|"
    r"count|number of|pack size)(?:\b|$)",
    re.IGNORECASE,
)
_NUMERIC_UNIT_HINT = re.compile(
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


def _schema_signature_payload(field: dict[str, Any]) -> list[Any]:
    signature = schema_field_signature(field)
    return [
        str(signature[0]),
        str(signature[1]),
        str(signature[2]),
        bool(signature[3]),
        bool(signature[4]),
        list(signature[5]),
        list(signature[6]),
    ]


def _schema_signature_key(payload: object) -> str | None:
    if not isinstance(payload, list) or len(payload) != 7:
        return None
    if not isinstance(payload[5], list) or not isinstance(payload[6], list):
        return None
    normalized = [
        str(payload[0]),
        str(payload[1]),
        str(payload[2]),
        bool(payload[3]),
        bool(payload[4]),
        [str(value) for value in payload[5]],
        [str(value) for value in payload[6]],
    ]
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def required_override_binding(field: dict[str, Any]) -> dict[str, Any]:
    """Persist both display-era field id and stable schema identity."""

    return {
        "field_id": field_id(field),
        "schema_signature": _schema_signature_payload(field),
    }


def _usable_option(values: Iterable[str]) -> str:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    for value in cleaned:
        if value.casefold() not in _OPTION_PLACEHOLDERS:
            return value
    return cleaned[0] if cleaned else ""


def _has_typed_live_value_control(field: dict[str, Any]) -> bool:
    for control in field.get("controls") or []:
        if not isinstance(control, dict):
            continue
        if str(control.get("name") or "").endswith("_qualifier"):
            continue
        if any(
            str(control.get(key) or "").strip()
            for key in ("field_kind", "type", "role", "inputmode")
        ):
            return True
    return False


def _looks_numeric(field: dict[str, Any]) -> bool:
    if is_numeric_semantic_field(field):
        return True
    if _has_typed_live_value_control(field):
        return False

    identity_text = " | ".join(
        re.sub(r"[_-]+", " ", str(field.get(key) or ""))
        for key in ("attribute_key", "label")
    )
    if _NUMERIC_NAME_HINT.search(identity_text):
        return True

    unit_text = " | ".join(
        str(field.get(key) or "") for key in ("help_text", "context_text")
    )
    return bool(_NUMERIC_UNIT_HINT.search(unit_text))


def required_fallback_override(field: dict[str, Any]) -> dict[str, Any]:
    """Build the final deterministic fallback for one unresolved required field.

    This function is deliberately downstream of the Resolver.  It never touches
    an AI READY field and never competes with an AI product decision.  It exists
    only so a required Makro field that the AI could not resolve does not block
    the listing forever.

    The fallback is derived from the current live field contract:
    - selection -> first enabled executable option;
    - numeric/unit field -> ``1`` plus the first executable qualifier when needed;
    - remaining free text -> ``N/A``.

    A selection/qualifier control with no executable option fails closed rather
    than inventing a value the browser cannot write.
    """

    if not allow_required_fallback(field):
        label = str(field.get("label") or field.get("attribute_key") or "required field")
        raise RequiredOverrideError(f"{label} 当前策略不允许自动必填兜底。")

    binding = required_override_binding(field)
    option = _usable_option(executable_value_options(field))
    if option:
        return {
            **binding,
            "values": [option],
            "source_type": "fallback",
            "reason": "deterministic first enabled live Makro option for unresolved required field",
        }

    if is_selection_semantic_field(field):
        label = str(field.get("label") or field.get("attribute_key") or "required field")
        raise RequiredOverrideError(
            f"{label} 当前是 selection 控件，但没有可执行的 enabled live option；无法自动兜底。"
        )

    qualifier_controls = [
        control
        for control in field.get("controls") or []
        if isinstance(control, dict)
        and str(control.get("name") or "").endswith("_qualifier")
    ]
    qualifier = _usable_option(executable_qualifier_options(field))
    if qualifier:
        return {
            **binding,
            "values": [FALLBACK_NUMERIC_VALUE],
            "qualifier": qualifier,
            "source_type": "fallback",
            "reason": "deterministic numeric placeholder with first enabled live Makro qualifier",
        }
    if qualifier_controls:
        label = str(field.get("label") or field.get("attribute_key") or "required field")
        raise RequiredOverrideError(
            f"{label} 当前 qualifier 控件没有可执行的 enabled live option；无法自动兜底。"
        )

    value = FALLBACK_NUMERIC_VALUE if _looks_numeric(field) else FALLBACK_TEXT_VALUE
    return {
        **binding,
        "values": [value],
        "source_type": "fallback",
        "reason": "deterministic placeholder for unresolved required field",
    }


def _bind_plan_items_to_fields(
    plan: LiveFillPlan,
    semantic_fields: Iterable[dict[str, Any]],
) -> dict[str, LiveFillPlanItem]:
    """Bind repeated live fields occurrence-by-occurrence without guessing."""

    item_buckets: dict[tuple[str, str, str], list[LiveFillPlanItem]] = {}
    for item in plan.items:
        item_buckets.setdefault(_item_identity(item), []).append(item)

    positions: dict[tuple[str, str, str], int] = {}
    by_field_id: dict[str, LiveFillPlanItem] = {}
    for field in semantic_fields:
        identity = _field_identity(field)
        bucket = item_buckets.get(identity, [])
        position = positions.get(identity, 0)
        if position >= len(bucket):
            raise RequiredOverrideError(
                "当前 live field 无法按出现顺序绑定到 Fill Plan；"
                f" identity={identity!r} occurrence={position + 1}。"
            )
        identifier = field_id(field)
        if identifier in by_field_id:
            raise RequiredOverrideError(
                f"当前 live schema field_id={identifier} 不唯一；required override 拒绝猜目标。"
            )
        by_field_id[identifier] = bucket[position]
        positions[identity] = position + 1

    for identity, bucket in item_buckets.items():
        if positions.get(identity, 0) != len(bucket):
            raise RequiredOverrideError(
                "Fill Plan 与当前 live fields 的重复字段数量不一致；"
                f" identity={identity!r} plan={len(bucket)} live={positions.get(identity, 0)}。"
            )
    return by_field_id


def load_required_blocked_fields(
    fill_plan_path: str | Path,
    live_schema_path: str | Path,
) -> list[dict[str, Any]]:
    """Return unresolved required fields with occurrence-aware schema binding."""

    payload = json.loads(Path(fill_plan_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise RequiredOverrideError("fill-plan.json 缺少 items 数组。")
    fields = load_live_schema(live_schema_path)

    field_buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for field in fields:
        field_buckets.setdefault(_field_identity(field), []).append(field)

    positions: dict[tuple[str, str, str], int] = {}
    output: list[dict[str, Any]] = []
    valid_items = 0
    for index, raw_item in enumerate(payload.get("items") or [], start=1):
        if not isinstance(raw_item, dict):
            raise RequiredOverrideError(f"fill-plan item[{index}] 不是对象。")
        valid_items += 1
        identity = _field_identity(raw_item)
        bucket = field_buckets.get(identity, [])
        position = positions.get(identity, 0)
        if position >= len(bucket):
            raise RequiredOverrideError(
                "Fill Plan 字段无法按出现顺序绑定到 live schema；"
                f" identity={identity!r} occurrence={position + 1}。"
            )
        field = bucket[position]
        positions[identity] = position + 1

        if bool(raw_item.get("required")) != bool(field.get("required")):
            raise RequiredOverrideError(
                "Fill Plan required 标记与 live schema 不一致；"
                f" identity={identity!r} occurrence={position + 1}。"
            )
        if not bool(raw_item.get("required")):
            continue
        if str(raw_item.get("action") or "").casefold() != BLOCKED:
            continue

        resolution = raw_item.get("resolution") or {}
        if not isinstance(resolution, dict):
            resolution = {}
        output.append(
            {
                "field_id": field_id(field),
                "field": field,
                "label": str(raw_item.get("label") or raw_item.get("attribute_key") or "必填字段"),
                "reason": str(raw_item.get("reason") or resolution.get("detail") or "").strip(),
                "options": [
                    str(value).strip()
                    for value in resolution.get("question_options") or []
                    if str(value).strip()
                    and str(value).strip().casefold() not in {"select one", "select"}
                ],
            }
        )

    if valid_items != len(fields):
        raise RequiredOverrideError(
            f"Fill Plan/live schema 字段数量不一致：plan={valid_items}, live={len(fields)}。"
        )
    for identity, bucket in field_buckets.items():
        if positions.get(identity, 0) != len(bucket):
            raise RequiredOverrideError(
                "Fill Plan/live schema 的重复字段数量不一致；"
                f" identity={identity!r} plan={positions.get(identity, 0)} live={len(bucket)}。"
            )
    return output


def build_required_fallback_overrides(
    fill_plan_path: str | Path,
    live_schema_path: str | Path,
) -> list[dict[str, Any]]:
    """Generate fallbacks only for required fields still BLOCKED after AI."""

    return [
        required_fallback_override(item["field"])
        for item in load_required_blocked_fields(fill_plan_path, live_schema_path)
        if allow_required_fallback(item["field"])
    ]


def write_required_fallback_overrides(
    fill_plan_path: str | Path,
    live_schema_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Persist deterministic required fallbacks for Batch execution."""

    schema_path = Path(live_schema_path).resolve()
    target = (
        Path(output_path).resolve()
        if output_path is not None
        else schema_path.with_name(REQUIRED_OVERRIDES_FILENAME)
    )
    overrides = build_required_fallback_overrides(fill_plan_path, schema_path)
    if not overrides:
        if target.exists():
            target.unlink()
        return {"path": "", "count": 0, "field_ids": []}

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"overrides": overrides}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "path": str(target),
        "count": len(overrides),
        "field_ids": [str(item.get("field_id") or "") for item in overrides],
    }


def _target_from_override(
    override: dict[str, Any],
    fields: list[dict[str, Any]],
    planned_fields: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    identifier = str(override.get("field_id") or "").strip()
    if not identifier:
        raise RequiredOverrideError("required override 缺少 field_id。")

    direct = [field for field in fields if field_id(field) == identifier]
    if len(direct) > 1:
        raise RequiredOverrideError(f"required override field_id={identifier} 匹配多个当前 live fields。")
    if len(direct) == 1:
        return direct[0], False

    signature_key = _schema_signature_key(override.get("schema_signature"))
    if signature_key is None and planned_fields:
        planned_matches = [field for field in planned_fields if field_id(field) == identifier]
        if len(planned_matches) > 1:
            raise RequiredOverrideError(
                f"required override field_id={identifier} 在 planned live schema 中不唯一。"
            )
        if len(planned_matches) == 1:
            signature_key = _schema_signature_key(
                _schema_signature_payload(planned_matches[0])
            )

    if signature_key is None:
        raise RequiredOverrideError(
            f"required override 无法绑定当前 live field：field_id={identifier}，且没有稳定 schema identity。"
        )

    rebound = [
        field
        for field in fields
        if _schema_signature_key(_schema_signature_payload(field)) == signature_key
    ]
    if len(rebound) != 1:
        raise RequiredOverrideError(
            f"required override field_id={identifier} 无法唯一重绑当前 live schema；stable_matches={len(rebound)}。"
        )
    return rebound[0], True


def _source_metadata(source_type: str) -> tuple[str, str, float, str]:
    if source_type == "user":
        return (
            "user",
            "user:required-override",
            1.0,
            "Explicit user value for an unresolved required Makro field.",
        )
    if source_type in {"fallback", "system"}:
        return (
            "fallback",
            FALLBACK_SOURCE_REFERENCE,
            0.0,
            "Deterministic non-AI placeholder used only after AI left a required field unresolved.",
        )
    raise RequiredOverrideError(f"required override source_type={source_type!r} 不受支持。")


def apply_required_overrides(
    plan: LiveFillPlan,
    semantic_fields: Iterable[dict[str, Any]],
    overrides: Iterable[dict[str, Any]],
    *,
    planned_fields: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve only still-BLOCKED required fields from user input or fallback.

    Existing READY items are never replaced.  Therefore the Resolver remains the
    sole owner whenever it produced a decision; deterministic fallback activates
    only after AI has left a required field unresolved.

    Persisted fallback values are treated as instructions, not trusted product
    data: every fallback is recomputed from the current live field before use and
    then passes the non-AI mechanical validator.
    """

    fields = list(semantic_fields)
    planned = list(planned_fields or [])
    items_by_field_id = _bind_plan_items_to_fields(plan, fields)

    applied: list[str] = []
    source_counts = {"user": 0, "fallback": 0}
    rebound_by_schema_signature = 0
    skipped_current_ready = 0
    fallback_recomputed_live = 0

    for index, raw in enumerate(overrides, start=1):
        if not isinstance(raw, dict):
            continue

        live_field, rebound = _target_from_override(raw, fields, planned)
        if rebound:
            rebound_by_schema_signature += 1
        current_identifier = field_id(live_field)
        item = items_by_field_id.get(current_identifier)
        if item is None:
            raise RequiredOverrideError(
                f"override[{index}] 无法绑定当前 Fill Plan：{current_identifier}"
            )
        if not item.required:
            raise RequiredOverrideError(f"{item.label} 不是 required 字段，拒绝 required override。")
        if item.action == READY:
            skipped_current_ready += 1
            continue
        if item.action != BLOCKED:
            raise RequiredOverrideError(
                f"{item.label} 当前 action={item.action!r}，不是可补齐的 BLOCKED required 字段。"
            )

        requested_source = str(raw.get("source_type") or "user").strip().casefold()
        source_type, source_reference, confidence, evidence = _source_metadata(requested_source)
        effective = raw
        if source_type == "fallback":
            effective = required_fallback_override(live_field)
            fallback_recomputed_live += 1

        raw_values = effective.get("values")
        if raw_values is None:
            raw_values = [effective.get("value")]
        if not isinstance(raw_values, list):
            raise RequiredOverrideError(f"{item.label} 的 values 必须是数组。")
        values = [str(value).strip() for value in raw_values if str(value or "").strip()]
        if not values:
            raise RequiredOverrideError(f"{item.label} 的补充值为空。")
        qualifier = str(effective.get("qualifier") or "").strip()

        hard_validation = validate_resolved_answer(
            live_field,
            ResolvedAnswer(
                attribute_key=item.attribute_key,
                label=item.label,
                status=RESOLVED,
                answer=" + ".join(values),
                answer_values=list(values),
                qualifier=qualifier or None,
                confidence=confidence,
                source_type=source_type,
                source_reference=source_reference,
                evidence=evidence,
                detail=str(effective.get("reason") or "").strip(),
            ),
        )
        if not hard_validation.valid:
            raise RequiredOverrideError(f"{item.label}: {hard_validation.detail}")

        record = item.resolution
        record.status = RESOLVED
        record.answer = " + ".join(values)
        record.answer_values = list(values)
        record.qualifier = qualifier or None
        record.confidence = confidence
        record.source_type = source_type
        record.source_reference = source_reference
        record.evidence = evidence
        record.detail = (
            "deterministic required-field fallback"
            if source_type == "fallback"
            else str(raw.get("reason") or "explicit user decision").strip()
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
            "AI 未解决的 Makro 必填项已使用固定自动兜底。"
            if source_type == "fallback"
            else "用户补充了 AI 未解决的 Makro 必填值。"
        )
        applied.append(current_identifier)
        source_counts[source_type] += 1

    _apply_business_relations(plan.items)
    return {
        "applied": len(applied),
        "field_ids": applied,
        "sources": source_counts,
        "rebound_by_schema_signature": rebound_by_schema_signature,
        "skipped_current_ready": skipped_current_ready,
        "fallback_recomputed_live": fallback_recomputed_live,
        "automatic_fallback_enabled": True,
    }
