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
    """Return value options only, never options owned by a qualifier/unit control.

    The raw semantic-field aggregator keeps a convenience union in ``field.options``.
    On Makro numeric+unit attributes that union can contain only the unit selector
    options (kg/g/cm/...), which previously made Fill Plan compare the numeric value
    itself against the unit list. When live controls are available, reconstruct the
    value-option contract from non-qualifier controls and use the aggregate list only
    as a legacy/final fallback.
    """

    controls = [
        control
        for control in field.get("controls") or []
        if isinstance(control, dict)
    ]
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
        # The aggregate field.options may be polluted solely by qualifier options.
        # No primary-control options means this is a free/numeric value input.
        return ()
    return _clean_options(field.get("options") or [])


def _qualifier_options(field: dict[str, Any]) -> tuple[str, ...]:
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
    """Keep nearby rendered wording without treating it as stable schema identity.

    Makro sometimes renders a fixed unit beside a numeric input without a separate
    qualifier control. Preserve all compact live wording that may carry that unit
    instead of returning only the first context fragment.
    """

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
        for key in (
            "context_text",
            "help_text",
            "placeholder",
            "aria_label",
            "label",
        ):
            push(control.get(key))
    return " | ".join(parts)


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
        # Nearby rendered UI text is carried to AI so fixed units/scope are not
        # lost when Makro does not expose a separate qualifier control.
        "context_text": _field_context(field),
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


def schema_field_signature(field: dict[str, Any]) -> tuple[object, ...]:
    """Return the stable field identity used by the production schema drift gate.

    Presentation-only wording and current render state are intentionally excluded.
    Callers that need to rebind a previously planned field must use this same
    identity and still require a unique match on the current live schema.
    """

    return (
        normalize_key(field.get("attribute_key")),
        normalize_key(field.get("label")),
        _stable_section(field.get("section_heading")),
        bool(field.get("required")),
        bool(field.get("multi_value")),
        tuple(sorted(normalize_key(item) for item in _field_options(field))),
        tuple(sorted(normalize_key(item) for item in _qualifier_options(field))),
    )


# Backward-compatible private alias for existing internal/tests that may still
# import the old helper name.
_drift_signature = schema_field_signature


def assert_live_schema_matches(
    planned_fields: Iterable[dict[str, Any]],
    current_fields: Iterable[dict[str, Any]],
) -> None:
    """Fail closed when the current page contract changed after AI planning.

    DOM paths, current values, completion counters, nearby presentation text and
    render state are ignored. Field identity, requiredness, multiplicity and
    option contracts must match.
    """

    planned = Counter(schema_field_signature(field) for field in planned_fields)
    current = Counter(schema_field_signature(field) for field in current_fields)
    if planned == current:
        return

    removed = list((planned - current).elements())
    added = list((current - planned).elements())
    raise RuntimeError(
        "live schema 与当前 Makro 页面不一致；拒绝使用旧答案写入。"
        f" removed={removed[:8]!r}; added={added[:8]!r}"
    )