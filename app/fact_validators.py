from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .answer_resolver import RESOLVED, ResolvedAnswer
from .source_bundle import normalize_key


@dataclass(slots=True, frozen=True)
class FieldValidationResult:
    valid: bool
    detail: str = ""


@dataclass(slots=True, frozen=True)
class SynthesisVerificationResult:
    verified: bool
    detail: str = ""


def is_valid_gtin(value: str) -> bool:
    """Validate GTIN-8/UPC-A/GTIN-13/GTIN-14 check digit."""

    digits = "".join(value.split())
    if not digits.isdigit() or len(digits) not in {8, 12, 13, 14}:
        return False
    body = digits[:-1]
    expected = int(digits[-1])
    total = 0
    # Starting from the rightmost body digit, weights are 3,1,3,1...
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
    """Apply deterministic domain/control validation before browser writes."""

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


# The rules below are deliberately much narrower than general language/model
# inference. They exist so a low-confidence ai_synthesis answer can be promoted
# only when ordinary code can recompute the same mapping from its grounded
# evidence. Anything outside this allowlist remains under the AI confidence gate.
_COLOUR_ALIASES: dict[str, tuple[str, ...]] = {
    "black": ("black", "黑色"),
    "white": ("white", "白色"),
    "red": ("red", "红色"),
    "blue": ("blue", "蓝色"),
    "green": ("green", "绿色"),
    "gray": ("gray", "grey", "灰色"),
    "grey": ("gray", "grey", "灰色"),
    "silver": ("silver", "银色"),
    "gold": ("gold", "金色"),
}
_DUAL_CAMERA_MARKERS = (
    "dual lens",
    "dual camera",
    "dual cameras",
    "front + cabin",
    "front and cabin",
    "front+cabin",
    "双镜头",
    "双摄",
    "双摄像头",
)
_INCLUDED_MARKERS = (
    "included",
    "includes",
    "in the box",
    "package includes",
    "package contains",
    "标配",
    "包装清单",
    "配件清单",
    "内含",
)
_BRACKET_MARKERS = ("bracket", "mounting bracket", "mount", "支架")
_DUAL_RECORDING_MARKERS = (
    "dual recording",
    "dual record",
    "front + cabin",
    "front and cabin",
    "front+cabin",
    "双录",
    "前后录像",
    "前后录",
)
_G_SENSOR_MARKERS = ("g-sensor", "g sensor", "碰撞感应", "重力感应")
_LITERAL_VALUE_FIELDS = {
    "otherconnectivityfeatures",
    "usbtypesupported",
    "framerate",
    "videoformats",
}


def _field_names(semantic_field: dict[str, Any]) -> set[str]:
    return {
        normalize_key(semantic_field.get("attribute_key")),
        normalize_key(semantic_field.get("label")),
    } - {""}


def _evidence_text(answer: ResolvedAnswer) -> str:
    return str(answer.evidence or "").casefold()


def _all_values_literal(answer: ResolvedAnswer, evidence: str) -> bool:
    normalized_evidence = normalize_key(evidence)
    if not normalized_evidence:
        return False
    for value in answer.answer_values:
        normalized = normalize_key(value)
        if not normalized or normalized not in normalized_evidence:
            return False
    return True


def _yes(answer: ResolvedAnswer) -> bool:
    return len(answer.answer_values) == 1 and normalize_key(answer.answer_values[0]) in {
        "yes",
        "true",
        "1",
    }


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def verify_deterministic_synthesis(
    semantic_field: dict[str, Any],
    answer: ResolvedAnswer,
) -> SynthesisVerificationResult:
    """Verify a tiny allowlist of source-grounded semantic transforms.

    This does *not* raise the trust of arbitrary AI synthesis. It only recognizes
    transformations with a deterministic inverse check. The original
    ``source_type=ai_synthesis``, confidence and provenance remain unchanged for
    auditability.
    """

    if answer.status != RESOLVED or answer.source_type != "ai_synthesis":
        return SynthesisVerificationResult(False)
    if not answer.answer_values or not (answer.source_reference or "").strip():
        return SynthesisVerificationResult(False)

    names = _field_names(semantic_field)
    evidence = _evidence_text(answer)
    if not evidence:
        return SynthesisVerificationResult(False)

    if names & {"numberofcameras", "numberofcamera", "cameracount"}:
        if (
            len(answer.answer_values) == 1
            and normalize_key(answer.answer_values[0]) == "2"
            and _contains_any(evidence, _DUAL_CAMERA_MARKERS)
        ):
            return SynthesisVerificationResult(
                True,
                "deterministic transform verified: explicit dual-camera evidence => camera count 2",
            )

    if names & {"packof", "packsize", "packquantity"}:
        if (
            len(answer.answer_values) == 1
            and normalize_key(answer.answer_values[0]) == "1"
            and re.search(r"(?<!\d)1\s*[×xX*](?!\d)", evidence)
        ):
            return SynthesisVerificationResult(
                True,
                "deterministic transform verified: explicit 1× selected pack => Pack of 1",
            )

    if names & {"colour", "color"} and len(answer.answer_values) == 1:
        wanted = normalize_key(answer.answer_values[0])
        aliases = _COLOUR_ALIASES.get(wanted, ())
        if aliases and any(alias.casefold() in evidence for alias in aliases):
            return SynthesisVerificationResult(
                True,
                "deterministic transform verified: reviewed colour synonym maps to the selected enum",
            )

    if names & _LITERAL_VALUE_FIELDS and _all_values_literal(answer, evidence):
        return SynthesisVerificationResult(
            True,
            "deterministic transform verified: every answer value is literally present in scoped evidence",
        )

    if names & {"mountingbracketincluded", "bracketincluded"} and _yes(answer):
        if _contains_any(evidence, _BRACKET_MARKERS) and _contains_any(
            evidence, _INCLUDED_MARKERS
        ):
            return SynthesisVerificationResult(
                True,
                "deterministic transform verified: package evidence explicitly includes a mounting bracket",
            )

    if names & {"dualrecording", "dualrecord", "simultaneousrecording"} and _yes(answer):
        if _contains_any(evidence, _DUAL_RECORDING_MARKERS):
            return SynthesisVerificationResult(
                True,
                "deterministic transform verified: evidence explicitly states dual recording",
            )

    if names & {"gsensor", "gsensorincluded", "collisionsensor"} and _yes(answer):
        if _contains_any(evidence, _G_SENSOR_MARKERS):
            return SynthesisVerificationResult(
                True,
                "deterministic transform verified: evidence explicitly states G-Sensor/collision sensing",
            )

    return SynthesisVerificationResult(False)
