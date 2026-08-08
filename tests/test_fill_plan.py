from __future__ import annotations

from app.ai_decisions import (
    CONFLICT,
    MISSING,
    READY as AI_READY,
    REVIEW,
    AIDecisionPacket,
    DecisionCitation,
    FieldDecision,
    field_id,
)
from app.evidence_contract import ProductIdentity
from app.fill_plan import (
    BLOCKED,
    GATE_AI_CONFLICT,
    GATE_AI_MISSING,
    GATE_AI_REVIEW,
    GATE_BUSINESS_LOCKED,
    GATE_HARD_FIELD_CONSTRAINT,
    READY,
    build_live_fill_plan,
)
from app.source_bundle import ProductSourceBundle


def field(
    key: str,
    label: str,
    *,
    required: bool = True,
    section: str = "Product Description",
    options: tuple[str, ...] = (),
    multi_value: bool = False,
):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section,
        "required": required,
        "multi_value": multi_value,
        "options": [{"text": item, "value": item} for item in options],
        "controls": [],
    }


def packet(fields, decisions):
    return AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1"),
        schema_sha256="",
        source_manifest_sha256="",
        decisions=decisions,
        extractor="fake-ai",
    )


def decision(target, status, values=(), *, evidence="visible proof", confidence=0.9):
    citations = []
    if evidence:
        citations = [
            DecisionCitation(
                source_reference="image:001",
                evidence_text=evidence,
            )
        ]
    return FieldDecision(
        field_id=field_id(target),
        status=status,
        values=list(values),
        confidence=confidence,
        citations=citations,
        reason="AI semantic decision",
    )


def add_structured(bundle: ProductSourceBundle, key: str, value: str):
    bundle.add_evidence(
        key=key,
        value=value,
        source_type="structured",
        source_reference=f"products.xlsx:{key}",
        priority=10,
        confidence=1.0,
        evidence_text=f"{key}: {value}",
    )


def test_ai_ready_field_goes_directly_to_fill_plan_without_qa_matcher():
    colour = field("colour", "Colour", options=("Black", "White"))
    plan = build_live_fill_plan(
        packet([colour], [decision(colour, AI_READY, ("Black",))]),
        [colour],
        ProductSourceBundle(),
    )

    item = plan.items[0]
    assert item.action == READY
    assert item.resolution.answer_values == ["Black"]
    assert item.resolution.source_type == "ai_decision"
    assert item.resolution.eligible_for_autofill is True
    assert "question" not in item.as_dict()
    assert "match_basis" not in item.as_dict()


def test_ai_review_is_previewable_but_not_autofill_ready():
    camera = field("camera_type", "Camera Type")
    plan = build_live_fill_plan(
        packet([camera], [decision(camera, REVIEW, ("Dashboard",), confidence=0.72)]),
        [camera],
        ProductSourceBundle(),
    )

    item = plan.items[0]
    assert item.action == BLOCKED
    assert item.resolution.preview_eligible is True
    assert item.resolution.gate_reason == GATE_AI_REVIEW


def test_ai_conflict_and_missing_are_blocked_without_local_semantic_override():
    resolution = field("recording_resolution", "Recording Resolution")
    sensor = field("image_sensor", "Image Sensor")
    plan = build_live_fill_plan(
        packet(
            [resolution, sensor],
            [
                decision(resolution, CONFLICT, (), evidence=""),
                decision(sensor, MISSING, (), evidence=""),
            ],
        ),
        [resolution, sensor],
        ProductSourceBundle(),
    )

    assert [item.action for item in plan.items] == [BLOCKED, BLOCKED]
    assert plan.items[0].resolution.gate_reason == GATE_AI_CONFLICT
    assert plan.items[1].resolution.gate_reason == GATE_AI_MISSING


def test_live_option_mismatch_is_a_hard_guard_not_a_semantic_guess():
    colour = field("colour", "Colour", options=("Black", "White"))
    plan = build_live_fill_plan(
        packet([colour], [decision(colour, AI_READY, ("Dark",))]),
        [colour],
        ProductSourceBundle(),
    )

    item = plan.items[0]
    assert item.action == BLOCKED
    assert item.resolution.gate_reason == GATE_HARD_FIELD_CONSTRAINT
    assert "Makro" in item.reason


def test_single_value_field_rejects_multiple_ai_values_before_browser_write():
    colour = field("colour", "Colour", options=("Black", "White"))
    plan = build_live_fill_plan(
        packet([colour], [decision(colour, AI_READY, ("Black", "White"))]),
        [colour],
        ProductSourceBundle(),
    )

    assert plan.items[0].action == BLOCKED
    assert plan.items[0].resolution.gate_reason == GATE_HARD_FIELD_CONSTRAINT


def test_business_field_ignores_ai_and_requires_explicit_seller_data():
    selling = field(
        "flipkart_selling_price",
        "Your selling price",
        section="Price, Stock and Shipping Information",
    )
    guessed = decision(selling, AI_READY, ("899",))

    no_business_data = build_live_fill_plan(
        packet([selling], [guessed]),
        [selling],
        ProductSourceBundle(),
    )
    assert no_business_data.items[0].action == BLOCKED
    assert no_business_data.items[0].resolution.gate_reason == GATE_BUSINESS_LOCKED

    bundle = ProductSourceBundle()
    add_structured(bundle, "Selling Price", "899")
    explicit = build_live_fill_plan(
        packet([selling], [guessed]),
        [selling],
        bundle,
    )
    assert explicit.items[0].action == READY
    assert explicit.items[0].resolution.source_type == "structured"


def test_selling_price_above_mrp_blocks_both_explicit_business_fields():
    mrp = field("mrp", "Base Price", section="Price, Stock and Shipping Information")
    selling = field(
        "flipkart_selling_price",
        "Your selling price",
        section="Price, Stock and Shipping Information",
    )
    bundle = ProductSourceBundle()
    add_structured(bundle, "Base Price", "800")
    add_structured(bundle, "Selling Price", "899")

    plan = build_live_fill_plan(
        packet(
            [mrp, selling],
            [
                decision(mrp, MISSING, (), evidence=""),
                decision(selling, MISSING, (), evidence=""),
            ],
        ),
        [mrp, selling],
        bundle,
    )

    assert [item.action for item in plan.items] == [BLOCKED, BLOCKED]
    assert all("价格关系无效" in item.reason for item in plan.items)


def test_minimum_order_quantity_above_maximum_blocks_both_explicit_fields():
    minimum = field(
        "minimum_order_quantity",
        "Minimum Order Quantity (MinOQ)",
        section="Price, Stock and Shipping Information",
    )
    maximum = field(
        "max_order_quantity_allowed",
        "Maximum Order Quantity (MaxOQ)",
        section="Price, Stock and Shipping Information",
    )
    bundle = ProductSourceBundle()
    add_structured(bundle, "Minimum Order Quantity", "10")
    add_structured(bundle, "Maximum Order Quantity", "5")

    plan = build_live_fill_plan(
        packet(
            [minimum, maximum],
            [
                decision(minimum, MISSING, (), evidence=""),
                decision(maximum, MISSING, (), evidence=""),
            ],
        ),
        [minimum, maximum],
        bundle,
    )

    assert [item.action for item in plan.items] == [BLOCKED, BLOCKED]
    assert all("MOQ 关系无效" in item.reason for item in plan.items)
