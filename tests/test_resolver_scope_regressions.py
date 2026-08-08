from __future__ import annotations

from app.answer_resolver import MISSING, RESOLVED, resolve_field
from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.resolution_engine import resolve_one
from app.snapshot_evidence import extract_snapshot_evidence
from app.source_bundle import ProductSourceBundle
from app.source_snapshot import SourceSnapshot, SnapshotTableRow


def field(
    key: str,
    label: str,
    *,
    section: str = "Product Description (0/14)",
    multi_value: bool = False,
):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section,
        "required": True,
        "multi_value": multi_value,
        "options": [],
        "controls": [],
    }


def add(
    bundle: ProductSourceBundle,
    key: str,
    value: str,
    *,
    source_type: str = "supplier_web",
    reference: str = "supplier:001:text:0001:abc",
    evidence: str,
    confidence: float = 0.88,
):
    bundle.add_evidence(
        key=key,
        value=value,
        source_type=source_type,
        source_reference=reference,
        priority=55,
        confidence=confidence,
        evidence_text=evidence,
    )


def test_supplier_product_brand_other_cannot_answer_vehicle_brand():
    bundle = ProductSourceBundle()
    add(
        bundle,
        "Vehicle Brand",
        "other",
        evidence="Brand: other",
    )

    answer = resolve_field(field("vehicle_brand", "Vehicle Brand"), bundle)

    assert answer.status == MISSING


def test_supplier_internal_memory_none_cannot_answer_sd_card_included_no():
    bundle = ProductSourceBundle()
    add(
        bundle,
        "SD Card Included",
        "No",
        evidence="内存容量 无",
    )
    add(
        bundle,
        "SD Card Included",
        "Yes",
        source_type="customer_answer",
        reference="qa.xlsx:row=12",
        evidence="SD Card Included=Yes",
        confidence=1.0,
    )

    answer = resolve_field(field("sd_card_included", "SD Card Included"), bundle)

    assert answer.status == RESOLVED
    assert answer.answer_values == ["Yes"]


def test_generic_angle_cannot_answer_either_specific_fov():
    bundle = ProductSourceBundle()
    add(
        bundle,
        "Interior Field of View",
        "120",
        source_type="ai_synthesis",
        reference="image:001:abc",
        evidence="拍摄角度 120°",
        confidence=0.84,
    )
    add(
        bundle,
        "Exterior Field of View",
        "120",
        source_type="ai_synthesis",
        reference="image:001:abc",
        evidence="拍摄角度 120°",
        confidence=0.84,
    )

    interior = resolve_field(
        field("interior_field_of_view", "Interior Field of View"), bundle
    )
    exterior = resolve_field(
        field("exterior_field_of_view", "Exterior Field of View"), bundle
    )

    assert interior.status == MISSING
    assert exterior.status == MISSING


def test_position_scoped_fov_evidence_remains_usable():
    bundle = ProductSourceBundle()
    add(
        bundle,
        "Interior Field of View",
        "120",
        source_type="ai_synthesis",
        reference="image:002:abc",
        evidence="Cabin camera field of view is shown as 120°",
        confidence=0.84,
    )
    add(
        bundle,
        "Exterior Field of View",
        "120",
        source_type="ai_synthesis",
        reference="image:003:abc",
        evidence="Front camera field of view is shown as 120°",
        confidence=0.84,
    )

    assert resolve_field(
        field("interior_field_of_view", "Interior Field of View"), bundle
    ).status == RESOLVED
    assert resolve_field(
        field("exterior_field_of_view", "Exterior Field of View"), bundle
    ).status == RESOLVED


def test_cabin_camera_cannot_be_rewritten_as_back_position():
    bundle = ProductSourceBundle()
    add(
        bundle,
        "Camera Position",
        "Front|Back",
        source_type="ai_synthesis",
        reference="image:002:abc",
        evidence="Front + cabin dual lens dash cam",
        confidence=0.84,
    )

    answer = resolve_field(
        field("camera_position", "Camera Position", multi_value=True), bundle
    )

    assert answer.status == MISSING


def test_product_dimensions_require_product_scope_and_ignore_packaging_scope():
    bundle = ProductSourceBundle()
    add(
        bundle,
        "Width",
        "11 cm",
        source_type="ai_synthesis",
        reference="image:001:abc",
        evidence="Packaging dimensions 16 x 11 x 7 cm",
        confidence=0.84,
    )
    add(
        bundle,
        "Width",
        "86 mm",
        source_type="ai_synthesis",
        reference="image:002:abc",
        evidence="Product dimensions: width 86 mm, depth 35 mm, height 36 mm",
        confidence=0.84,
    )

    answer = resolve_field(field("width", "Width"), bundle)

    assert answer.status == RESOLVED
    assert answer.answer_values == ["86 mm"]


def test_package_dimension_target_uses_only_packaging_scope():
    bundle = ProductSourceBundle()
    add(
        bundle,
        "Length",
        "16 cm",
        source_type="ai_synthesis",
        reference="image:001:abc",
        evidence="Packaging dimensions: length 16 cm, breadth 11 cm, height 7 cm",
        confidence=0.84,
    )
    add(
        bundle,
        "Length",
        "86 mm",
        source_type="ai_synthesis",
        reference="image:002:abc",
        evidence="Product dimensions: length 86 mm",
        confidence=0.84,
    )

    answer = resolve_field(
        field(
            "length",
            "Length",
            section="Price, Stock and Shipping Information (0/14)",
        ),
        bundle,
    )

    assert answer.status == RESOLVED
    assert answer.answer_values == ["16 cm"]


def test_matched_length_does_not_inherit_reused_height_attribute_key_evidence():
    bundle = ProductSourceBundle()
    add(
        bundle,
        "Height",
        "36 mm",
        source_type="structured",
        reference="products.xlsx:Height",
        evidence="Height=36 mm",
        confidence=1.0,
    )
    add(
        bundle,
        "Length",
        "16 cm",
        source_type="structured",
        reference="products.xlsx:PackageLength",
        evidence="Package Length=16 cm",
        confidence=1.0,
    )
    live = field(
        "height",
        "Length",
        section="Price, Stock and Shipping Information (0/14)",
    )
    question = QuestionRecord(number="LIVE-56", question="Length")

    record = resolve_one(live, bundle, question=question)

    assert record.answer_values == ["16 cm"]
    assert [item["key"] for item in record.provenance] == ["Length"]
    assert record.attribute_key == "height"
    assert record.label == "Length"


def test_generic_snapshot_dimension_row_is_quarantined_without_scope():
    catalog = QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[QuestionRecord(number="1", question="Width")],
    )
    snapshot = SourceSnapshot(
        requested_url="https://supplier.test/item",
        final_url="https://supplier.test/item",
        title="Camera",
        captured_at="2026-08-08T00:00:00+00:00",
        table_rows=[SnapshotTableRow("Width", "11 cm", 1, 1)],
    )

    result = extract_snapshot_evidence(snapshot, catalog)

    assert result.packet.facts == []
    assert any("scope-ambiguous dimension ignored" in item for item in result.warnings)
