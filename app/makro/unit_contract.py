from __future__ import annotations

import re
from typing import Any


_QUALIFIER_NAME_RE = re.compile(r"_qualifier$")
_FIXED_UNIT_SUFFIX_RE = re.compile(
    r"^\*+\s*(?P<unit>[A-Za-zµμ°%]+(?:[./-][A-Za-zµμ°%]+)*(?:\s+[A-Za-zµμ°%]+(?:[./-][A-Za-zµμ°%]+)*){0,2})\s*$"
)
_UNIT_TOKEN_RE = re.compile(r"[^a-z0-9µμ°%]+")

_UNIT_ALIASES = {
    "centimeter": "cm",
    "centimeters": "cm",
    "centimetre": "cm",
    "centimetres": "cm",
    "millimeter": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "in": "inch",
    "inches": "inch",
    "kilogram": "kg",
    "kilograms": "kg",
    "gram": "g",
    "grams": "g",
    "milligram": "mg",
    "milligrams": "mg",
    "pound": "lb",
    "pounds": "lb",
    "lbs": "lb",
    "watt": "w",
    "watts": "w",
    "kilowatt": "kw",
    "kilowatts": "kw",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "days": "day",
    "degreescelsius": "°c",
    "degreecelsius": "°c",
    "celsius": "°c",
    "c": "°c",
    "degreesfahrenheit": "°f",
    "degreefahrenheit": "°f",
    "fahrenheit": "°f",
    "f": "°f",
}


def qualifier_controls(semantic_field: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only real live controls that can mutate a qualifier/unit.

    Static rendered suffixes such as ``CM``/``KG`` are deliberately excluded.
    They are part of the value contract but are not browser controls.
    """

    return [
        control
        for control in semantic_field.get("controls") or []
        if _QUALIFIER_NAME_RE.search(str(control.get("name") or ""))
    ]


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


def _candidate_contexts(semantic_field: dict[str, Any]) -> list[str]:
    output: list[str] = []
    for raw in [
        semantic_field.get("context_text"),
        *(control.get("context_text") for control in semantic_field.get("controls") or []),
    ]:
        text = " ".join(str(raw or "").split()).strip()
        if text and text not in output:
            output.append(text)
    return output


def _candidate_labels(semantic_field: dict[str, Any]) -> list[str]:
    output: list[str] = []
    for raw in (semantic_field.get("label"), semantic_field.get("rendered_label")):
        text = " ".join(str(raw or "").split()).strip()
        if text and text not in output:
            output.append(text)
    return output


def fixed_rendered_unit(semantic_field: dict[str, Any]) -> str:
    """Return a fixed unit rendered next to the value input, if one is explicit.

    Makro's fixed-unit fields currently render their mandatory marker and unit in
    the same local text fragment as the label, e.g. ``Breadth *CM`` or
    ``Weight *KG``. We only accept that local suffix shape. We do not mine arbitrary
    card text, options, help text, or neighbouring fields, so an unrelated token can
    never silently become the execution unit.
    """

    if qualifier_controls(semantic_field):
        return ""

    labels = _candidate_labels(semantic_field)
    if not labels:
        return ""

    for context in _candidate_contexts(semantic_field):
        local = context.split("|", 1)[0].strip()
        for label in labels:
            if not local.casefold().startswith(label.casefold()):
                continue
            suffix = local[len(label) :].strip()
            match = _FIXED_UNIT_SUFFIX_RE.fullmatch(suffix)
            if match:
                return match.group("unit").strip()
    return ""


def validate_answer_unit(semantic_field: dict[str, Any], answer_unit: object) -> str | None:
    """Validate an answer unit against the live field without mutating the DOM.

    A selectable qualifier is validated by the existing qualifier-control path.
    A fixed rendered unit must be semantically equivalent to the answer unit. If a
    unit-bearing answer reaches a field with neither contract, fail closed rather
    than dropping unit information.
    """

    expected = str(answer_unit or "").strip()
    if not expected:
        return None
    if qualifier_controls(semantic_field):
        return None

    fixed = fixed_rendered_unit(semantic_field)
    if not fixed:
        return "答案包含 qualifier，但当前 live field 既没有 qualifier control，也没有可确认的固定单位；未执行写入。"
    if not units_equivalent(expected, fixed):
        return (
            "答案单位与页面固定单位冲突；未执行写入："
            f"answer_qualifier={expected!r}, fixed_unit={fixed!r}。"
        )
    return None


__all__ = [
    "fixed_rendered_unit",
    "normalize_unit",
    "qualifier_controls",
    "units_equivalent",
    "validate_answer_unit",
]
