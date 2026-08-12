from __future__ import annotations

from app.makro.semantic_normalize import coalesce_radio_semantic_fields


def _radio_field(*, key: str, name: str, label: str, value: str, section: str = "Product Description (0/10)"):
    return {
        "attribute_key": key,
        "label": "Colour",
        "section_heading": section,
        "subsection_heading": "",
        "required": True,
        "field_kind": "radio",
        "accepted_control_kinds": ["radio"],
        "multi_value": False,
        "options": [],
        "controls": [
            {
                "id": key,
                "name": name,
                "field_kind": "radio",
                "label": label,
                "value": value,
            }
        ],
    }


def test_same_named_radio_options_are_coalesced_into_one_semantic_field():
    fields = [
        _radio_field(key="colour_red", name="colour", label="Red", value="RED"),
        _radio_field(key="colour_blue", name="colour", label="Blue", value="BLUE"),
    ]
    result = coalesce_radio_semantic_fields(fields)
    assert len(result) == 1
    merged = result[0]
    assert merged["attribute_key"] == "colour"
    assert merged["radio_group_normalized"] is True
    assert len(merged["controls"]) == 2
    assert {(option["text"], option["value"]) for option in merged["options"]} == {
        ("Red", "RED"),
        ("Blue", "BLUE"),
    }


def test_radio_groups_never_merge_across_sections():
    result = coalesce_radio_semantic_fields(
        [
            _radio_field(key="red_a", name="colour", label="Red", value="RED", section="Section A (0/2)"),
            _radio_field(key="blue_b", name="colour", label="Blue", value="BLUE", section="Section B (0/2)"),
        ]
    )
    assert len(result) == 2
    assert not any(field.get("radio_group_normalized") for field in result)


def test_non_radio_semantic_fields_are_bit_for_bit_untouched():
    field = {
        "attribute_key": "model_number",
        "label": "Model Number",
        "section_heading": "Product Description (0/10)",
        "controls": [{"id": "model_number", "name": "model_number_0_value", "field_kind": "input"}],
    }
    result = coalesce_radio_semantic_fields([field])
    assert result == [field]
    assert result[0] is field
