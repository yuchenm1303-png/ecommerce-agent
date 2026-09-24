from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .source_bundle import normalize_key


PRICE_DECISION_KEYS = frozenset({"mrp", "flipkart_selling_price"})

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
    """Return the canonical seller-decision key without product inference."""

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
    """Price fields require an explicit seller choice and never use placeholders."""

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
        return str(value or "").strip() or "未确认"
    rendered = f"{parsed:,.2f}".rstrip("0").rstrip(".")
    return f"{currency} {rendered}".strip()


def price_decision_advisory(
    field_or_key: object,
    *,
    confirmed_value: object = "",
    counterpart_value: object = "",
    currency: str = "R",
) -> dict[str, Any]:
    """Build display-only guidance for a seller-controlled price field.

    The payload is deliberately advisory. It never chooses a price and contains
    no guessed market data. Callers may add evidence-backed reference rows later
    without changing the HUD contract.
    """

    key = user_decision_business_key(field_or_key)
    if key not in PRICE_DECISION_KEYS:
        return {}

    value = _decimal(confirmed_value)
    counterpart = _decimal(counterpart_value)
    rows: list[dict[str, str]] = []

    if confirmed_value not in (None, ""):
        rows.append({"label": "当前确认", "value": _money(confirmed_value, currency=currency)})
    else:
        rows.append({"label": "当前确认", "value": "等待你决定"})

    if key == "mrp":
        title = "Base Price · 需要你决定"
        relation = "Base Price / MRP 应不低于 Selling Price"
        thought = (
            "这是卖家经营决策，不属于商品事实。程序不会再使用临时占位价；"
            "请结合采购成本、平台费用、目标毛利和你掌握的市场价格决定最终值。"
        )
        if counterpart_value not in (None, ""):
            rows.append(
                {
                    "label": "Selling Price",
                    "value": _money(counterpart_value, currency=currency),
                }
            )
    else:
        title = "Selling Price · 需要你决定"
        relation = "Selling Price 应不高于 Base Price / MRP"
        thought = (
            "这是最终成交价的卖家经营决策。程序只会写入你明确确认的值；"
            "建议同时检查采购成本、平台费用、目标毛利与市场参考，不会凭空生成市场价。"
        )
        if counterpart_value not in (None, ""):
            rows.append(
                {
                    "label": "Base Price / MRP",
                    "value": _money(counterpart_value, currency=currency),
                }
            )

    rows.append({"label": "关系约束", "value": relation})
    rows.append({"label": "自动占位", "value": "已禁用"})

    warning = ""
    if value is not None and counterpart is not None:
        selling = value if key == "flipkart_selling_price" else counterpart
        mrp = counterpart if key == "flipkart_selling_price" else value
        if mrp > 0:
            spread = mrp - selling
            discount = (spread / mrp) * Decimal("100")
            rows.append(
                {
                    "label": "价差",
                    "value": f"{_money(spread, currency=currency)} · {discount:.1f}%",
                }
            )
        if selling > mrp:
            warning = "当前 Selling Price 高于 Base Price / MRP，保存前必须调整。"

    return {
        "kind": "price_decision",
        "key": key,
        "title": title,
        "thought": thought,
        "rows": rows,
        "source": "用户决策 + Makro 经营约束",
        "warning": warning,
        "auto_write_without_user": False,
    }


__all__ = [
    "PRICE_DECISION_KEYS",
    "is_user_decision_business_field",
    "price_decision_advisory",
    "user_decision_business_key",
]
