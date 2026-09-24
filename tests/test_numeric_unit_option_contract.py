from __future__ import annotations

import pytest

from app.ai_decisions import FieldDecision, READY as AI_READY, field_options, field_qualifier_options
from app.fill_plan import _hard_guard_values
from app.live_schema import live_schema_payload
from app.required_overrides import RequiredOverrideError, required_fallback_override


def _depth_field() -> dict[str, object]:
    return {
        "attribute_key": "depth",
        "label": "Depth",
        "section_heading": "Additional Description (0/12)",
        "required": True,
        "multi_value": False,
        "options": ["cm"],
        "qualifier_options": [],
        "help_text": "",
        "context_text": "",
        "controls": [
            {"name": "depth", "options": []},
            {"name": "depth_qualifier", "options": ["cm"]},
        ],
    }


def test_schema_still_separates_value_options_from_qualifier_options():
    field = _depth_field()

    assert field_options(field) == []
    assert field_qualifier_options(field) == ["cm"]

    schema_field = live_schema_payload([field])["fields"][0]
    assert schema_field["options"] == []
    assert schema_field["qualifier_options"] == ["cm"]


def test_required_placeholder_generation_is_disabled():
    with pytest.raises(RequiredOverrideError, match="自动 N/A / 1 / 首选项兜底已禁用"):
        required_fallback_override(_depth_field())


def test_ai_qualified_value_is_preserved_exactly_for_free_text_control():
    field = {
        "attribute_key": "ideal_room_size",
        "label": "Ideal Room Size",
        "controls": [{"type": "text", "field_kind": "input"}],
    }
    decision = FieldDecision(
        field_id="unused",
        status=AI_READY,
        values=["1000"],
        qualifier="square_feet",
    )

    values, qualifier, error = _hard_guard_values(field, decision)

    assert error is None
    assert values == ["1000"]
    assert qualifier == "square_feet"


def test_ai_value_is_not_reformatted_when_unit_already_appears_in_value():
    field = {
        "attribute_key": "water_tank_capacity",
        "label": "Water Tank Capacity",
        "controls": [{"type": "text", "field_kind": "input"}],
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
    assert qualifier == "fl oz"


def test_numeric_control_does_not_veto_or_rewrite_ai_qualifier():
    field = {
        "attribute_key": "capacity",
        "label": "Capacity",
        "controls": [
            {
                "id": "capacity",
                "name": "capacity",
                "field_kind": "input",
                "type": "number",
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

    assert error is None
    assert values == ["95"]
    assert qualifier == "fl oz"


def test_fixed_rendered_unit_does_not_consume_ai_qualifier():
    field = {
        "attribute_key": "length",
        "label": "Length",
        "controls": [
            {
                "id": "length",
                "name": "length",
                "field_kind": "input",
                "type": "number",
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
    assert qualifier == "cm"


def test_real_value_select_options_are_still_available_as_schema_context():
    field = {
        "attribute_key": "colour",
        "label": "Colour",
        "options": ["Select One", "White", "Black"],
        "controls": [{"name": "colour", "options": ["Select One", "White", "Black"]}],
    }

    assert field_options(field) == ["Select One", "White", "Black"]
