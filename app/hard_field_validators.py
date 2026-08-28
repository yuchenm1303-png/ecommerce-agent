from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .resolution_types import RESOLVED, ResolvedAnswer
from .source_bundle import normalize_key


_SELECTION_KINDS = {
    "select",
    "dropdown",
    "autocomplete",
    "listbox",
    "radio",
    "custom_radio",
}


@dataclass(slots=True, frozen=True)
class FieldValidationResult:
    valid: bool
    detail: str = ""


def is_valid_gtin(value: str) -> bool:
    """Validate GTIN-8/UPC-A/GTIN-13/GTIN-14 check digit."""

    digits = "".join(value.split())
    if not digits.isdigit() or len(digits) not in {8, 12, 13, 14}:
        return False
    body = digits[:-1]
    expected = int(digits[-1])
    total = 0
    for offset, char in enumerate(reversed(body)):
        total += int(char) * (3 if offset % 2 == 0 else 1)
    check = (10 - (total % 10)) % 10
    return check == expected


def _value_controls(semantic_field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control
        for control in semantic_field.get("controls") or []
        if isinstance(control, dict)
        and str(control.get("field_kind") or "").casefold() != "option"
        and not str(control.get("name") or "").endswith("_qualifier")
    ]


def has_live_value_control(semantic_field: dict[str, Any]) -> bool:
    """Return True only when the current DOM contract exposes a writable value control."""

    return bool(_value_controls(semantic_field))


def _qualifier_controls(semantic_field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control
        for control in semantic_field.get("controls") or []
        if isinstance(control, dict)
        and str(control.get("name") or "").endswith("_qualifier")
    ]


def _primary_control(semantic_field: dict[str, Any]) -> dict[str, Any] | None:
    controls = _value_controls(semantic_field)
    key = str(semantic_field.get("attribute_key") or "")
    if key:
        for control in controls:
            if str(control.get("id") or "") == key:
                return control
    return controls[0] if controls else None


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _option_value(option: object) -> str:
    if isinstance(option, dict):
        return str(option.get("text") or option.get("value") or "").strip()
    return str(option or "").strip()


def _enabled_option_values(values: Iterable[object]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if isinstance(raw, dict) and bool(raw.get("disabled")):
            continue
        value = _option_value(raw)
        key = _norm(value)
        if not value or not key or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def is_selection_semantic_field(semantic_field: dict[str, Any]) -> bool:
    """Return True when the current value-control family is a selection family."""

    controls = _value_controls(semantic_field)
    if controls:
        return all(
            str(control.get("field_kind") or "").casefold() in _SELECTION_KINDS
            for control in controls
        )
    return bool(semantic_field.get("options"))


def _controls_prove_closed_domain(controls: list[dict[str, Any]]) -> bool:
    if not controls:
        return False
    kinds = [str(control.get("field_kind") or "").casefold() for control in controls]
    if all(kind in {"radio", "custom_radio"} for kind in kinds):
        return True
    if all(kind == "select" for kind in kinds):
        return True
    return any(bool(control.get("options")) for control in controls)


def is_closed_selection_semantic_field(semantic_field: dict[str, Any]) -> bool:
    """Return True only when the current live DOM proves a finite option domain.

    Native selects and radio groups are closed even when their current option set
    is empty. Custom dropdown/autocomplete controls may render options only after
    interaction; without captured options their domain is not yet proven here and
    final execution must validate the unique live choice after opening them.
    """

    controls = _value_controls(semantic_field)
    if controls:
        return is_selection_semantic_field(semantic_field) and _controls_prove_closed_domain(controls)
    return bool(semantic_field.get("options"))


def executable_value_options(semantic_field: dict[str, Any]) -> list[str]:
    """Return only values the current observed value controls can execute."""

    controls = _value_controls(semantic_field)
    if controls:
        direct: list[str] = []
        for control in controls:
            direct.extend(_enabled_option_values(control.get("options") or []))
        if direct:
            return list(dict.fromkeys(direct))
        if all(
            str(control.get("field_kind") or "").casefold()
            in {"radio", "custom_radio"}
            for control in controls
        ):
            return _enabled_option_values(semantic_field.get("options") or [])
        return []
    return _enabled_option_values(semantic_field.get("options") or [])


def executable_qualifier_options(semantic_field: dict[str, Any]) -> list[str]:
    controls = _qualifier_controls(semantic_field)
    if controls:
        output: list[str] = []
        for control in controls:
            output.extend(_enabled_option_values(control.get("options") or []))
        return list(dict.fromkeys(output))
    return _enabled_option_values(semantic_field.get("qualifier_options") or [])


def is_numeric_semantic_field(semantic_field: dict[str, Any]) -> bool:
    """Return True when the current live control itself is numeric.

    This is the shared DOM contract for both hard validation and deterministic
    required-field fallbacks. Field labels are not authoritative: Makro can use
    names such as ``Pick Pack SLA`` or ``Air Flow Level`` for real number inputs.
    """

    control = _primary_control(semantic_field)
    if control is None:
        return False
    return (
        str(control.get("type") or "").casefold() == "number"
        or str(control.get("inputmode") or "").casefold() in {"numeric", "decimal"}
        or str(control.get("role") or "").casefold() == "spinbutton"
        or str(control.get("field_kind") or "") in {"custom_spinbutton", "custom_slider"}
    )


def _float(value: str) -> float | None:
    try:
        number = float(value.strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _numeric_constraint_validation(
    semantic_field: dict[str, Any],
    answer: ResolvedAnswer,
) -> FieldValidationResult:
    control = _primary_control(semantic_field)
    if control is None or not is_numeric_semantic_field(semantic_field):
        return FieldValidationResult(True)

    for raw in answer.answer_values:
        number = _float(raw)
        if number is None:
            return FieldValidationResult(False, f"数值字段答案 {raw!r} 不是有限数字。")
        minimum = _float(str(control.get("min"))) if control.get("min") not in (None, "") else None
        maximum = _float(str(control.get("max"))) if control.get("max") not in (None, "") else None
        if minimum is not None and number < minimum:
            return FieldValidationResult(False, f"数值 {number:g} 小于字段最小值 {minimum:g}。")
        if maximum is not None and number > maximum:
            return FieldValidationResult(False, f"数值 {number:g} 大于字段最大值 {maximum:g}。")
    return FieldValidationResult(True)


def _length_validation(
    semantic_field: dict[str, Any],
    answer: ResolvedAnswer,
) -> FieldValidationResult:
    control = _primary_control(semantic_field)
    if control is None:
        return FieldValidationResult(True)
    maxlength = control.get("maxlength")
    if not isinstance(maxlength, int) or maxlength <= 0:
        return FieldValidationResult(True)
    for value in answer.answer_values:
        if len(value) > maxlength:
            return FieldValidationResult(
                False,
                f"答案长度 {len(value)} 超过字段 maxlength={maxlength}。",
            )
    return FieldValidationResult(True)


def _closed_domain_validation(
    semantic_field: dict[str, Any],
    answer: ResolvedAnswer,
) -> FieldValidationResult:
    if is_closed_selection_semantic_field(semantic_field):
        options = executable_value_options(semantic_field)
        if not options:
            return FieldValidationResult(
                False,
                "当前 live selection 控件没有 enabled executable option；拒绝把自由文本答案标记为 READY。",
            )
        allowed = {_norm(value): value for value in options if _norm(value)}
        for value in answer.answer_values:
            if _norm(value) not in allowed:
                return FieldValidationResult(
                    False,
                    f"selection 答案 {value!r} 不属于当前 enabled live options。",
                )

    qualifier_controls = _qualifier_controls(semantic_field)
    if answer.qualifier and qualifier_controls and _controls_prove_closed_domain(qualifier_controls):
        qualifiers = executable_qualifier_options(semantic_field)
        if not qualifiers:
            return FieldValidationResult(
                False,
                "当前 qualifier 控件没有 enabled executable option。",
            )
        if _norm(answer.qualifier) not in {_norm(value) for value in qualifiers}:
            return FieldValidationResult(
                False,
                f"qualifier {answer.qualifier!r} 不属于当前 enabled live qualifier options。",
            )
    return FieldValidationResult(True)


def validate_resolved_answer(
    semantic_field: dict[str, Any],
    answer: ResolvedAnswer,
) -> FieldValidationResult:
    """Apply deterministic marketplace/control validation before writes.

    Product meaning is intentionally absent here. Translation, synonyms,
    compatibility, feature interpretation and source conflict judgment belong to
    the AI field-decision layer. A proven closed-domain answer is executable only
    when it belongs to the currently enabled option domain; custom interactive
    dropdowns whose options are not yet rendered defer their final truth to the
    exact live-option executor.
    """

    if answer.status != RESOLVED:
        return FieldValidationResult(True)
    if not answer.answer_values:
        return FieldValidationResult(False, "resolved 答案没有 answer_values。")
    if answer.qualifier and not has_live_value_control(semantic_field):
        return FieldValidationResult(
            False,
            "带 qualifier 的答案没有当前 live value control；拒绝把单位内联到未知执行控件。",
        )

    key_names = {
        normalize_key(semantic_field.get("attribute_key")),
        normalize_key(semantic_field.get("label")),
    }
    gtin_names = {"ean", "gtin", "barcode", "upc", "upca", "ean13", "gtin13"}
    if key_names & gtin_names:
        for value in answer.answer_values:
            if not is_valid_gtin(value):
                return FieldValidationResult(
                    False,
                    f"GTIN/EAN 校验失败：{value!r} 不是有效的 GTIN-8/12/13/14。",
                )

    for validator in (
        _closed_domain_validation,
        _numeric_constraint_validation,
        _length_validation,
    ):
        result = validator(semantic_field, answer)
        if not result.valid:
            return result

    return FieldValidationResult(True)
