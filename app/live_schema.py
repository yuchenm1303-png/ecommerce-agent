from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .source_bundle import normalize_key

SCHEMA_VERSION = 1


def _stable_section(value: object) -> str:
    text = str(value or "").strip()
    # Completion counters and '(Optional)' are presentation state, not schema
    # identity. Ignore them so Save/reopen does not create false drift.
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
    output = list(_clean_options(field.get("options") or []))
    seen = {normalize_key(item) for item in output}
    for control in field.get("controls") or []:
        if str(control.get("name") or "").endswith("_qualifier"):
            continue
        for item in _clean_options(control.get("options") or []):
            key = normalize_key(item)
            if key not in seen:
                output.append(item)
                seen.add(key)
    return tuple(output)


def _qualifier_options(field: dict[str, Any]) -> tuple[str, ...]:
    output = list(_clean_options(field.get("qualifier_options") or []))
    seen = {normalize_key(item) for item in output}
    for control in field.get("controls") or []:
        if not str(control.get("name") or "").endswith("_qualifier"):
            continue
        for item in _clean_options(control.get("options") or []):
            key = normalize_key(item)
            if key not in seen:
                output.append(item)
                seen.add(key)
    return tuple(output)


def _schema_field(field: dict[str, Any]) -> dict[str, Any]:
    return {
        "attribute_key": str(field.get("attribute_key") or ""),
        "label": str(field.get("label") or ""),
        "section_heading": str(field.get("section_heading") or ""),
        "required": bool(field.get("required")),
        "multi_value": bool(field.get("multi_value")),
        "options": list(_field_options(field)),
        "qualifier_options": list(_qualifier_options(field)),
        "help_text": str(field.get("help_text") or ""),
    }


def live_schema_payload(semantic_fields: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Serialize the live Makro field contract without values or sensitive state."""

    return {
        "schema_version": SCHEMA_VERSION,
        "fields": [_schema_field(field) for field in semantic_fields],
    }


def write_live_schema(
    semantic_fields: Iterable[dict[str, Any]],
    path: str | Path,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(live_schema_payload(semantic_fields), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def load_live_schema(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("live schema 格式或 schema_version 不受支持。")
    fields = payload.get("fields")
    if not isinstance(fields, list):
        raise ValueError("live schema 缺少 fields 数组。")
    return [item for item in fields if isinstance(item, dict)]


def _drift_signature(field: dict[str, Any]) -> tuple[object, ...]:
    return (
        normalize_key(field.get("attribute_key")),
        normalize_key(field.get("label")),
        _stable_section(field.get("section_heading")),
        bool(field.get("required")),
        bool(field.get("multi_value")),
        tuple(sorted(normalize_key(item) for item in _field_options(field))),
        tuple(sorted(normalize_key(item) for item in _qualifier_options(field))),
    )


def assert_live_schema_matches(
    planned_fields: Iterable[dict[str, Any]],
    current_fields: Iterable[dict[str, Any]],
) -> None:
    """Fail closed when the current page contract changed after AI planning.

    DOM paths, current values, completion counters and render state are ignored.
    Field identity, requiredness, multiplicity and option contracts must match.
    """

    planned = Counter(_drift_signature(field) for field in planned_fields)
    current = Counter(_drift_signature(field) for field in current_fields)
    if planned == current:
        return

    removed = list((planned - current).elements())
    added = list((current - planned).elements())
    raise RuntimeError(
        "live schema 与当前 Makro 页面不一致；拒绝使用旧答案写入。"
        f" removed={removed[:8]!r}; added={added[:8]!r}"
    )
