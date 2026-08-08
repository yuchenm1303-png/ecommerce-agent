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


def test_generic_multi_camera_category_does_not_conflict_with_exact_count_two():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Number of Cameras",
        value="多镜头",
        source_type="product_image",
        source_reference="image:001",
        priority=30,
        evidence_text="镜头数量 多镜头",
    )
    bundle.add_evidence(
        key="Number of Cameras",
        value="2",
        source_type="ai_synthesis",
        source_reference="customer-text:001:text:0001:abc",
        priority=90,
        confidence=0.84,
        evidence_text="front + cabin dual recording",
    )

    answer = resolve_field(field("number_of_cameras", "Number of Cameras"), bundle)

    assert answer.status == RESOLVED
    assert answer.answer_values == ["2"]


def test_storage_expandability_text_is_not_a_capacity_value():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Storage Capacity",
        value="Expandable via TF Card",
        source_type="ai_synthesis",
        source_reference="image:001",
        priority=90,
        confidence=0.84,
        evidence_text="存储卡 TF卡",
    )
    bundle.add_evidence(
        key="Storage Capacity",
        value="64",
        source_type="customer_file",
        source_reference="customer-text:001:text:0001:abc",
        priority=60,
        confidence=0.9,
        evidence_text="64GB memory card",
    )

    answer = resolve_field(field("storage_capacity", "Storage Capacity"), bundle)

    assert answer.status == RESOLVED
    assert answer.answer_values == ["64"]


def test_product_dimension_ignores_explicit_packaging_dimension_evidence():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Width",
        value="11 cm",
        source_type="supplier_web",
        source_reference="supplier:001:text:0001:abc",
        priority=55,
        evidence_text="Packaging dimensions 16 x 11 x 7 cm",
    )
    bundle.add_evidence(
        key="Width",
        value="86 mm",
        source_type="product_image",
        source_reference="image:002",
        priority=30,
        evidence_text="Product width 86 mm",
    )

    answer = resolve_field(field("width", "Width"), bundle)

    assert answer.status == RESOLVED
    assert answer.answer_values == ["86 mm"]


def test_set_like_field_accepts_existing_superset_but_does_not_synthesize_union():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Technology Used",
        value="G-Sensor|Loop Recording",
        source_type="product_image",
        source_reference="image:002",
        priority=30,
        evidence_text="G-Sensor, Loop Recording",
    )
    bundle.add_evidence(
        key="Technology Used",
        value="G-Sensor",
        source_type="supplier_web",
        source_reference="supplier:001:text:0001:abc",
        priority=55,
        evidence_text="G-Sensor",
    )

    answer = resolve_field(field("technology_used", "Technology Used", multi_value=True), bundle)

    assert answer.status == RESOLVED
    assert answer.answer_values == ["G-Sensor", "Loop Recording"]


def test_set_like_field_with_divergent_non_subset_values_remains_conflict():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Technology Used",
        value="G-Sensor|Loop Recording",
        source_type="product_image",
        source_reference="image:002",
        priority=30,
    )
    bundle.add_evidence(
        key="Technology Used",
        value="G-Sensor|HDR",
        source_type="supplier_web",
        source_reference="supplier:001:text:0001:abc",
        priority=55,
    )

    answer = resolve_field(field("technology_used", "Technology Used", multi_value=True), bundle)

    assert answer.status == CONFLICT


def test_true_recording_resolution_disagreement_remains_conflict():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Recording Resolution",
        value="720p",
        source_type="supplier_web",
        source_reference="supplier:001:text:0001:abc",
        priority=55,
    )
    bundle.add_evidence(
        key="Recording Resolution",
        value="1080p",
        source_type="product_image",
        source_reference="image:002",
        priority=30,
    )

    answer = resolve_field(field("recording_resolution", "Recording Resolution"), bundle)

    assert answer.status == CONFLICT


def test_generic_product_brand_cannot_answer_vehicle_brand():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Vehicle Brand",
        value="other",
        source_type="ai_synthesis",
        source_reference="image:001",
        priority=90,
        confidence=0.84,
        evidence_text="品牌 other",
    )

    answer = resolve_field(field("vehicle_brand", "Vehicle Brand"), bundle)

    assert answer.status == MISSING


def test_manual_languages_cannot_answer_device_languages_supported():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Languages Supported",
        value="English|German",
        source_type="ai_synthesis",
        source_reference="customer-text:001:text:0001:abc",
        priority=90,
        confidence=0.84,
        evidence_text="English + German manual included",
    )

    answer = resolve_field(field("languages_supported", "Languages Supported", multi_value=True), bundle)

    assert answer.status == MISSING


def test_reverse_assist_feature_cannot_prove_reverse_camera_type():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Camera Type",
        value="Dashboard + Reverse Assist",
        source_type="ai_synthesis",
        source_reference="image:001",
        priority=90,
        confidence=0.84,
        evidence_text="功能 倒车影像, 循环录像, 碰撞感应",
    )
    bundle.add_evidence(
        key="Camera Type",
        value="Dashboard + In-Car",
        source_type="ai_synthesis",
        source_reference="image:002",
        priority=90,
        confidence=0.84,
        evidence_text="Front + cabin dual lens dash cam",
    )

    answer = resolve_field(field("camera_type", "Camera Type"), bundle)

    assert answer.status == RESOLVED
    assert answer.answer_values == ["Dashboard + In-Car"]


def test_no_internal_memory_cannot_prove_sd_card_not_included():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="SD Card Included",
        value="No",
        source_type="ai_synthesis",
        source_reference="image:001",
        priority=90,
        confidence=0.84,
        evidence_text="内存容量 无",
    )
    bundle.add_evidence(
        key="SD Card Included",
        value="Yes",
        source_type="customer_file",
        source_reference="customer-text:001:text:0001:abc",
        priority=60,
        confidence=0.99,
        evidence_text="64GB memory card included",
    )

    answer = resolve_field(field("sd_card_included", "SD Card Included"), bundle)

    assert answer.status == RESOLVED
    assert answer.answer_values == ["Yes"]
