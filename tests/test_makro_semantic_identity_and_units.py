from __future__ import annotations

from app.ai_decisions import READY, FieldDecision
from app.fill_plan import _fixed_qualifier_rendered, _hard_guard_values
from app.makro.semantic_normalize import coalesce_radio_semantic_fields


def _semantic_field(attribute_key: str, label: str, *, section: str = "Price, Stock and Shipping Information") -> dict:
    return {
        "attribute_key": attribute_key,
        "label": label,
        "section_heading": section,
        "subsection_heading": "",
        "required": True,
        "multi_value": False,
        "controls": [
            {
                "id": attribute_key,
                "name": f"{attribute_key}_0_value",
                "field_kind": "input",
                "type": "number",
                "options": [],
            }
        ],
    }


def test_duplicate_rendered_dimension_labels_are_disambiguated_from_stable_keys():
    fields = [
        _semantic_field("length", "Length"),
        _semantic_field("breadth", "Length"),
        _semantic_field("height", "Length"),
        _semantic_field("weight", "Length"),
    ]

    normalized = coalesce_radio_semantic_fields(fields)

    assert [field["label"] for field in normalized] == [
        "Length",
        "Breadth",
        "Height",
        "Weight",
    ]
    assert [field["attribute_key"] for field in normalized] == [
        "length",
        "breadth",
        "height",
        "weight",
    ]
    assert normalized[1]["rendered_label"] == "Length"
    assert normalized[3]["label_disambiguated_from_attribute_key"] is True


def test_duplicate_labels_with_opaque_keys_fail_closed_without_renaming():
    fields = [
        _semantic_field("length", "Length"),
        _semantic_field("zoe6em", "Length"),
    ]

    normalized = coalesce_radio_semantic_fields(fields)

    assert [field["label"] for field in normalized] == ["Length", "Length"]
    assert all("label_disambiguated_from_attribute_key" not in field for field in normalized)


def _noise_field(context: str) -> dict:
    return {
        "attribute_key": "noise_level",
        "label": "Noise Level",
        "section_heading": "Additional Description",
        "required": False,
        "multi_value": False,
        "options": [],
        "qualifier_options": [],
        "context_text": context,
        "controls": [
            {
                "id": "noise_level",
                "name": "noise_level_0_value",
                "field_kind": "input",
                "type": "number",
                "context_text": context,
                "options": [],
            }
        ],
    }


def test_annotated_sound_unit_matches_bare_fixed_db_display():
    field = _noise_field("Noise Level dB")
    decision = FieldDecision(
        field_id="noise",
        status=READY,
        values=["36"],
        qualifier="dB(A)",
        confidence=0.9,
    )

    values, qualifier, error = _hard_guard_values(field, decision)

    assert _fixed_qualifier_rendered(field, "dB(A)") is True
    assert values == ["36"]
    assert qualifier == ""
    assert error is None


def test_annotated_sound_unit_matches_compact_dba_display():
    field = _noise_field("Noise Level dBA")

    assert _fixed_qualifier_rendered(field, "dB(A)") is True


def test_annotated_sound_unit_does_not_match_different_weighting():
    field = _noise_field("Noise Level dB(C)")
    decision = FieldDecision(
        field_id="noise",
        status=READY,
        values=["36"],
        qualifier="dB(A)",
        confidence=0.9,
    )

    values, qualifier, error = _hard_guard_values(field, decision)

    assert _fixed_qualifier_rendered(field, "dB(A)") is False
    assert values == ["36"]
    assert qualifier == "dB(A)"
    assert error is not None
