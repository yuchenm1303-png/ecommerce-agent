from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .live_field_contract import execution_contract, validate_qualifier
from .resolution_types import RESOLVED, ResolvedAnswer
from .source_bundle import normalize_key


@dataclass(slots=True, frozen=True)
class FieldValidationResult:
    valid: bool
    detail: str = ""


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _float(value: object) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def is_valid_gtin(value: str) -> bool:
    digits = "".join(value.split())
    if not digits.isdigit() or len(digits) not in {8, 12, 13, 14}:
        return False
    body = digits[:-1]
    expected = int(digits[-1])
    total = 0
    for offset, char in enumerate(reversed(body)):
        total += int(char) * (3 if offset % 2 == 0 else 1)
    return (10 - (total % 10)) % 10 == expected


def has_live_value_control(semantic_field: dict[str, Any]) -> bool:
    return int(execution_contract(semantic_field)["value_control_count"]) > 0


def is_selection_semantic_field(semantic_field: dict[str, Any]) -> bool:
    return execution_contract(semantic_field)["family"] == "selection"


def is_closed_selection_semantic_field(semantic_field: dict[str, Any]) -> bool:
    contract = execution_contract(semantic_field)
    return contract["family"] == "selection" and bool(contract["options"])


def executable_value_options(semantic_field: dict[str, Any]) -> list[str]:
    return list(execution_contract(semantic_field)["options"])


def executable_qualifier_options(semantic_field: dict[str, Any]) -> list[str]:
    return list(execution_contract(semantic_field)["qualifier_options"])


def is_numeric_semantic_field(semantic_field: dict[str, Any]) -> bool:
    return execution_contract(semantic_field)["family"] == "numeric"


def _cardinality_validation(contract: dict[str, Any], answer: ResolvedAnswer) -> FieldValidationResult:
    if not contract["multi_value"] and len(answer.answer_values) > 1:
        return FieldValidationResult(False, "当前 live field 是单值控件，但答案包含多个 values。")
    return FieldValidationResult(True)


def _closed_domain_validation(contract: dict[str, Any], answer: ResolvedAnswer) -> FieldValidationResult:
    if contract["family"] != "selection":
        return FieldValidationResult(True)
    options = list(contract["options"])
    if not options:
        return FieldValidationResult(False, "当前 live selection 控件没有 enabled executable option。")
    allowed = {_norm(value) for value in options if _norm(value)}
    for value in answer.answer_values:
        if _norm(value) not in allowed:
            return FieldValidationResult(False, f"selection 答案 {value!r} 不属于当前 enabled live options。")
    return FieldValidationResult(True)


def _numeric_validation(contract: dict[str, Any], answer: ResolvedAnswer) -> FieldValidationResult:
    if contract["family"] != "numeric":
        return FieldValidationResult(True)
    minimum = _float(contract["min"]) if contract["min"] else None
    maximum = _float(contract["max"]) if contract["max"] else None
    for raw in answer.answer_values:
        number = _float(raw)
        if number is None:
            return FieldValidationResult(False, f"数值字段答案 {raw!r} 不是有限数字。")
        if minimum is not None and number < minimum:
            return FieldValidationResult(False, f"数值 {number:g} 小于字段最小值 {minimum:g}。")
        if maximum is not None and number > maximum:
            return FieldValidationResult(False, f"数值 {number:g} 大于字段最大值 {maximum:g}。")
    return FieldValidationResult(True)


def _length_validation(contract: dict[str, Any], answer: ResolvedAnswer) -> FieldValidationResult:
    maxlength = contract.get("maxlength")
    if not isinstance(maxlength, int) or maxlength <= 0:
        return FieldValidationResult(True)
    for value in answer.answer_values:
        if len(value) > maxlength:
            return FieldValidationResult(False, f"答案长度 {len(value)} 超过字段 maxlength={maxlength}。")
    return FieldValidationResult(True)


def validate_resolved_answer(
    semantic_field: dict[str, Any],
    answer: ResolvedAnswer,
) -> FieldValidationResult:
    """Validate exact semantics against the one observed mechanical contract.

    The validator never guesses, rewrites, coerces or drops semantic information.
    AI/user/business/fallback sources all pass through the same contract before a
    field can become executable.
    """
    if answer.status != RESOLVED:
        return FieldValidationResult(True)
    if not answer.answer_values:
        return FieldValidationResult(False, "resolved 答案没有 answer_values。")

    contract = execution_contract(semantic_field)
    if not contract["supported"]:
        return FieldValidationResult(False, contract["reason"] or "当前 live field 没有受支持的可写控件。")
    if int(contract["value_control_count"]) <= 0:
        return FieldValidationResult(False, "resolved 答案没有当前 live value control。")

    unit_error = validate_qualifier(semantic_field, answer.qualifier)
    if unit_error:
        return FieldValidationResult(False, unit_error)

    key_names = {
        normalize_key(semantic_field.get("attribute_key")),
        normalize_key(semantic_field.get("label")),
    }
    if key_names & {"ean", "gtin", "barcode", "upc", "upca", "ean13", "gtin13"}:
        for value in answer.answer_values:
            if not is_valid_gtin(value):
                return FieldValidationResult(False, f"GTIN/EAN 校验失败：{value!r} 不是有效的 GTIN-8/12/13/14。")

    for validator in (_cardinality_validation, _closed_domain_validation, _numeric_validation, _length_validation):
        result = validator(contract, answer)
        if not result.valid:
            return result
    return FieldValidationResult(True)
