from __future__ import annotations

from app.fill_plan import BLOCKED, READY, build_live_fill_plan
from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.resolution_engine import ResolutionPolicy
from app.source_bundle import ProductSourceBundle


def field(key: str, label: str, *, required: bool = True):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": "Product Description",
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


def test_plan_resolves_matched_product_question_and_unmatched_business_field():
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key="Model Number",
        value="L11",
        source_type="structured",
        source_reference="products.xlsx:model",
        priority=10,
        confidence=0.99,
    )
    bundle.add_evidence(
        key="Selling Price",
        value="899",
        source_type="structured",
        source_reference="products.xlsx:price",
        priority=10,
        confidence=1.0,
    )

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
    bundle.add_evidence(
        key="Model Number",
        value="L11",
        source_type="structured",
        source_reference="products.xlsx:model",
        priority=10,
        confidence=0.99,
    )

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
