from __future__ import annotations

from types import SimpleNamespace

from app.fill_plan import BLOCKED, READY
from app.makro.user_decisions import pending_user_decision_items, trusted_input_ready


def _item(
    key: str,
    label: str,
    *,
    action: str = BLOCKED,
    required: bool = True,
    section: str = "Price, Stock and Shipping Information",
):
    return SimpleNamespace(
        attribute_key=key,
        label=label,
        action=action,
        required=required,
        section_heading=section,
        resolution=SimpleNamespace(
            source_type="",
            answer_values=[],
            answer="",
        ),
    )


def test_pending_runtime_decisions_include_only_blocked_required_prices() -> None:
    plan = SimpleNamespace(
        items=[
            _item("mrp", "Base Price"),
            _item("flipkart_selling_price", "Your selling price"),
            _item("minimum_order_quantity", "Minimum Order Quantity"),
            _item("ean", "EAN"),
            _item("mrp", "Base Price", action=READY),
            _item("flipkart_selling_price", "Your selling price", required=False),
        ]
    )

    pending = pending_user_decision_items(plan)

    assert [(item.attribute_key, item.label) for item in pending] == [
        ("mrp", "Base Price"),
        ("flipkart_selling_price", "Your selling price"),
    ]


def test_pending_runtime_decisions_respect_single_section_scope() -> None:
    plan = SimpleNamespace(
        items=[
            _item("mrp", "Base Price"),
            _item(
                "flipkart_selling_price",
                "Your selling price",
                section="Product Description",
            ),
        ]
    )

    pending = pending_user_decision_items(
        plan,
        section="Price, Stock and Shipping Information (0/8)",
    )

    assert len(pending) == 1
    assert pending[0].attribute_key == "mrp"


def test_trusted_input_waits_for_stable_value_or_commit_event() -> None:
    assert trusted_input_ready(
        {"touched": True, "event_type": "input", "stable_for_ms": 120},
        stable_ms=900,
    ) is False
    assert trusted_input_ready(
        {"touched": True, "event_type": "input", "stable_for_ms": 900},
        stable_ms=900,
    ) is True
    assert trusted_input_ready(
        {"touched": True, "event_type": "change", "stable_for_ms": 0},
        stable_ms=900,
    ) is True
    assert trusted_input_ready(
        {"touched": True, "event_type": "blur", "stable_for_ms": 0},
        stable_ms=900,
    ) is True
    assert trusted_input_ready(
        {"touched": False, "event_type": "change", "stable_for_ms": 9999},
        stable_ms=900,
    ) is False
