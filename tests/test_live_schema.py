from __future__ import annotations

import pytest

from app.live_schema import (
    assert_live_schema_matches,
    augment_catalog_with_live_fields,
    live_schema_payload,
)
from app.qa_catalog import QuestionCatalog, QuestionRecord


def _catalog():
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[QuestionRecord(number="1", question="Model Number")],
    )


def _field(key, label, section="Price, Stock and Shipping Information", required=True):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section,
        "required": required,
        "options": [],
        "controls": [],
    }


def test_live_schema_adds_non_business_field_missing_from_customer_qa():
    fields = [
        _field("model_number", "Model Number", "Product Description"),
        _field("package_length", "Length"),
        _field("stock", "Stock"),
    ]

    augmented, warnings = augment_catalog_with_live_fields(
        _catalog(),
        fields,
        business_locked=lambda value: str(value).casefold() == "stock",
    )

    assert [item.question for item in augmented.questions] == ["Model Number", "Length"]
    assert augmented.questions[-1].number.startswith("LIVE-")
    assert augmented.questions[-1].extra["attribute_key"] == "package_length"
    assert any(item.startswith("business_locked:") for item in warnings)


def test_duplicate_uncovered_label_without_unique_key_is_not_exposed_to_ai():
    fields = [
        _field("height", "Height", "Additional Description"),
        _field("height", "Height", "Price, Stock and Shipping Information"),
    ]

    augmented, warnings = augment_catalog_with_live_fields(
        _catalog(), fields, business_locked=lambda value: False
    )

    assert len(augmented.questions) == 1
    assert sum(item.startswith("no_unique_evidence_key:") for item in warnings) == 2


def test_live_schema_drift_fails_before_browser_write():
    planned = live_schema_payload([_field("package_length", "Length")])["fields"]
    current = [_field("package_length", "Length", required=False)]

    with pytest.raises(RuntimeError, match="live schema 与当前 Makro 页面不一致"):
        assert_live_schema_matches(planned, current)


def test_live_schema_ignores_dom_values_and_paths_in_drift_check():
    field = _field("package_length", "Length")
    planned = live_schema_payload([field])["fields"]
    current = [{**field, "value": "16", "path": "body > div:nth-child(99)"}]

    assert_live_schema_matches(planned, current)
