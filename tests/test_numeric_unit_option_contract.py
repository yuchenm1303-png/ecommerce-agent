from __future__ import annotations

from app.ai_decisions import FieldDecision, READY as AI_READY, field_options, field_qualifier_options
from app.fill_plan import _hard_guard_values
from app.live_schema import live_schema_payload
from app.required_overrides import required_fallback_override


def _depth_field() -> dict[str, object]:
    return {
        "attribute_key": "depth",
        "label": "Depth",
        "section_heading": "Additional Description (0/12)",
        "required": True,
        "multi_value": False,
        # Raw semantic aggregation can contain qualifier options here even
        # though the primary value control itself is a free numeric input.
        "options": ["cm"],
        "qualifier_options": [],
        "help_text": "",
        "context_text": "",
        "controls": [
            {
                "name": "depth",
                "options": [],
            },
            {
                "name": "depth_qualifier",
                "options": ["cm"],
            },
        ],
    }


def test_numeric_unit_field_does_not_treat_unit_as_value_option():
    field = _depth_field()

    assert field_options(field) == []
    assert field_qualifier_options(field) == ["cm"]

    schema_field = live_schema_payload([field])["fields"][0]
    assert schema_field["options"] == []
    assert schema_field["qualifier_options"] == ["cm"]


def test_required_depth_fallback_passes_the_same_production_hard_guard():
    field = _depth_field()
    fallback = required_fallback_override(field)

    assert fallback["values"] == ["1"]
    assert fallback["qualifier"] == "cm"

    decision = FieldDecision(
        field_id=str(fallback["field_id"]),
        status=AI_READY,
        values=list(fallback["values"]),
        qualifier=str(fallback["qualifier"]),
    )
    values, qualifier, error = _hard_guard_values(field, decision)

    assert error is None
    assert values == ["1"]
    assert qualifier == "cm"


def test_free_text_field_folds_detached_qualifier_into_value():
    field = {
        "attribute_key": "ideal_room_size",
        "label": "Ideal Room Size",
        "section_heading": "Additional Description",
        "required": False,
        "multi_value": False,
        "options": [],
        "qualifier_options": [],
        "controls": [
            {
                "id": "ideal_room_size",
                "name": "ideal_room_size",
                "field_kind": "input",
                "type": "text",
                "options": [],
            }
        ],
    }
    decision = FieldDecision(
        field_id="unused",
        status=AI_READY,
        values=["1000"],
        qualifier="square_feet",
    )

    values, qualifier, error = _hard_guard_values(field, decision)

    assert error is None
    assert values == ["1000 square feet"]
    assert qualifier == ""


def test_free_text_field_does_not_duplicate_unit_already_in_value():
    field = {
        "attribute_key": "water_tank_capacity",
        "label": "Water Tank Capacity",
        "section_heading": "Additional Description",
        "required": False,
        "multi_value": False,
        "options": [],
        "qualifier_options": [],
        "controls": [
            {
                "id": "water_tank_capacity",
                "name": "water_tank_capacity",
                "field_kind": "input",
                "type": "text",
                "options": [],
            }
        ],
    }
    decision = FieldDecision(
        field_id="unused",
        status=AI_READY,
        values=["95 fl oz"],
        qualifier="fl oz",
    )

    values, qualifier, error = _hard_guard_values(field, decision)

    assert error is None
    assert values == ["95 fl oz"]
    assert qualifier == ""


def test_numeric_field_without_unit_contract_still_fails_closed():
    field = {
        "attribute_key": "capacity",
        "label": "Capacity",
        "section_heading": "Additional Description",
        "required": False,
        "multi_value": False,
        "options": [],
        "qualifier_options": [],
        "controls": [
            {
                "id": "capacity",
                "name": "capacity",
                "field_kind": "input",
                "type": "number",
                "options": [],
                "context_text": "",
            }
        ],
    }
    decision = FieldDecision(
        field_id="unused",
        status=AI_READY,
        values=["95"],
        qualifier="fl oz",
    )

    values, qualifier, error = _hard_guard_values(field, decision)

    assert values == ["95"]
    assert qualifier == "fl oz"
    assert error is not None


def test_numeric_field_accepts_exact_local_fixed_unit():
    field = {
        "attribute_key": "length",
        "label": "Length",
        "section_heading": "Additional Description",
        "required": False,
        "multi_value": False,
        "options": [],
        "qualifier_options": [],
        "controls": [
            {
                "id": "length",
                "name": "length",
                "field_kind": "input",
                "type": "number",
                "options": [],
                "context_text": "Length cm",
            }
        ],
    }
    decision = FieldDecision(
        field_id="unused",
        status=AI_READY,
        values=["17"],
        qualifier="cm",
    )

    values, qualifier, error = _hard_guard_values(field, decision)

    assert error is None
    assert values == ["17"]
    assert qualifier == ""


def test_real_value_select_options_are_still_preserved():
    field = {
        "attribute_key": "colour",
        "label": "Colour",
        "section_heading": "Product Description (0/10)",
        "required": True,
        "multi_value": False,
        "options": ["Select One", "White", "Black"],
        "qualifier_options": [],
        "controls": [
            {
                "name": "colour",
                "options": ["Select One", "White", "Black"],
            }
        ],
    }

    assert field_options(field) == ["Select One", "White", "Black"]
