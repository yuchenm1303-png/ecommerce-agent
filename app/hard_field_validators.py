from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .resolution_types import ResolvedAnswer
from .source_bundle import normalize_key


GTIN_KEYS = {
    "ean",
    "gtin",
    "barcode",
    "barcode number",
    "barcode_number",
}


@dataclass(slots=True, frozen=True)
class FactValidationResult:
    valid: bool
    detail: str = ""


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def is_valid_gtin(value: str) -> bool:
    """Validate GTIN-8/12/13/14 checksum without interpreting product meaning."""

    digits = _digits(value)
    if len(digits) not in {8, 12, 13, 14} or digits != value.strip():
        return False
    body = [int(ch) for ch in digits[:-1]]
    check = int(digits[-1])
    total = 0
    for offset, digit in enumerate(reversed(body), start=1):
        total += digit * (3 if offset % 2 == 1 else 1)
    expected = (10 - total % 10) % 10
    return check == expected


def _primary_value(answer: ResolvedAnswer) -> str:
    if answer.answer_values:
        return str(answer.answer_values[0]).strip()
    return str(answer.answer or "").strip()


def _numeric_controls(field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control
        for control in field.get("controls") or []
        if str(control.get("type") or "").casefold() in {"number", "range"}
        or control.get("min") not in {None, ""}
        or control.get("max") not in {None, ""}
    ]


def _decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not math.isfinite(float(parsed)):
        return None
    return parsed


def validate_resolved_answer(
    field: dict[str, Any],
    answer: ResolvedAnswer,
) -> FactValidationResult:
    """Apply only deterministic marketplace/control constraints.

    Product-language semantics, synonyms, inclusion reasoning and attribute
    interpretation belong to the AI resolver and must not be added here.
    """

    value = _primary_value(answer)
    key = normalize_key(field.get("attribute_key") or field.get("label"))
    label = normalize_key(field.get("label"))

    if key in GTIN_KEYS or label in GTIN_KEYS:
        if not is_valid_gtin(value):
            return FactValidationResult(False, "GTIN/EAN checksum 或长度无效。")

    numeric_controls = _numeric_controls(field)
    if numeric_controls:
        parsed = _decimal(value)
        if parsed is None:
            return FactValidationResult(False, "数值控件要求有限数字。")
        for control in numeric_controls:
            minimum = _decimal(control.get("min"))
            maximum = _decimal(control.get("max"))
            if minimum is not None and parsed < minimum:
                return FactValidationResult(False, f"数值低于 Makro 最小值 {minimum}。")
            if maximum is not None and parsed > maximum:
                return FactValidationResult(False, f"数值高于 Makro 最大值 {maximum}。")

    for control in field.get("controls") or []:
        maximum_length = control.get("maxlength")
        if maximum_length in {None, ""}:
            continue
        try:
            limit = int(maximum_length)
        except (TypeError, ValueError):
            continue
        if limit >= 0 and len(value) > limit:
            return FactValidationResult(False, f"文本超过 Makro maxlength={limit}。")

    return FactValidationResult(True)
