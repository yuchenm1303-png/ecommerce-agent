from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .source_bundle import normalize_key


_NUMBER_WITH_UNIT = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*([a-zA-Z°%]+(?:\s+[a-zA-Z]+)?)?\s*$"
)
_RESOLUTION = re.compile(r"^\s*(\d{2,5})\s*[x×*]\s*(\d{2,5})\s*$", re.IGNORECASE)

_UNIT_ALIASES = {
    "inch": "in",
    "inches": "in",
    "in": "in",
    "\"": "in",
    "millimeter": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "mm": "mm",
    "centimeter": "cm",
    "centimeters": "cm",
    "centimetre": "cm",
    "centimetres": "cm",
    "cm": "cm",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "m": "m",
    "gram": "g",
    "grams": "g",
    "g": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "kg": "kg",
    "hour": "h",
    "hours": "h",
    "hr": "h",
    "hrs": "h",
    "h": "h",
    "minute": "min",
    "minutes": "min",
    "mins": "min",
    "min": "min",
    "second": "s",
    "seconds": "s",
    "sec": "s",
    "secs": "s",
    "s": "s",
    "degree": "deg",
    "degrees": "deg",
    "deg": "deg",
    "°": "deg",
    "watt": "w",
    "watts": "w",
    "w": "w",
    "volt": "v",
    "volts": "v",
    "v": "v",
    "mah": "mah",
    "gb": "gb",
    "mb": "mb",
    "tb": "tb",
}

# Only direct, mechanically equivalent resolution names are normalized here.
# Marketing labels such as HD/FHD/2K/4K are deliberately not expanded because
# their exact pixel meaning varies by source/product context.
_RESOLUTION_FIELD_HINTS = {
    "imageresolution",
    "videoresolution",
    "recordingresolution",
    "resolution",
}


def _decimal_key(value: str) -> str | None:
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        return None
    if not decimal.is_finite():
        return None
    normalized = decimal.normalize()
    return format(normalized, "f")


def _unit_key(unit: str) -> str:
    compact = re.sub(r"\s+", " ", unit.strip()).casefold()
    return _UNIT_ALIASES.get(compact, compact)


def canonical_scalar_for_field(
    semantic_field: dict,
    value: str,
) -> str:
    """Canonicalize only representations known to be mechanically equivalent.

    This function intentionally avoids semantic synonym expansion. It may prove
    that ``3`` equals ``3.0``, ``3 inch`` equals ``3.0 inches`` or that different
    multiplication glyphs represent the same pixel dimensions. It must not infer
    that ``1080p`` equals ``1920x1080`` or that ``FHD`` equals either one.
    """

    text = re.sub(r"\s+", " ", str(value).strip()).casefold()
    if not text:
        return ""

    field_names = {
        normalize_key(semantic_field.get("attribute_key")),
        normalize_key(semantic_field.get("label")),
    }
    resolution_match = _RESOLUTION.match(text)
    if resolution_match and (field_names & _RESOLUTION_FIELD_HINTS):
        return f"px:{int(resolution_match.group(1))}x{int(resolution_match.group(2))}"

    numeric_match = _NUMBER_WITH_UNIT.match(text)
    if numeric_match:
        number = _decimal_key(numeric_match.group(1))
        if number is not None:
            unit = _unit_key(numeric_match.group(2) or "")
            return f"num:{number}:{unit}"

    return "text:" + text


def canonical_evidence_value_for_field(
    semantic_field: dict,
    value: str | tuple[str, ...],
) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return tuple(canonical_scalar_for_field(semantic_field, item) for item in value)
    return (canonical_scalar_for_field(semantic_field, value),)
