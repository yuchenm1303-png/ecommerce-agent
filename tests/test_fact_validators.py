from __future__ import annotations

from app.hard_field_validators import is_valid_gtin, validate_resolved_answer
from app.resolution_types import RESOLVED, ResolvedAnswer


def answer(value: str) -> ResolvedAnswer:
    return ResolvedAnswer(
        attribute_key="test",
        label="Test",
        status=RESOLVED,
        answer=value,
        answer_values=[value],
        confidence=1.0,
    )


def test_gtin_checksum_examples():
    assert is_valid_gtin("4006381333931") is True
    assert is_valid_gtin("4006381333932") is False
    assert is_valid_gtin("4") is False


def test_invalid_ean_is_rejected_by_hard_validator():
    field = {
        "attribute_key": "ean",
        "label": "EAN",
        "controls": [{"field_kind": "input", "type": "text", "name": "ean_0_value"}],
    }

    result = validate_resolved_answer(field, answer("4"))

    assert result.valid is False
    assert "GTIN/EAN" in result.detail


def test_valid_ean_passes_hard_validator():
    field = {
        "attribute_key": "ean",
        "label": "EAN",
        "controls": [{"field_kind": "input", "type": "text", "name": "ean_0_value"}],
    }

    result = validate_resolved_answer(field, answer("4006381333931"))

    assert result.valid is True


def test_number_outside_live_control_range_is_rejected():
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

    result = validate_resolved_answer(field, answer("99"))

    assert result.valid is False
    assert "最大值" in result.detail


def test_non_numeric_value_is_rejected_for_numeric_control():
    field = {
        "attribute_key": "weight",
        "label": "Weight",
        "controls": [
            {
                "field_kind": "input",
                "type": "number",
                "name": "weight_0_value",
            }
        ],
    }

    result = validate_resolved_answer(field, answer("heavy"))

    assert result.valid is False
    assert "有限数字" in result.detail


def test_maxlength_is_a_hard_marketplace_constraint():
    field = {
        "attribute_key": "model_name",
        "label": "Model Name",
        "controls": [
            {
                "field_kind": "input",
                "type": "text",
                "name": "model_name_0_value",
                "maxlength": 5,
            }
        ],
    }

    result = validate_resolved_answer(field, answer("TOO-LONG"))

    assert result.valid is False
    assert "maxlength=5" in result.detail
