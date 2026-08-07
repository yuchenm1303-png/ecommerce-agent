from __future__ import annotations

import re
from collections.abc import Mapping

from .models import MatchResult


FIELD_ALIASES: dict[str, set[str]] = {
    "brand": {"品牌", "品牌名称", "brand", "manufacturerbrand"},
    "rated_power": {"额定功率", "功率", "ratedpower", "ratedwattage"},
    "color_temperature": {"色温", "色温值", "colortemperature", "colourtemperature", "cct"},
    "ip_rating": {"防水等级", "防护等级", "ip等级", "iprating", "ingressprotection"},
    "housing_material": {"外壳材质", "外壳材料", "壳体材质", "housingmaterial", "shellmaterial"},
}


def normalize_text(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[\s_\-—–:：/\\()（）\[\]【】]+", "", value)
    return value


_ALIAS_TO_CANONICAL = {
    normalize_text(alias): canonical
    for canonical, aliases in FIELD_ALIASES.items()
    for alias in aliases
}


def canonicalize(value: str) -> str:
    normalized = normalize_text(value)
    return _ALIAS_TO_CANONICAL.get(normalized, normalized)


def match_answer(field_label: str, product_values: Mapping[str, str]) -> MatchResult | None:
    """Match conservatively: exact normalized header first, then a known alias family.

    We intentionally do not use fuzzy matching in v1. A missed field is safer than a
    wrong product attribute being submitted to a real seller backend.
    """

    field_normalized = normalize_text(field_label)
    field_canonical = canonicalize(field_label)

    for header, value in product_values.items():
        if not value:
            continue
        if normalize_text(header) == field_normalized:
            return MatchResult(
                source_header=header,
                answer=value,
                strategy="exact",
                confidence=1.0,
            )

    for header, value in product_values.items():
        if not value:
            continue
        if canonicalize(header) == field_canonical and field_canonical in FIELD_ALIASES:
            return MatchResult(
                source_header=header,
                answer=value,
                strategy="alias",
                confidence=0.98,
            )

    return None
