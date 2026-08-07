from __future__ import annotations

from app.answer_resolver import NEEDS_REVIEW, RESOLVED
from app.evidence_pipeline import add_fact
from app.fact_validators import is_valid_gtin
from app.resolution_engine import resolve_one
from app.source_bundle import ProductSourceBundle


def test_gtin_checksum_examples():
    assert is_valid_gtin("4006381333931") is True
    assert is_valid_gtin("4006381333932") is False
    assert is_valid_gtin("4") is False


def test_invalid_ean_is_blocked_before_browser_autofill():
    bundle = ProductSourceBundle()
    add_fact(
        bundle,
        key="EAN",
        value="4",
        source_type="structured",
        source_reference="products.xlsx:ean",
        confidence=1.0,
    )
    field = {
        "attribute_key": "ean",
        "label": "EAN",
        "controls": [{"field_kind": "input", "type": "text", "name": "ean_0_value"}],
    }

    record = resolve_one(field, bundle)

    assert record.status == NEEDS_REVIEW
    assert record.eligible_for_autofill is False
    assert "GTIN/EAN" in record.detail


def test_valid_ean_can_pass_validation():
    bundle = ProductSourceBundle()
    add_fact(
        bundle,
        key="EAN",
        value="4006381333931",
        source_type="structured",
        source_reference="products.xlsx:ean",
        confidence=1.0,
    )
    field = {
        "attribute_key": "ean",
        "label": "EAN",
        "controls": [{"field_kind": "input", "type": "text", "name": "ean_0_value"}],
    }

    record = resolve_one(field, bundle)

    assert record.status == RESOLVED
    assert record.eligible_for_autofill is True


def test_number_outside_live_control_range_is_blocked():
    bundle = ProductSourceBundle()
    add_fact(
        bundle,
        key="Battery Life",
        value="99",
        source_type="structured",
        source_reference="products.xlsx:battery",
        confidence=1.0,
    )
    field = {
        "attribute_key": "battery_life",
        "label": "Battery Life",
        "controls": [
            {
                "field_kind": "input",
                "type": "number",
                "name": "battery_life_0_value",
                "min": "1",
                "max": "24",
            }
        ],
    }

    record = resolve_one(field, bundle)

    assert record.status == NEEDS_REVIEW
    assert record.eligible_for_autofill is False
    assert "最大值" in record.detail
