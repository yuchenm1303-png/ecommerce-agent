from __future__ import annotations

from openpyxl import Workbook

from app.answer_resolver import CONFLICT, MISSING, NEEDS_REVIEW, RESOLVED, resolve_field
from app.source_bundle import ProductSourceBundle, bundle_from_qa_file


def field(
    key: str,
    label: str,
    *,
    required: bool = True,
    multi_value: bool = False,
    options=None,
    controls=None,
):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": "Product Description (0/10)",
        "required": required,
        "multi_value": multi_value,
        "options": options or [],
        "controls": controls or [],
    }


def test_qa_workbook_loads_explicit_question_answer(tmp_path):
    path = tmp_path / "qa.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Question", "Explanation", "Answer"])
    sheet.append(["Model Number", "", "L11"])
    sheet.append(["Ports", "", "USB-C"])
    workbook.save(path)

    bundle = bundle_from_qa_file(path)

    assert len(bundle.evidence) == 2
    assert bundle.candidates(["model number"])[0].value == "L11"


def test_exact_structured_resolution():
    bundle = ProductSourceBundle(sku="ABC")
    bundle.add_evidence(
        key="Model Number",
        value="L11",
        source_type="structured",
        source_reference="products.xlsx:row=2",
        priority=10,
    )

    answer = resolve_field(field("model_number", "Model Number"), bundle)

    assert answer.status == RESOLVED
    assert answer.answer_values == ["L11"]
    assert answer.source_type == "structured"


def test_option_matching_is_exact_after_normalization():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Colour",
        value=" black ",
        source_type="customer_file",
        source_reference="qa.xlsx:row=3",
        priority=20,
    )
    semantic = field(
        "colour",
        "Colour",
        options=[
            {"text": "Select One", "value": "Select One"},
            {"text": "Black", "value": "Black"},
            {"text": "Blue", "value": "Blue"},
        ],
    )

    answer = resolve_field(semantic, bundle)

    assert answer.status == RESOLVED
    assert answer.answer_values == ["Black"]
    assert answer.option_match[0]["text"] == "Black"


def test_option_mismatch_requires_review():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Colour",
        value="Charcoal",
        source_type="customer_file",
        source_reference="qa.xlsx:row=3",
        priority=20,
    )
    semantic = field(
        "colour",
        "Colour",
        options=[{"text": "Black", "value": "Black"}, {"text": "Blue", "value": "Blue"}],
    )

    answer = resolve_field(semantic, bundle)

    assert answer.status == NEEDS_REVIEW
    assert "下拉选项" in answer.detail


def test_multi_value_is_resolved_once_as_array():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Sales Package",
        value="Camera|USB Cable|Manual",
        source_type="customer_file",
        source_reference="qa.xlsx:row=4",
        priority=20,
    )

    answer = resolve_field(
        field("sales_package", "Sales Package", multi_value=True), bundle
    )

    assert answer.status == RESOLVED
    assert answer.answer_values == ["Camera", "USB Cable", "Manual"]


def test_conflicting_explicit_sources_are_blocked():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Model Number",
        value="L11",
        source_type="structured",
        source_reference="table.xlsx:row=2",
        priority=10,
    )
    bundle.add_evidence(
        key="Model Number",
        value="L12",
        source_type="customer_file",
        source_reference="qa.xlsx:row=2",
        priority=20,
    )

    answer = resolve_field(field("model_number", "Model Number"), bundle)

    assert answer.status == CONFLICT
    assert "L11" in answer.detail and "L12" in answer.detail


def test_missing_evidence_is_not_guessed():
    answer = resolve_field(field("waterproof_depth", "Waterproof Depth"), ProductSourceBundle())

    assert answer.status == MISSING
    assert answer.answer is None


def test_business_field_rejects_non_structured_source():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Base Price",
        value="999",
        source_type="customer_file",
        source_reference="qa.xlsx:row=8",
        priority=20,
    )

    answer = resolve_field(field("mrp", "Base Price"), bundle)

    assert answer.status == NEEDS_REVIEW
    assert "经营字段" in answer.detail


def test_business_field_accepts_explicit_structured_source():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Base Price",
        value="999",
        source_type="structured",
        source_reference="products.xlsx:row=2:column=Base Price",
        priority=10,
    )

    answer = resolve_field(field("mrp", "Base Price"), bundle)

    assert answer.status == RESOLVED
    assert answer.answer_values == ["999"]


def test_value_plus_qualifier_is_parsed_from_allowed_qualifier():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Battery Life",
        value="3 Hours",
        source_type="customer_file",
        source_reference="qa.xlsx:row=9",
        priority=20,
    )
    semantic = field(
        "battery_life",
        "Battery Life",
        controls=[
            {"id": "battery_life", "name": "battery_life_0_value", "field_kind": "input", "options": []},
            {
                "id": "",
                "name": "battery_life_0_qualifier",
                "field_kind": "select",
                "options": [
                    {"text": "Minutes", "value": "Minutes"},
                    {"text": "Hours", "value": "Hours"},
                ],
            },
        ],
    )

    answer = resolve_field(semantic, bundle)

    assert answer.status == RESOLVED
    assert answer.answer_values == ["3"]
    assert answer.qualifier == "Hours"
