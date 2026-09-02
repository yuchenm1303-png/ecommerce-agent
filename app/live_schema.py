from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .live_field_contract import contract_signature, execution_contract
from .makro.listing_draft_identity import (
    DRAFT_IDENTITY_FIELD,
    assert_same_listing_draft,
    normalized_listing_draft_identity,
)
from .source_bundle import normalize_key

SCHEMA_VERSION = 3


def _stable_section(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\([^)]*\)", " ", text)
    return normalize_key(text)


def _clean_options(items: Iterable[object]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in items:
        if isinstance(raw, dict):
            value = str(raw.get("text") or raw.get("value") or "").strip()
        else:
            value = str(raw or "").strip()
        key = normalize_key(value)
        if not value or not key or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return tuple(output)


def _field_options(field: dict[str, Any]) -> tuple[str, ...]:
    contract = execution_contract(field)
    if contract.get("options"):
        return tuple(str(item) for item in contract["options"])
    controls = [control for control in field.get("controls") or [] if isinstance(control, dict)]
    output: list[str] = []
    seen: set[str] = set()
    has_qualifier_control = False
    for control in controls:
        if str(control.get("name") or "").endswith("_qualifier"):
            has_qualifier_control = True
            continue
        for item in _clean_options(control.get("options") or []):
            key = normalize_key(item)
            if key not in seen:
                output.append(item)
                seen.add(key)
    if output:
        return tuple(output)
    if controls and has_qualifier_control:
        return ()
    return _clean_options(field.get("options") or [])


def _qualifier_options(field: dict[str, Any]) -> tuple[str, ...]:
    contract = execution_contract(field)
    if contract.get("qualifier_options"):
        return tuple(str(item) for item in contract["qualifier_options"])
    output = list(_clean_options(field.get("qualifier_options") or []))
    seen = {normalize_key(item) for item in output}
    for control in field.get("controls") or []:
        if str(control.get("name") or "").endswith("_qualifier"):
            for item in _clean_options(control.get("options") or []):
                key = normalize_key(item)
                if key not in seen:
                    output.append(item)
                    seen.add(key)
    return tuple(output)


def _field_context(field: dict[str, Any]) -> str:
    parts: list[str] = []
    seen: set[str] = set()

    def push(value: object) -> None:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            parts.append(text)

    push(field.get("context_text"))
    push(field.get("help_text"))
    for control in field.get("controls") or []:
        if not isinstance(control, dict):
            continue
        for key in ("context_text", "help_text", "placeholder", "aria_label", "label"):
            push(control.get(key))
    return " | ".join(parts)


def _field_multi_value(field: dict[str, Any]) -> bool:
    return bool(execution_contract(field).get("multi_value"))


def _schema_field(field: dict[str, Any]) -> dict[str, Any]:
    contract = execution_contract(field)
    return {
        "attribute_key": str(field.get("attribute_key") or ""),
        "label": str(field.get("label") or ""),
        "section_heading": str(field.get("section_heading") or ""),
        "required": bool(field.get("required")),
        "multi_value": bool(contract["multi_value"]),
        "options": list(contract["options"]),
        "qualifier_options": list(contract["qualifier_options"]),
        "help_text": str(field.get("help_text") or ""),
        "context_text": _field_context(field),
        "execution_contract": contract,
    }


def live_schema_payload(
    semantic_fields: Iterable[dict[str, Any]],
    *,
    listing_draft_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize stable field identity plus the canonical mechanical execution contract."""
    identity = normalized_listing_draft_identity(listing_draft_identity)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "fields": [_schema_field(field) for field in semantic_fields],
    }
    if identity is not None:
        payload["listing_draft_identity"] = identity
    return payload


def write_live_schema(
    semantic_fields: Iterable[dict[str, Any]],
    path: str | Path,
    *,
    listing_draft_identity: dict[str, Any] | None = None,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            live_schema_payload(semantic_fields, listing_draft_identity=listing_draft_identity),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def load_live_schema(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            "live schema 格式或 schema_version 不受支持；请重新扫描当前 Makro 页面生成包含完整执行合同的 schema。"
        )
    fields = payload.get("fields")
    if not isinstance(fields, list):
        raise ValueError("live schema 缺少 fields 数组。")
    identity = normalized_listing_draft_identity(payload.get("listing_draft_identity"))
    output: list[dict[str, Any]] = []
    for raw in fields:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if not isinstance(item.get("execution_contract"), dict):
            raise ValueError("live schema field 缺少 execution_contract；请重新扫描当前 Makro 页面。")
        if identity is not None:
            item[DRAFT_IDENTITY_FIELD] = dict(identity)
        output.append(item)
    return output


def schema_field_signature(field: dict[str, Any]) -> tuple[object, ...]:
    """Stable address plus mechanical contract used by the production drift gate."""
    return (
        normalize_key(field.get("attribute_key")),
        normalize_key(field.get("label")),
        _stable_section(field.get("section_heading")),
        bool(field.get("required")),
        *contract_signature(field),
    )


_drift_signature = schema_field_signature


def _listing_identity(fields: Iterable[dict[str, Any]]) -> dict[str, str] | None:
    identities: list[dict[str, str]] = []
    for field in fields:
        identity = normalized_listing_draft_identity(field.get(DRAFT_IDENTITY_FIELD))
        if identity is None:
            continue
        if identity not in identities:
            identities.append(identity)
    if len(identities) > 1:
        raise RuntimeError("one live schema contains multiple Makro draft identities")
    return identities[0] if identities else None


def assert_live_schema_matches(
    planned_fields: Iterable[dict[str, Any]],
    current_fields: Iterable[dict[str, Any]],
) -> None:
    """Fail closed before writing when ownership, address or mechanical contract drifted."""
    planned_items = list(planned_fields)
    current_items = list(current_fields)
    prepared_identity = _listing_identity(planned_items)
    if prepared_identity is not None:
        assert_same_listing_draft(prepared_identity, _listing_identity(current_items))

    planned = Counter(schema_field_signature(field) for field in planned_items)
    current = Counter(schema_field_signature(field) for field in current_items)
    if planned == current:
        return
    removed = list((planned - current).elements())
    added = list((current - planned).elements())
    raise RuntimeError(
        "live schema 与当前 Makro 页面执行合同不一致；拒绝使用旧答案写入。"
        f" removed={removed[:8]!r}; added={added[:8]!r}"
    )
