from __future__ import annotations

import pytest

from app.live_schema import assert_live_schema_matches, live_schema_payload


def _field(key, label, section="Price, Stock and Shipping Information", required=True):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section,
        "required": required,
        "options": [],
        "controls": [],
    }


def test_live_schema_serializes_current_fields_directly_without_qa_augmentation():
    fields = [
        _field("model_number", "Model Number", "Product Description"),
        _field("package_length", "Length"),
        _field("stock", "Stock"),
    ]

    payload = live_schema_payload(fields)

    assert [item["label"] for item in payload["fields"]] == [
        "Model Number",
        "Length",
        "Stock",
    ]
    assert payload["fields"][1]["attribute_key"] == "package_length"


def test_live_schema_drift_fails_before_browser_write():
    planned = live_schema_payload([_field("package_length", "Length")])["fields"]
    current = [_field("package_length", "Length", required=False)]

    with pytest.raises(RuntimeError, match="live schema 与当前 Makro 页面执行合同不一致"):
        assert_live_schema_matches(planned, current)


def test_live_schema_ignores_dom_values_and_paths_in_drift_check():
    field = _field("package_length", "Length")
    planned = live_schema_payload([field])["fields"]
    current = [{**field, "value": "16", "path": "body > div:nth-child(99)"}]

    assert_live_schema_matches(planned, current)


def test_live_schema_preserves_qualifier_options_after_serialization():
    field = {
        **_field("package_length", "Length"),
        "controls": [
            {
                "name": "package_length_qualifier",
                "options": [
                    {"text": "cm", "value": "cm"},
                    {"text": "mm", "value": "mm"},
                    {"text": "inch", "value": "inch"},
                ],
            }
        ],
    }
    planned = live_schema_payload([field])["fields"]

    assert planned[0]["qualifier_options"] == ["cm", "mm", "inch"]
    assert_live_schema_matches(planned, [field])


def test_live_schema_preserves_nearby_context_for_fixed_unit_inputs():
    field = {
        **_field("length", "Length"),
        "controls": [
            {
                "id": "length",
                "name": "length_0_value",
                "type": "number",
                "field_kind": "input",
                "context_text": "Length * cm",
            }
        ],
    }
    planned = live_schema_payload([field])["fields"]
    assert planned[0]["context_text"] == "Length * cm"
    assert planned[0]["qualifier_options"] == []
    assert planned[0]["execution_contract"]["qualifier_mode"] == "fixed"
    assert planned[0]["execution_contract"]["fixed_unit"] == "cm"

    # Context wording is AI input, not schema identity: churn that leaves the
    # mechanical contract unchanged is not drift.
    unchanged = [
        {**field, "controls": [{**field["controls"][0], "context_text": "Length *cm | Enter a value"}]}
    ]
    assert_live_schema_matches(planned, unchanged)

    # The rendered fixed unit is part of the write contract, so losing it is drift
    # and must fail closed before any browser write.
    unit_lost = [{**field, "controls": [{**field["controls"][0], "context_text": "Length cm"}]}]
    with pytest.raises(RuntimeError, match="执行合同不一致"):
        assert_live_schema_matches(planned, unit_lost)
