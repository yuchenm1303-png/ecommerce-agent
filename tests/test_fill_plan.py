from __future__ import annotations

from app.fill_plan import (
    BLOCKED,
    GATE_SHARED_SYNTHESIS_BINDING,
    READY,
    build_live_fill_plan,
)
from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.resolution_engine import ResolutionPolicy
from app.source_bundle import ProductSourceBundle, normalize_key


def field(key: str, label: str, *, required: bool = True, section: str = "Product Description"):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section,
        "required": required,
        "multi_value": False,
        "controls": [],
    }


def catalog() -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[QuestionRecord(number="1", question="Model Number")],
    )


def add_structured(bundle: ProductSourceBundle, key: str, value: str):
    bundle.add_evidence(
        key=key,
        value=value,
        source_type="structured",
        source_reference=f"products.xlsx:{key}",
        priority=10,
        confidence=1.0,
    )


def add_product_image(bundle: ProductSourceBundle, key: str, value: str):
    bundle.add_evidence(
        key=key,
        value=value,
        source_type="product_image",
        source_reference="image:001",
        priority=30,
        confidence=0.96,
        evidence_text=f"{key}: {value}",
    )


def add_ai_synthesis(
    bundle: ProductSourceBundle,
    key: str,
    value: str,
    *,
    reference: str,
    evidence: str,
):
    bundle.add_evidence(
        key=key,
        value=value,
        source_type="ai_synthesis",
        source_reference=reference,
        priority=90,
        confidence=0.84,
        evidence_text=evidence,
    )


def test_plan_resolves_matched_product_question_and_unmatched_business_field():
    bundle = ProductSourceBundle()
    add_structured(bundle, "Model Number", "L11")
    add_structured(bundle, "Selling Price", "899")

    plan = build_live_fill_plan(
        catalog(),
        [
            field("model_number", "Model Number"),
            field("flipkart_selling_price", "Your selling price"),
        ],
        bundle,
    )

    assert plan.items[0].action == READY
    assert plan.items[0].question == "Model Number"
    assert plan.items[1].action == READY
    assert plan.items[1].question == ""
    assert plan.summary()["required_blocked"] == 0


def test_missing_required_business_field_blocks_plan():
    bundle = ProductSourceBundle()
    add_structured(bundle, "Model Number", "L11")

    plan = build_live_fill_plan(
        catalog(),
        [
            field("model_number", "Model Number"),
            field("flipkart_selling_price", "Your selling price"),
        ],
        bundle,
        policy=ResolutionPolicy(),
    )

    assert plan.items[1].action == BLOCKED
    assert plan.items[1].required is True
    assert plan.summary()["required_blocked"] == 1
    assert plan.summary()["safe_to_autofill_required_fields"] is False


def test_selling_price_above_base_price_blocks_both_fields():
    bundle = ProductSourceBundle()
    add_structured(bundle, "Base Price", "800")
    add_structured(bundle, "Selling Price", "899")

    plan = build_live_fill_plan(
        catalog(),
        [
            field("mrp", "Base Price"),
            field("flipkart_selling_price", "Your selling price"),
        ],
        bundle,
    )

    assert [item.action for item in plan.items] == [BLOCKED, BLOCKED]
    assert all("价格关系无效" in item.reason for item in plan.items)


def test_minimum_order_quantity_above_maximum_blocks_both_fields():
    bundle = ProductSourceBundle()
    add_structured(bundle, "Minimum Order Quantity", "10")
    add_structured(bundle, "Maximum Order Quantity", "5")

    plan = build_live_fill_plan(
        catalog(),
        [
            field("minimum_order_quantity", "Minimum Order Quantity (MinOQ)"),
            field("max_order_quantity_allowed", "Maximum Order Quantity (MaxOQ)"),
        ],
        bundle,
    )

    assert [item.action for item in plan.items] == [BLOCKED, BLOCKED]
    assert all("MOQ 关系无效" in item.reason for item in plan.items)


def test_explicit_question_alias_also_resolves_evidence_under_qa_label():
    qa = QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[QuestionRecord(number="1", question="Video Resolution")],
    )
    bundle = ProductSourceBundle()
    add_structured(bundle, "Video Resolution", "1920x1080")
    aliases = {normalize_key("Video Resolution"): ("Image Resolution",)}

    plan = build_live_fill_plan(
        qa,
        [field("image_resolution", "Image Resolution")],
        bundle,
        aliases=aliases,
    )

    assert plan.items[0].action == READY
    assert plan.items[0].question == "Video Resolution"
    assert plan.items[0].match_basis == "explicit-alias"
    assert plan.items[0].resolution.answer == "1920x1080"
    assert plan.items[0].resolution.label == "Image Resolution"
    assert plan.items[0].resolution.provenance[0]["key"] == "Video Resolution"


def test_unmatched_live_field_cannot_autofill_from_semantic_key_collision():
    bundle = ProductSourceBundle()
    add_product_image(bundle, "Height", "7")

    plan = build_live_fill_plan(
        catalog(),
        [
            field(
                "height",
                "Length",
                section="Price, Stock and Shipping Information (0/14)",
            )
        ],
        bundle,
    )

    assert plan.items[0].question == ""
    assert plan.items[0].action == BLOCKED
    assert plan.items[0].resolution.eligible_for_autofill is False
    assert "同名 attribute_key/label 串字段" in plan.items[0].reason


def test_unmatched_live_field_may_autofill_from_explicit_structured_input():
    bundle = ProductSourceBundle()
    add_structured(bundle, "Height", "7")

    plan = build_live_fill_plan(
        catalog(),
        [
            field(
                "height",
                "Length",
                section="Price, Stock and Shipping Information (0/14)",
            )
        ],
        bundle,
    )

    assert plan.items[0].question == ""
    assert plan.items[0].action == READY
    assert plan.items[0].resolution.source_type == "structured"


def test_same_ai_synthesis_source_value_cannot_preview_two_different_fields_even_with_different_prose():
    qa = QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[
            QuestionRecord(number="10", question="Interior Field of View"),
            QuestionRecord(number="11", question="Exterior Field of View"),
        ],
    )
    bundle = ProductSourceBundle()
    add_ai_synthesis(
        bundle,
        "Interior Field of View",
        "120",
        reference="image:attributes",
        evidence="generic 120 degree angle interpreted for interior view",
    )
    add_ai_synthesis(
        bundle,
        "Exterior Field of View",
        "120",
        reference="image:attributes",
        evidence="same generic 120 degree angle interpreted for exterior view",
    )

    plan = build_live_fill_plan(
        qa,
        [
            field("interior_field_of_view", "Interior Field of View"),
            field("exterior_field_of_view", "Exterior Field of View"),
        ],
        bundle,
    )

    assert plan.summary()["preview_eligible"] == 0
    assert all(item.action == BLOCKED for item in plan.items)
    assert all(
        item.resolution.gate_reason == GATE_SHARED_SYNTHESIS_BINDING
        for item in plan.items
    )
    assert all("字段归属不唯一" in item.reason for item in plan.items)
