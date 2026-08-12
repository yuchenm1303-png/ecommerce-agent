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
