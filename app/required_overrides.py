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
from .hard_field_validators import is_numeric_semantic_field, validate_resolved_answer
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
    """JSON-safe form of the exact identity used by live-schema drift checks."""

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
    """Persist both the display-era field id and its stable schema identity.

    ``field_id`` is still the first-choice address. The schema signature is a
    fail-closed rebind key for the case where an equivalent current DOM field is
    represented slightly differently from the serialized planning field.
    """

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
    """Return True once the current value control exposes its mechanical type.

    Modern live scans always include ``field_kind/type/role/inputmode``. When
    those facts exist they are authoritative even when the control is *not*
    numeric; legacy label heuristics must not overrule a proven text/select DOM
    control merely because prose help text contains words such as ``current``.
    """

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
    # Current live DOM control metadata is authoritative. Stable field identity
    # is only a backward-compatibility fallback for old serialized schemas that
    # predate control metadata. Free-form help/context prose is never allowed to
    # turn a named text field numeric; it may contribute only an explicit unit.
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
    """Build one deterministic, non-AI fallback for an ordinary required field.

    Seller-critical listing/title/package/identifier/compliance fields are
    explicitly excluded by ``listing_content_policy``. Those fields must be
    resolved from evidence/offer intent or explicitly confirmed by the user;
    they may never become ``N/A``, ``1`` or an arbitrary first option merely to
    complete the form.

    For ordinary required fields that remain BLOCKED after the normal Resolver:
    - option/radio/select -> first usable live Makro option;
    - numeric/unit field -> ``1`` and the first usable live qualifier when present;
    - remaining free text -> ``N/A``.

    The production executor still rebinds the value to the current live field and
    runs the existing Makro option/unit hard guards before any browser write.
    """

    if not allow_required_fallback(field):
        label = str(field.get("label") or field.get("attribute_key") or "required field")
        raise RequiredOverrideError(
            f"{label} 是关键 listing 必填字段，禁止使用 N/A / 1 / 随机 option 兜底；请提供准确值。"
        )

    binding = required_override_binding(field)
    options = field_options(field)
    option = _usable_option(options)
    if option:
        return {
            **binding,
            "values": [option],
            "source_type": "fallback",
            "reason": "deterministic first valid Makro option for unresolved ordinary required field",
        }

    qualifiers = field_qualifier_options(field)
    qualifier = _usable_option(qualifiers)
    if qualifier:
        return {
            **binding,
            "values": [FALLBACK_NUMERIC_VALUE],
            "qualifier": qualifier,
            "source_type": "fallback",
            "reason": "deterministic numeric placeholder with first valid Makro qualifier",
        }

    value = FALLBACK_NUMERIC_VALUE if _looks_numeric(field) else FALLBACK_TEXT_VALUE
    return {
        **binding,
        "values": [value],
        "source_type": "fallback",
        "reason": "deterministic placeholder for unresolved ordinary required field",
    }


def _bind_plan_items_to_fields(
    plan: LiveFillPlan,
    semantic_fields: Iterable[dict[str, Any]],
) -> dict[str, LiveFillPlanItem]:
    """Bind every current field to its Fill Plan item without collapsing duplicates.

    Some Makro verticals expose repeated labels such as several ``Length`` fields.
    A dict keyed only by attribute/label/section silently overwrites those items.
    Plan construction and semantic-field scanning preserve occurrence order, so
    repeated identities are bound one-by-one inside their identity bucket.
    """

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
    """Return unresolved required fields using occurrence-aware schema binding.

    This is shared by Single GUI and Batch. It reads only the existing read-only
    Fill Plan plus its live schema and never calls AI or touches the browser.
    """

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
    """Build deterministic fallbacks only for ordinary unresolved required fields.

    Protected fields are intentionally omitted. Their unresolved state remains a
    real execution gate until an explicit user override is provided.
    """

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
    """Persist ordinary Batch/Single deterministic fallback instructions.

    Protected required fields never appear in this file. The executor recomputes
    every persisted ordinary fallback against the current DOM before writing, so
    this file remains a bounded instruction set rather than trusted product data.
    If no ordinary fallback remains, a stale fallback file is removed.
    """

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
            "Deterministic non-AI placeholder used only because the ordinary required field remained unresolved.",
        )
    raise RequiredOverrideError(f"不支持 required override source_type={source_type!r}。")


def apply_required_overrides(
    plan: LiveFillPlan,
    semantic_fields: Iterable[dict[str, Any]],
    overrides: Iterable[dict[str, Any]],
    *,
    planned_fields: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Promote unresolved required fields using explicit user or safe ordinary fallback values.

    No AI/search pass exists here. Explicit user values may resolve protected or
    ordinary required fields. A ``source_type=fallback`` override is accepted only
    when the current live field is still eligible for generic fallback; otherwise
    it fails closed. Both paths are revalidated against the current live Makro
    option/unit hard guards. Existing READY items are never replaced here.

    A persisted ordinary fallback is an instruction to recompute the deterministic
    value from the *current* live DOM field, so an old ``N/A`` cannot survive when
    Makro now proves that control is numeric. Explicit user values are never
    recomputed.

    Binding is deliberately two-stage. Exact current ``field_id`` wins. If that
    presentation-sensitive id changed while the production live-schema drift gate
    still considers the field equivalent, the stable schema signature may rebind
    it only when exactly one current field matches. Zero or multiple matches fail
    closed before any browser write.
    """

    fields = list(semantic_fields)
    planned = list(planned_fields or [])

    fields_by_id: dict[str, list[dict[str, Any]]] = {}
    fields_by_signature: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        fields_by_id.setdefault(field_id(field), []).append(field)
        signature_key = _schema_signature_key(_schema_signature_payload(field))
        if signature_key is not None:
            fields_by_signature.setdefault(signature_key, []).append(field)

    planned_by_id: dict[str, list[dict[str, Any]]] = {}
    for field in planned:
        planned_by_id.setdefault(field_id(field), []).append(field)

    items_by_field_id = _bind_plan_items_to_fields(plan, fields)
    applied: list[str] = []
    source_counts = {"user": 0, "fallback": 0}
    rebound_by_schema_signature = 0
    skipped_current_ready = 0
    fallback_recomputed_live = 0

    for index, override in enumerate(overrides, start=1):
        identifier = str(override.get("field_id") or "").strip()
        if not identifier:
            raise RequiredOverrideError(f"override[{index}] 缺少 field_id。")

        direct = fields_by_id.get(identifier, [])
        if len(direct) > 1:
            raise RequiredOverrideError(
                f"override[{index}] field_id={identifier} 在当前 live schema 中不唯一；拒绝猜目标。"
            )

        live_field: dict[str, Any] | None = direct[0] if direct else None
        if live_field is None:
            signature_payload = override.get("schema_signature")
            signature_key = _schema_signature_key(signature_payload)

            # Backward compatibility for an override file produced before stable
            # signatures were persisted: recover the signature from the exact
            # planned field id that already passed the production schema drift gate.
            if signature_key is None and planned:
                planned_matches = planned_by_id.get(identifier, [])
                if len(planned_matches) > 1:
                    raise RequiredOverrideError(
                        f"override[{index}] field_id={identifier} 在 planned live schema 中不唯一；拒绝猜目标。"
                    )
                if len(planned_matches) == 1:
                    signature_key = _schema_signature_key(
                        _schema_signature_payload(planned_matches[0])
                    )

            if signature_key is None:
                raise RequiredOverrideError(
                    f"override[{index}] field_id={identifier} 不属于当前 live schema，且没有可验证的稳定 schema identity。"
                )

            rebound = fields_by_signature.get(signature_key, [])
            if len(rebound) != 1:
                raise RequiredOverrideError(
                    f"override[{index}] field_id={identifier} 无法唯一重绑到当前 live schema；"
                    f" stable_matches={len(rebound)}。"
                )
            live_field = rebound[0]
            rebound_by_schema_signature += 1

        current_identifier = field_id(live_field)
        item = items_by_field_id.get(current_identifier)
        if item is None:
            raise RequiredOverrideError(
                f"override[{index}] 无法绑定到当前 Fill Plan：{identifier}"
            )
        if not item.required:
            raise RequiredOverrideError(f"{item.label} 不是 required 字段；补充值只用于未解决必填项。")
        if item.action != BLOCKED:
            # The current Resolver/live-control contract already has a READY
            # answer. A stale persisted override must never replace it and must
            # not turn an otherwise valid run into a preflight failure.
            skipped_current_ready += 1
            continue

        source_type, source_reference, confidence, evidence = _source_metadata(override)
        effective_override = override
        if source_type == "fallback":
            # required_fallback_override performs the current protected-field
            # policy check before recomputing any placeholder.
            effective_override = required_fallback_override(live_field)
            fallback_recomputed_live += 1

        raw_values = effective_override.get("values")
        if raw_values is None:
            raw_values = [effective_override.get("value")]
        if not isinstance(raw_values, list):
            raise RequiredOverrideError(f"{item.label} 的 values 必须是数组。")
        values = [str(value).strip() for value in raw_values if str(value or "").strip()]
        if not values:
            raise RequiredOverrideError(f"{item.label} 的补充值为空。")

        reason = str(effective_override.get("reason") or "").strip()
        decision = FieldDecision(
            field_id=current_identifier,
            status=AI_READY,
            values=values,
            qualifier=str(effective_override.get("qualifier") or "").strip(),
            confidence=confidence,
            reason=(
                reason
                or (
                    "deterministic fallback for unresolved ordinary required field"
                    if source_type == "fallback"
                    else "explicit user value for unresolved required field"
                )
            ),
        )
        canonical_values, qualifier, hard_error = _hard_guard_values(live_field, decision)
        if hard_error:
            raise RequiredOverrideError(f"{item.label}: {hard_error}")

        hard_validation = validate_resolved_answer(
            live_field,
            ResolvedAnswer(
                attribute_key=item.attribute_key,
                label=item.label,
                status=RESOLVED,
                answer=" + ".join(canonical_values),
                answer_values=list(canonical_values),
                qualifier=qualifier or None,
                confidence=confidence,
                source_type=source_type,
                source_reference=source_reference,
                evidence=evidence,
                detail=reason,
            ),
        )
        if not hard_validation.valid:
            raise RequiredOverrideError(f"{item.label}: {hard_validation.detail}")

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
            "未解决的普通 Makro 必填项已使用非 AI 的固定兜底值。"
            if source_type == "fallback"
            else "用户补充了 Resolver 未能确定的 Makro 必填值。"
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
    }
