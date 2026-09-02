from __future__ import annotations

import math
import re
from typing import Any, Iterable


LIVE_FIELD_CONTRACT_VERSION = 1

_TEXT_KINDS = {"input", "custom_textbox", "custom_searchbox"}
_LONG_TEXT_KINDS = {"textarea", "contenteditable"}
_SELECT_KINDS = {"select", "dropdown", "autocomplete", "listbox", "radio", "custom_radio"}
_BOOLEAN_KINDS = {"checkbox", "custom_checkbox"}
_NUMERIC_KINDS = {"custom_spinbutton", "custom_slider"}
_QUALIFIER_NAME_RE = re.compile(r"_qualifier$")
_FIXED_UNIT_SUFFIX_RE = re.compile(
    r"^\*+\s*(?P<unit>[A-Za-zµμ°%]+(?:[./-][A-Za-zµμ°%]+)*(?:\s+[A-Za-zµμ°%]+(?:[./-][A-Za-zµμ°%]+)*){0,2})\s*$"
)
_UNIT_TOKEN_RE = re.compile(r"[^a-z0-9µμ°%]+")
_UNIT_ALIASES = {
    "centimeter": "cm", "centimeters": "cm", "centimetre": "cm", "centimetres": "cm",
    "millimeter": "mm", "millimeters": "mm", "millimetre": "mm", "millimetres": "mm",
    "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "in": "inch", "inches": "inch",
    "kilogram": "kg", "kilograms": "kg", "gram": "g", "grams": "g",
    "milligram": "mg", "milligrams": "mg", "pound": "lb", "pounds": "lb", "lbs": "lb",
    "watt": "w", "watts": "w", "kilowatt": "kw", "kilowatts": "kw",
    "milliliter": "ml", "milliliters": "ml", "millilitre": "ml", "millilitres": "ml",
    "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "days": "day", "degreescelsius": "°c", "degreecelsius": "°c", "celsius": "°c", "c": "°c",
    "degreesfahrenheit": "°f", "degreefahrenheit": "°f", "fahrenheit": "°f", "f": "°f",
}


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _clean_options(items: Iterable[object]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in items:
        if isinstance(raw, dict):
            if bool(raw.get("disabled")):
                continue
            value = str(raw.get("text") or raw.get("value") or "").strip()
        else:
            value = str(raw or "").strip()
        key = _norm(value)
        if value and key and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def value_controls(field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control for control in field.get("controls") or []
        if isinstance(control, dict)
        and str(control.get("field_kind") or "").casefold() != "option"
        and not _QUALIFIER_NAME_RE.search(str(control.get("name") or ""))
    ]


def qualifier_controls(field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control for control in field.get("controls") or []
        if isinstance(control, dict)
        and _QUALIFIER_NAME_RE.search(str(control.get("name") or ""))
    ]


def _primary_control(field: dict[str, Any], controls: list[dict[str, Any]]) -> dict[str, Any] | None:
    key = str(field.get("attribute_key") or "")
    if key:
        for control in controls:
            if str(control.get("id") or "") == key:
                return control
    return controls[0] if controls else None


def is_numeric_control(control: dict[str, Any]) -> bool:
    return (
        str(control.get("field_kind") or "").casefold() in _NUMERIC_KINDS
        or str(control.get("type") or "").casefold() in {"number", "range"}
        or str(control.get("role") or "").casefold() in {"spinbutton", "slider"}
        or str(control.get("inputmode") or "").casefold() in {"numeric", "decimal"}
    )


def _family(field: dict[str, Any], controls: list[dict[str, Any]]) -> str:
    primary = _primary_control(field, controls)
    if primary is None:
        return "unsupported"
    kinds = [str(control.get("field_kind") or "").casefold() for control in controls]
    if controls and all(kind in {"radio", "custom_radio"} for kind in kinds):
        return "selection"
    kind = str(primary.get("field_kind") or "").casefold()
    if kind in _BOOLEAN_KINDS:
        return "boolean"
    if is_numeric_control(primary):
        return "numeric"
    if kind in _SELECT_KINDS:
        return "selection"
    if kind in _LONG_TEXT_KINDS:
        return "long_text"
    if kind in _TEXT_KINDS:
        return "text"
    return "unsupported"


def normalize_unit(value: object) -> str:
    raw = str(value or "").strip().casefold().replace("μ", "µ")
    if not raw:
        return ""
    compact = _UNIT_TOKEN_RE.sub("", raw)
    return _UNIT_ALIASES.get(compact, compact)


def units_equivalent(left: object, right: object) -> bool:
    left_unit = normalize_unit(left)
    right_unit = normalize_unit(right)
    return bool(left_unit and right_unit and left_unit == right_unit)


def _candidate_contexts(field: dict[str, Any]) -> list[str]:
    output: list[str] = []
    for raw in [field.get("context_text"), *(c.get("context_text") for c in field.get("controls") or [] if isinstance(c, dict))]:
        text = " ".join(str(raw or "").split()).strip()
        if text and text not in output:
            output.append(text)
    return output


def _candidate_labels(field: dict[str, Any]) -> list[str]:
    output: list[str] = []
    for raw in (field.get("label"), field.get("rendered_label")):
        text = " ".join(str(raw or "").split()).strip()
        if text and text not in output:
            output.append(text)
    return output


def fixed_rendered_unit(field: dict[str, Any]) -> str:
    if qualifier_controls(field):
        return ""
    labels = _candidate_labels(field)
    if not labels:
        return ""
    for context in _candidate_contexts(field):
        local = context.split("|", 1)[0].strip()
        for label in labels:
            if not local.casefold().startswith(label.casefold()):
                continue
            match = _FIXED_UNIT_SUFFIX_RE.fullmatch(local[len(label):].strip())
            if match:
                return match.group("unit").strip()
    return ""


def _multi_value(field: dict[str, Any], controls: list[dict[str, Any]]) -> bool:
    if bool(field.get("multi_value") or field.get("has_add_value_control")):
        return True
    return any(bool(c.get("repeatable") or c.get("has_add_value_control")) for c in controls)


def _value_options(field: dict[str, Any], controls: list[dict[str, Any]]) -> list[str]:
    direct: list[str] = []
    for control in controls:
        direct.extend(_clean_options(control.get("options") or []))
    if direct:
        return list(dict.fromkeys(direct))
    if controls:
        return []
    return _clean_options(field.get("options") or [])


def _qualifier_options(field: dict[str, Any], controls: list[dict[str, Any]]) -> list[str]:
    direct: list[str] = []
    for control in controls:
        direct.extend(_clean_options(control.get("options") or []))
    if direct:
        return list(dict.fromkeys(direct))
    return _clean_options(field.get("qualifier_options") or [])


def _constraint_text(control: dict[str, Any] | None, key: str) -> str:
    if control is None or control.get(key) in (None, ""):
        return ""
    return str(control.get(key)).strip()


def _constraint_int(control: dict[str, Any] | None, key: str) -> int | None:
    if control is None:
        return None
    raw = control.get(key)
    if isinstance(raw, int) and raw > 0:
        return raw
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_persisted(raw: dict[str, Any]) -> dict[str, Any]:
    family = str(raw.get("family") or "unsupported").casefold()
    qualifier_mode = str(raw.get("qualifier_mode") or "none").casefold()
    return {
        "version": LIVE_FIELD_CONTRACT_VERSION,
        "family": family if family in {"numeric", "text", "long_text", "selection", "boolean", "unsupported"} else "unsupported",
        "value_control_count": max(0, int(raw.get("value_control_count") or 0)),
        "multi_value": bool(raw.get("multi_value")),
        "qualifier_mode": qualifier_mode if qualifier_mode in {"none", "selectable", "fixed"} else "none",
        "fixed_unit": str(raw.get("fixed_unit") or "").strip(),
        "options": _clean_options(raw.get("options") or []),
        "qualifier_options": _clean_options(raw.get("qualifier_options") or []),
        "min": str(raw.get("min") or "").strip(),
        "max": str(raw.get("max") or "").strip(),
        "step": str(raw.get("step") or "").strip(),
        "maxlength": raw.get("maxlength") if isinstance(raw.get("maxlength"), int) and raw.get("maxlength") > 0 else None,
        "supported": bool(raw.get("supported")),
        "reason": str(raw.get("reason") or "").strip(),
    }


def execution_contract(field: dict[str, Any]) -> dict[str, Any]:
    """Return the one canonical mechanical contract for a marketplace field.

    Raw live controls win when present. Persisted schemas carry the normalized
    contract verbatim, so Resolver, Fill Plan and executor can reason over the
    same shape without serializing selectors, values or other transient DOM state.
    """
    controls = value_controls(field)
    qcontrols = qualifier_controls(field)
    if not controls and isinstance(field.get("execution_contract"), dict):
        return _normalize_persisted(field["execution_contract"])

    family = _family(field, controls)
    primary = _primary_control(field, controls)
    fixed_unit = fixed_rendered_unit(field)
    qualifier_mode = "selectable" if qcontrols else "fixed" if fixed_unit else "none"
    supported = family != "unsupported"
    return {
        "version": LIVE_FIELD_CONTRACT_VERSION,
        "family": family,
        "value_control_count": len(controls),
        "multi_value": _multi_value(field, controls),
        "qualifier_mode": qualifier_mode,
        "fixed_unit": fixed_unit,
        "options": _value_options(field, controls),
        "qualifier_options": _qualifier_options(field, qcontrols),
        "min": _constraint_text(primary, "min"),
        "max": _constraint_text(primary, "max"),
        "step": _constraint_text(primary, "step"),
        "maxlength": _constraint_int(primary, "maxlength"),
        "supported": supported,
        "reason": "" if supported else "live field has no supported writable control family",
    }


def contract_signature(field: dict[str, Any]) -> tuple[object, ...]:
    contract = execution_contract(field)
    return (
        contract["version"], contract["family"], contract["value_control_count"], contract["multi_value"],
        contract["qualifier_mode"], normalize_unit(contract["fixed_unit"]),
        tuple(sorted(_norm(v) for v in contract["options"])),
        tuple(sorted(_norm(v) for v in contract["qualifier_options"])),
        contract["min"], contract["max"], contract["step"], contract["maxlength"], contract["supported"],
    )


def _finite(value: object) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def validate_qualifier(field: dict[str, Any], qualifier: object) -> str | None:
    expected = str(qualifier or "").strip()
    if not expected:
        return None
    contract = execution_contract(field)
    mode = contract["qualifier_mode"]
    if mode == "none":
        return "答案包含 qualifier，但当前 live field 既没有 qualifier control，也没有可确认的固定单位；未执行写入。"
    if mode == "fixed":
        fixed = contract["fixed_unit"]
        if not units_equivalent(expected, fixed):
            return f"答案单位与页面固定单位冲突；未执行写入：answer_qualifier={expected!r}, fixed_unit={fixed!r}。"
        return None
    options = contract["qualifier_options"]
    if options and _norm(expected) not in {_norm(value) for value in options}:
        return f"qualifier {expected!r} 不属于当前 enabled live qualifier options。"
    return None


__all__ = [
    "LIVE_FIELD_CONTRACT_VERSION", "contract_signature", "execution_contract", "fixed_rendered_unit",
    "is_numeric_control", "normalize_unit", "qualifier_controls", "units_equivalent",
    "validate_qualifier", "value_controls",
]
