from __future__ import annotations

from app.evidence_pipeline import add_fact
from app.resolution_engine import GATE_BUSINESS_SOURCE, resolve_one
from app.source_bundle import ProductSourceBundle


def _stock_field():
    return {
        "attribute_key": "stock",
        "label": "Stock",
        "section_heading": "Price, Stock and Shipping Information",
        "required": True,
        "multi_value": False,
        "controls": [],
    }


def test_unstructured_customer_file_cannot_authorize_stock():
    bundle = ProductSourceBundle()
    add_fact(
        bundle,
        key="Stock",
        value="25",
        source_type="customer_file",
        source_reference="notes.txt:line=1",
        confidence=0.99,
        evidence_text="Stock: 25",
    )

    result = resolve_one(_stock_field(), bundle)

    assert result.status == "needs_review"
    assert result.eligible_for_autofill is False
    assert result.preview_eligible is False
    assert result.gate_reason == GATE_BUSINESS_SOURCE


def test_structured_stock_remains_allowed():
    bundle = ProductSourceBundle()
    add_fact(
        bundle,
        key="Stock",
        value="25",
        source_type="structured",
        source_reference="seller-config.xlsx:row=2",
        confidence=0.99,
        evidence_text="Stock: 25",
    )

    result = resolve_one(_stock_field(), bundle)

    assert result.status == "resolved"
    assert result.eligible_for_autofill is True
    assert result.answer_values == ["25"]
