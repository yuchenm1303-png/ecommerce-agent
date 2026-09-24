from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .source_bundle import normalize_key


PRICE_DECISION_KEYS = frozenset({"mrp", "flipkart_selling_price"})
PRICE_REFERENCE_DEFAULTS = {
    "mrp": "6000",
    "flipkart_selling_price": "5000",
}

_PRICE_ALIASES: dict[str, tuple[str, ...]] = {
    "mrp": (
        "mrp",
        "base price",
        "base_price",
        "原价",
        "基础价格",
    ),
    "flipkart_selling_price": (
        "selling price",
        "your selling price",
        "flipkart_selling_price",
        "售价",
        "销售价",
    ),
}

_NORMALIZED_PRICE_ALIASES = {
    normalize_key(alias): key
    for key, aliases in _PRICE_ALIASES.items()
    for alias in (key, *aliases)
}


def user_decision_business_key(field_or_key: object) -> str:
    """Compatibility name: return the canonical price-business key."""

    if isinstance(field_or_key, dict):
        candidates = (
            field_or_key.get("attribute_key"),
            field_or_key.get("label"),
        )
    else:
        candidates = (field_or_key,)

    for candidate in candidates:
        normalized = normalize_key(candidate)
        if normalized in _NORMALIZED_PRICE_ALIASES:
            return _NORMALIZED_PRICE_ALIASES[normalized]
    return ""


def is_user_decision_business_field(field_or_key: object) -> bool:
    """Compatibility helper retained for callers/tests; price remains auto-filled."""

    return user_decision_business_key(field_or_key) in PRICE_DECISION_KEYS


def _decimal(value: object) -> Decimal | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _money(value: object, *, currency: str = "R") -> str:
    parsed = _decimal(value)
    if parsed is None:
        return str(value or "").strip() or "—"
    rendered = f"{parsed:,.2f}".rstrip("0").rstrip(".")
    return f"{currency} {rendered}".strip()


def price_reference_advisory(
    field_or_key: object,
    *,
    confirmed_value: object = "",
    counterpart_value: object = "",
    currency: str = "R",
) -> dict[str, Any]:
    """Build a display-only reference card for the established auto-filled prices.

    This function never changes Fill Plan readiness, browser writes, Save policy,
    or execution flow. The 6000/5000 values are the existing seller operating
    defaults; the card explains them instead of turning them into a human gate.
    """

    key = user_decision_business_key(field_or_key)
    if key not in PRICE_DECISION_KEYS:
        return {}

    current_raw = confirmed_value or PRICE_REFERENCE_DEFAULTS[key]
    counterpart_key = "flipkart_selling_price" if key == "mrp" else "mrp"
    counterpart_raw = counterpart_value or PRICE_REFERENCE_DEFAULTS[counterpart_key]
    current = _decimal(current_raw)
    counterpart = _decimal(counterpart_raw)

    if key == "mrp":
        title = "Base Price · 价格参考"
        paired_label = "Selling Price 参考"
        relation = "Base Price / MRP 不低于 Selling Price"
    else:
        title = "Selling Price · 价格参考"
        paired_label = "Base Price / MRP 参考"
        relation = "Selling Price 不高于 Base Price / MRP"

    rows: list[dict[str, str]] = [
        {"label": "参考填写", "value": _money(current_raw, currency=currency)},
        {"label": paired_label, "value": _money(counterpart_raw, currency=currency)},
    ]

    mrp = current if key == "mrp" else counterpart
    selling = counterpart if key == "mrp" else current
    warning = ""
    if mrp is not None and selling is not None and mrp > 0:
        spread = mrp - selling
        discount = (spread / mrp) * Decimal("100")
        rows.append(
            {
                "label": "价格结构",
                "value": f"价差 {_money(spread, currency=currency)} · {discount:.1f}% 折扣",
            }
        )
        if selling > mrp:
            warning = "当前 Selling Price 高于 Base Price / MRP，建议调整价格关系。"

    rows.append({"label": "关系参考", "value": relation})

    return {
        "kind": "price_reference",
        "key": key,
        "title": title,
        "eyebrow": "经营参考",
        "thought": (
            f"系统会继续按原流程自动填写 {_money(current_raw, currency=currency)}，不会暂停等待。"
            " 这是当前经营参考值，不是实时市场报价；可按实际成本、平台费用和销售策略修改。"
        ),
        "rows": rows,
        "source": "Listing Studio · 当前经营参数",
        "warning": warning,
        "auto_write_without_user": True,
        "persistent": True,
    }


def price_decision_advisory(
    field_or_key: object,
    *,
    confirmed_value: object = "",
    counterpart_value: object = "",
    currency: str = "R",
) -> dict[str, Any]:
    """Backward-compatible alias for the non-blocking price reference card."""

    return price_reference_advisory(
        field_or_key,
        confirmed_value=confirmed_value,
        counterpart_value=counterpart_value,
        currency=currency,
    )


__all__ = [
    "PRICE_DECISION_KEYS",
    "PRICE_REFERENCE_DEFAULTS",
    "is_user_decision_business_field",
    "price_decision_advisory",
    "price_reference_advisory",
    "user_decision_business_key",
]
