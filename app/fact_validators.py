from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .resolution_types import RESOLVED, ResolvedAnswer
from .source_bundle import normalize_key


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


def _primary_control(semantic_field: dict[str, Any]) -> dict[str, Any] | None:
    controls = semantic_field.get("controls") or []
    for control in controls:
        name = str(control.get("name") or "")
        if name.endswith("_qualifier"):
            continue
        return control
    return None


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
    if control is None:
        return FieldValidationResult(True)

    numeric = (
        str(control.get("type") or "").casefold() == "number"
        or str(control.get("inputmode") or "").casefold() in {"numeric", "decimal"}
        or str(control.get("field_kind") or "") in {"custom_spinbutton", "custom_slider"}
    )
    if not numeric:
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


def validate_resolved_answer(
    semantic_field: dict[str, Any],
    answer: ResolvedAnswer,
) -> FieldValidationResult:
    """Apply only deterministic marketplace/control validation before writes.

    Product meaning is intentionally absent here. Translation, synonyms,
    compatibility, feature interpretation and source conflict judgment belong to
    the AI field-decision layer.
    """

    if answer.status != RESOLVED:
        return FieldValidationResult(True)
    if not answer.answer_values:
        return FieldValidationResult(False, "resolved 答案没有 answer_values。")

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

    for validator in (_numeric_constraint_validation, _length_validation):
        result = validator(semantic_field, answer)
        if not result.valid:
            return result

    return FieldValidationResult(True)
