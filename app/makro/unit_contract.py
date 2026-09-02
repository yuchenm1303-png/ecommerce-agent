from __future__ import annotations

from typing import Any

from ..live_field_contract import (
    fixed_rendered_unit,
    normalize_unit,
    qualifier_controls,
    units_equivalent,
    validate_qualifier,
)


def validate_answer_unit(semantic_field: dict[str, Any], answer_unit: object) -> str | None:
    """Compatibility facade over the canonical live execution contract."""
    return validate_qualifier(semantic_field, answer_unit)


__all__ = [
    "fixed_rendered_unit",
    "normalize_unit",
    "qualifier_controls",
    "units_equivalent",
    "validate_answer_unit",
]
