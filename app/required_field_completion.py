from __future__ import annotations

import json
from typing import Any, Iterable

from .ai_decisions import READY as AI_READY, FieldDecision, field_id, field_options, field_qualifier_options
from .fill_plan import _hard_guard_values
from .source_bundle import normalize_key


COMPLETION_SOURCE_REFERENCE = "model-inference:required-completion"
COMPLETION_SOURCE_TYPE = "model"
_MAX_MODEL_CONFIDENCE = 0.82
_PLACEHOLDER_VALUES = {
    "",
    "unknown",
    "n/a",
    "na",
    "not applicable",
    "not available",
    "tbd",
    "to be determined",
}


def _identity(payload: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(payload.get("attribute_key") or ""),
        str(payload.get("label") or payload.get("attribute_key") or ""),
        str(payload.get("section_heading") or ""),
    )


def _is_identifier_like(field: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(field.get("attribute_key") or ""),
            str(field.get("label") or ""),
            str(field.get("context_text") or ""),
        ]
    ).casefold()
    markers = (
        "ean",
        "gtin",
        "upc",
        "barcode",
        "model number",
        "model no",
        "part number",
        "part no",
        "mpn",
        "serial number",
    )
    return any(marker in text for marker in markers)


def _target(field: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "field_id": field_id(field),
        "key": str(field.get("attribute_key") or ""),
        "label": str(field.get("label") or field.get("attribute_key") or ""),
        "section": str(field.get("section_heading") or ""),
        "required": True,
        "multi_value": bool(field.get("multi_value")),
        "options": field_options(field),
        "qualifier_options": field_qualifier_options(field),
        "context_text": str(field.get("context_text") or ""),
        "help_text": str(field.get("help_text") or ""),
        "previous_gate_reason": reason,
        "identifier_like": _is_identifier_like(field),
    }


def _resolved_context(plan_payload: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in plan_payload.get("items") or []:
        if not isinstance(item, dict) or str(item.get("action") or "").casefold() != "ready":
            continue
        resolution = item.get("resolution") or {}
        values = [
            str(value).strip()
            for value in resolution.get("answer_values") or []
            if str(value).strip()
        ]
        if not values:
            answer = str(resolution.get("answer") or "").strip()
            if answer:
                values = [answer]
        output.append(
            {
                "key": str(item.get("attribute_key") or ""),
                "label": str(item.get("label") or ""),
                "section": str(item.get("section_heading") or ""),
                "values": values,
                "qualifier": str(resolution.get("qualifier") or ""),
                "source_type": str(resolution.get("source_type") or ""),
            }
        )
    return output


def required_targets(
    plan_payload: dict[str, Any],
    live_fields: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    fields = [field for field in live_fields if isinstance(field, dict)]
    by_identity = {_identity(field): field for field in fields}
    targets: list[dict[str, Any]] = []
    for item in plan_payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("required")) or str(item.get("action") or "").casefold() != "blocked":
            continue
        field = by_identity.get(_identity(item))
        if field is None:
            continue
        resolution = item.get("resolution") or {}
        reason = str(item.get("reason") or resolution.get("detail") or "").strip()
        targets.append(_target(field, reason))
    return targets


def _json_schema(targets: list[dict[str, Any]]) -> dict[str, Any]:
    identifiers = [target["field_id"] for target in targets]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "field_id": {"type": "string", "enum": identifiers},
                        "status": {"type": "string", "enum": ["ready", "missing"]},
                        "values": {"type": "array", "items": {"type": "string"}},
                        "qualifier": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                        "basis": {"type": "string", "enum": ["source", "inference", "none"]},
                        "source_id": {"type": "string"},
                    },
                    "required": [
                        "field_id",
                        "status",
                        "values",
                        "qualifier",
                        "confidence",
                        "reason",
                        "basis",
                        "source_id",
                    ],
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["decisions", "summary"],
    }


def build_required_completion_request(
    plan_payload: dict[str, Any],
    live_fields: Iterable[dict[str, Any]],
    *,
    product_url: str,
    grounded_sources: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    fields = list(live_fields)
    targets = required_targets(plan_payload, fields)
    sources = [source for source in grounded_sources if isinstance(source, dict)]
    return {
        "task": "complete_required_marketplace_fields_before_execution",
        "system_instruction": (
            "Resolve only the remaining required Makro fields immediately before browser execution. "
            "Use the supplied product evidence and already resolved fields. Return JSON only."
        ),
        "prompt_instruction": (
            "Return exactly one decision per target. Prefer a practical READY answer when it can be "
            "supported by the supplied product evidence or a conservative ordinary-category inference. "
            "Do not output placeholder text merely to pass the required-field gate."
        ),
        "evidence_policy": "best_effort",
        "product_identity": {"product_url": product_url},
        "context": {
            "resolved_fields": _resolved_context(plan_payload),
            "purpose": "last targeted required-field completion before Full Step 3",
        },
        "target_fields": targets,
        "all_marketplace_fields": [],
        "grounded_sources": sources,
        "rules": [
            "Do not change or reinterpret fields that are already READY.",
            "For a target with options, READY must use one exact allowed option.",
            "For a single-value target, return exactly one value.",
            "For qualifier_options, use one exact allowed qualifier. Otherwise do not invent a qualifier.",
            "Never return PLACEHOLDER, Unknown, N/A, TBD, or an empty-looking surrogate just to satisfy the gate.",
            "EAN/GTIN/UPC/barcodes, serial numbers, legal identities and seller-specific identifiers must never be invented.",
            "For Model Number, Part Number, MPN or another identifier-like target, READY is allowed only when the exact value is present in supplied evidence. Set basis=source and source_id to that evidence source; otherwise return MISSING.",
            "For ordinary descriptive/product-property fields, conservative category inference is allowed when it does not contradict evidence; set basis=inference and use lower confidence.",
            "If evidence is insufficient for a value that would be unsafe to infer, return MISSING. The GUI will ask the user only for that residual field.",
        ],
        "json_contract": _json_schema(targets),
        "strict_json_schema": True,
    }


def _placeholder(value: str) -> bool:
    normalized = normalize_key(value)
    if normalized in _PLACEHOLDER_VALUES:
        return True
    return normalized.startswith("placeholder") or normalized.startswith("test placeholder")


def parse_required_completion_response(
    raw: Any,
    targets: Iterable[dict[str, Any]],
    live_fields: Iterable[dict[str, Any]],
    *,
    allowed_source_ids: Iterable[str] = (),
) -> dict[str, Any]:
    target_list = list(targets)
    target_ids = {str(item.get("field_id") or "") for item in target_list}
    target_by_id = {str(item.get("field_id") or ""): item for item in target_list}
    live_by_id = {field_id(field): field for field in live_fields if isinstance(field, dict)}
    source_ids = {str(value) for value in allowed_source_ids if str(value).strip()}

    if not isinstance(raw, dict) or not isinstance(raw.get("decisions"), list):
        raise ValueError("required completion response requires decisions array")

    overrides: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    seen: set[str] = set()

    for decision in raw["decisions"]:
        if not isinstance(decision, dict):
            continue
        identifier = str(decision.get("field_id") or "").strip()
        if identifier not in target_ids or identifier in seen:
            continue
        seen.add(identifier)
        target = target_by_id[identifier]
        live_field = live_by_id.get(identifier)
        if live_field is None:
            unresolved.append({"field_id": identifier, "reason": "live field missing"})
            continue

        status = str(decision.get("status") or "missing").strip().casefold()
        values = [
            str(value).strip()
            for value in decision.get("values") or []
            if str(value).strip()
        ]
        reason = str(decision.get("reason") or "").strip()
        basis = str(decision.get("basis") or "none").strip().casefold()
        source_id = str(decision.get("source_id") or "").strip()

        if status != "ready" or not values:
            unresolved.append({"field_id": identifier, "reason": reason or "model returned MISSING"})
            continue
        if any(_placeholder(value) for value in values):
            unresolved.append({"field_id": identifier, "reason": "placeholder-like model value rejected"})
            continue

        if bool(target.get("identifier_like")):
            if basis != "source" or not source_id or source_id not in source_ids:
                unresolved.append(
                    {
                        "field_id": identifier,
                        "reason": "identifier-like field requires explicit supplied evidence",
                    }
                )
                continue

        try:
            confidence = float(decision.get("confidence", 0.6))
        except (TypeError, ValueError):
            confidence = 0.6
        confidence = max(0.0, min(_MAX_MODEL_CONFIDENCE, confidence))
        candidate = FieldDecision(
            field_id=identifier,
            status=AI_READY,
            values=values,
            qualifier=str(decision.get("qualifier") or "").strip(),
            confidence=confidence,
            reason=reason or "targeted required-field completion",
        )
        canonical_values, qualifier, hard_error = _hard_guard_values(live_field, candidate)
        if hard_error:
            unresolved.append({"field_id": identifier, "reason": hard_error})
            continue

        overrides.append(
            {
                "field_id": identifier,
                "values": canonical_values,
                "qualifier": qualifier,
                "source_type": COMPLETION_SOURCE_TYPE,
                "source_reference": COMPLETION_SOURCE_REFERENCE,
                "confidence": confidence,
                "reason": reason or "targeted required-field completion",
                "basis": basis,
                "source_id": source_id,
            }
        )

    for target in target_list:
        identifier = str(target.get("field_id") or "")
        if identifier and identifier not in seen:
            unresolved.append({"field_id": identifier, "reason": "model omitted target"})

    return {
        "schema_version": 1,
        "requested": len(target_list),
        "ready": len(overrides),
        "unresolved_count": len(unresolved),
        "overrides": overrides,
        "unresolved": unresolved,
        "summary": str(raw.get("summary") or "").strip(),
    }
