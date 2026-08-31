from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .ai_decisions import field_id
from .fill_plan import BLOCKED, READY, LiveFillPlan, LiveFillPlanItem, _apply_business_relations
from .live_schema import load_live_schema, schema_field_signature
from .resolution_types import RESOLVED


# Kept as compatibility constants for old imports/artifacts.  Production no longer
# generates either value automatically.
FALLBACK_TEXT_VALUE = "N/A"
FALLBACK_NUMERIC_VALUE = "1"
FALLBACK_SOURCE_REFERENCE = "system:required-placeholder"
REQUIRED_OVERRIDES_FILENAME = "required-overrides.json"


class RequiredOverrideError(ValueError):
    """Raised when an explicit required-field override cannot be bound safely."""


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


def required_fallback_override(field: dict[str, Any]) -> dict[str, Any]:
    """Automatic required-field placeholders are intentionally disabled.

    AI REVIEW/CONFLICT/MISSING is a real unresolved state.  Python must not turn
    it into READY by choosing ``N/A``, ``1`` or the first marketplace option.
    """

    label = str(field.get("label") or field.get("attribute_key") or "required field")
    raise RequiredOverrideError(
        f"{label} 未由 AI 决策为 READY；自动 N/A / 1 / 首选项兜底已禁用，请由用户明确提供值。"
    )


def _bind_plan_items_to_fields(
    plan: LiveFillPlan,
    semantic_fields: Iterable[dict[str, Any]],
) -> dict[str, LiveFillPlanItem]:
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
    """Return required fields whose authoritative AI action is still BLOCKED."""

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
    """Compatibility API: automatic fallback generation is permanently empty."""

    # Still load/bind the artifacts so corrupt plan/schema pairs fail loudly, but
    # never synthesize a value from them.
    load_required_blocked_fields(fill_plan_path, live_schema_path)
    return []


def write_required_fallback_overrides(
    fill_plan_path: str | Path,
    live_schema_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Compatibility API used by Batch; remove stale synthetic overrides."""

    blocked = load_required_blocked_fields(fill_plan_path, live_schema_path)
    target = Path(output_path) if output_path is not None else Path(live_schema_path).resolve().with_name(REQUIRED_OVERRIDES_FILENAME)
    if target.exists():
        try:
            payload = load_required_overrides(target)
        except Exception:
            payload = []
        # Never delete explicit user-owned values.  Batch normally has none; a
        # stale all-fallback artifact from older versions is safe to remove.
        if payload and any(str(item.get("source_type") or "").casefold() not in {"fallback", "system"} for item in payload):
            return {
                "path": str(target.resolve()),
                "count": 0,
                "blocked_required": len(blocked),
                "automatic_fallback_disabled": True,
                "preserved_explicit_overrides": True,
            }
        target.unlink()
    return {
        "path": "",
        "count": 0,
        "blocked_required": len(blocked),
        "automatic_fallback_disabled": True,
        "preserved_explicit_overrides": False,
    }


def _override_target(
    override: dict[str, Any],
    fields: list[dict[str, Any]],
) -> dict[str, Any]:
    identifier = str(override.get("field_id") or "").strip()
    if identifier:
        matches = [field for field in fields if field_id(field) == identifier]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RequiredOverrideError(f"override field_id={identifier} 匹配多个 live fields。")

    wanted_signature = _schema_signature_key(override.get("schema_signature"))
    if wanted_signature:
        matches = [
            field
            for field in fields
            if _schema_signature_key(_schema_signature_payload(field)) == wanted_signature
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RequiredOverrideError("override schema_signature 匹配多个 live fields。")

    raise RequiredOverrideError(
        f"required override 无法唯一绑定当前 live field：field_id={identifier or '<missing>'}。"
    )


def apply_required_overrides(
    plan: LiveFillPlan,
    semantic_fields: Iterable[dict[str, Any]],
    overrides: Iterable[dict[str, Any]],
    *,
    planned_fields: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply explicit user decisions only; never manufacture product answers."""

    fields = list(semantic_fields)
    # Force occurrence-aware plan/live binding before any mutation.
    by_field_id = _bind_plan_items_to_fields(plan, fields)
    del planned_fields

    applied: list[str] = []
    skipped_ready = 0
    ignored_automatic = 0

    for raw in overrides:
        if not isinstance(raw, dict):
            continue
        source_type = str(raw.get("source_type") or "user").strip().casefold()
        if source_type in {"fallback", "system"}:
            ignored_automatic += 1
            continue
        if source_type != "user":
            raise RequiredOverrideError(
                f"required override source_type={source_type!r} 不受支持；只接受明确 user override。"
            )

        field = _override_target(raw, fields)
        identifier = field_id(field)
        item = by_field_id[identifier]
        if item.action == READY:
            skipped_ready += 1
            continue
        if not item.required:
            raise RequiredOverrideError(f"{item.label} 不是 required 字段，拒绝 required override。")

        values = [str(value).strip() for value in raw.get("values") or [] if str(value).strip()]
        if not values:
            raise RequiredOverrideError(f"{item.label} 的明确用户 override 没有 values。")
        qualifier = str(raw.get("qualifier") or "").strip()

        item.action = READY
        item.reason = "explicit user decision"
        item.resolution.status = RESOLVED
        item.resolution.answer_values = values
        item.resolution.answer = " + ".join(values)
        item.resolution.qualifier = qualifier or None
        item.resolution.confidence = 1.0
        item.resolution.source_type = "user"
        item.resolution.source_reference = "user:required-override"
        item.resolution.evidence = None
        item.resolution.detail = str(raw.get("reason") or "explicit user decision").strip()
        item.resolution.eligible_for_autofill = True
        item.resolution.preview_eligible = False
        item.resolution.gate_reason = ""
        applied.append(identifier)

    _apply_business_relations(plan.items)
    return {
        "applied": len(applied),
        "field_ids": applied,
        "skipped_current_ready": skipped_ready,
        "ignored_automatic": ignored_automatic,
        "fallback_recomputed_live": 0,
        "automatic_fallback_disabled": True,
    }
