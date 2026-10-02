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
from app.live_schema import live_schema_payload
from app.source_bundle import ProductSourceBundle


def field(
    key: str,
    label: str,
    *,
    required: bool = True,
    section: str = "Product Description",
    options: tuple[str, ...] = (),
    multi_value: bool = False,
    qualifier_options: tuple[str, ...] = (),
    context_text: str = "",
    controls=(),
):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section,
        "required": required,
        "multi_value": multi_value,
        "options": [{"text": item, "value": item} for item in options],
        "qualifier_options": list(qualifier_options),
        "context_text": context_text,
        "controls": list(controls),
    }


# A value becomes READY only when the observed live execution contract admits it,
# so executable fixtures carry the live controls a real Makro DOM scan produces.
def select_control(key: str, options: tuple[str, ...]) -> dict:
    return {
        "id": key,
        "name": f"{key}_0_value",
        "field_kind": "select",
        "options": [{"text": item, "value": item, "disabled": False} for item in options],
    }


def text_control(key: str) -> dict:
    return {"id": key, "name": f"{key}_0_value", "type": "text", "field_kind": "input"}


def number_control(key: str, **extra) -> dict:
    return {
        "id": key,
        "name": f"{key}_0_value",
        "type": "number",
        "inputmode": "decimal",
        "field_kind": "input",
        **extra,
    }


def packet(fields, decisions):
    del fields
    return AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1"),
        schema_sha256="",
        source_manifest_sha256="",
        decisions=decisions,
        extractor="fake-ai",
    )


def decision(target, status, values=(), *, evidence="visible proof", confidence=0.9, qualifier=""):
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
        qualifier=qualifier,
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


def test_ai_ready_is_authoritative_and_flows_directly_to_fill_plan():
    colour = field(
        "colour",
        "Colour",
        options=("Black", "White"),
        controls=(select_control("colour", ("Black", "White")),),
    )
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


def test_ai_ready_outside_live_option_domain_is_blocked_without_rewrite():
    # The live option domain is a mechanical hard guard: Python never maps the
    # AI answer onto a "close" option, it keeps the answer and blocks the write.
    colour = field(
        "colour",
        "Colour",
        options=("Black", "White"),
        controls=(
            {
                "name": "colour_0_value",
                "field_kind": "select",
                "options": [
                    {"text": "Black", "value": "Black"},
                    {"text": "White", "value": "White"},
                ],
            },
        ),
    )
    plan = build_live_fill_plan(
        packet([colour], [decision(colour, AI_READY, ("Dark",))]),
        [colour],
        ProductSourceBundle(),
    )

    item = plan.items[0]
    assert item.action == BLOCKED
    assert item.resolution.gate_reason == GATE_HARD_FIELD_CONSTRAINT
    assert item.resolution.eligible_for_autofill is False
    assert item.resolution.answer_values == ["Dark"]
    assert "live execution contract" in item.reason


def test_single_value_field_blocks_multiple_ai_values_without_rewrite():
    feature = field(
        "feature",
        "Feature",
        multi_value=False,
        controls=(text_control("feature"),),
    )
    plan = build_live_fill_plan(
        packet([feature], [decision(feature, AI_READY, ("A", "B"))]),
        [feature],
        ProductSourceBundle(),
    )

    item = plan.items[0]
    assert item.action == BLOCKED
    assert item.resolution.gate_reason == GATE_HARD_FIELD_CONSTRAINT
    assert item.resolution.answer_values == ["A", "B"]


def test_ai_qualifier_is_never_unit_converted_to_fit_a_fixed_unit():
    weight = field(
        "weight",
        "Weight",
        context_text="Weight *KG",
        controls=({"type": "number", "inputmode": "decimal", "context_text": "Weight *KG"},),
    )
    converted_unit = build_live_fill_plan(
        packet([weight], [decision(weight, AI_READY, ("285",), qualifier="g")]),
        [weight],
        ProductSourceBundle(),
    )

    blocked = converted_unit.items[0]
    assert blocked.action == BLOCKED
    assert blocked.resolution.gate_reason == GATE_HARD_FIELD_CONSTRAINT
    assert blocked.resolution.answer_values == ["285"]
    assert blocked.resolution.qualifier == "g"

    same_unit = build_live_fill_plan(
        packet([weight], [decision(weight, AI_READY, ("0.285",), qualifier="kg")]),
        [weight],
        ProductSourceBundle(),
    )

    ready = same_unit.items[0]
    assert ready.action == READY
    assert ready.resolution.answer_values == ["0.285"]
    assert ready.resolution.qualifier == "kg"


def test_ai_ready_survives_schema_only_to_full_dom_rebind_unchanged():
    current = field(
        "bluetooth_range",
        "Bluetooth Range",
        controls=(
            number_control("bluetooth_range", min="0", max="1000"),
            {
                "name": "bluetooth_range_0_qualifier",
                "field_kind": "select",
                "options": [
                    {"text": "Feet", "value": "Feet", "disabled": False},
                    {"text": "Meter", "value": "Meter", "disabled": False},
                ],
            },
        ),
    )
    # The planner-side schema field carries only the frozen execution contract.
    planned = live_schema_payload([current])["fields"][0]
    assert "controls" not in planned
    resolved = decision(planned, AI_READY, ("33",), qualifier="Feet")

    first = build_live_fill_plan(packet([planned], [resolved]), [planned], ProductSourceBundle())
    second = build_live_fill_plan(packet([current], [resolved]), [current], ProductSourceBundle())

    assert first.items[0].action == READY
    assert second.items[0].action == READY
    assert second.items[0].resolution.answer_values == ["33"]
    assert second.items[0].resolution.qualifier == "Feet"


def test_ai_review_is_previewable_but_not_promoted_by_python():
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


def test_ai_conflict_and_missing_remain_ai_owned_blocked_states():
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


def test_business_field_still_requires_explicit_seller_data():
    selling = field(
        "flipkart_selling_price",
        "Your selling price",
        section="Price, Stock and Shipping Information",
        controls=(number_control("flipkart_selling_price"),),
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


def test_selling_price_above_mrp_blocks_only_explicit_business_fields():
    mrp = field(
        "mrp",
        "Base Price",
        section="Price, Stock and Shipping Information",
        controls=(number_control("mrp"),),
    )
    selling = field(
        "flipkart_selling_price",
        "Your selling price",
        section="Price, Stock and Shipping Information",
        controls=(number_control("flipkart_selling_price"),),
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
