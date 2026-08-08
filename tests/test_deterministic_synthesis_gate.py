from __future__ import annotations

from app.resolution_engine import (
    GATE_DETERMINISTIC_SYNTHESIS,
    GATE_LOW_CONFIDENCE,
    resolve_one,
)
from app.source_bundle import ProductSourceBundle


def field(key: str, label: str, *, multi_value: bool = False):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": "Additional Description (0/46)",
        "required": False,
        "multi_value": multi_value,
        "options": [],
        "controls": [],
    }


def synthesis(
    key: str,
    value: str,
    evidence: str,
    *,
    reference: str = "image:001:abc",
) -> ProductSourceBundle:
    bundle = ProductSourceBundle()
    bundle.add_evidence(
        key=key,
        value=value,
        source_type="ai_synthesis",
        source_reference=reference,
        priority=90,
        confidence=0.84,
        evidence_text=evidence,
    )
    return bundle


def test_reviewed_colour_translation_is_code_verified_not_threshold_relaxed():
    record = resolve_one(
        field("colour", "Colour"),
        synthesis("Colour", "Black", "颜色分类 黑色"),
    )

    assert record.eligible_for_autofill is True
    assert record.preview_eligible is False
    assert record.confidence == 0.84
    assert record.source_type == "ai_synthesis"
    assert record.gate_reason == GATE_DETERMINISTIC_SYNTHESIS


def test_explicit_g_sensor_presence_can_be_code_verified_to_yes():
    record = resolve_one(
        field("g_sensor", "G-sensor"),
        synthesis("G-sensor", "Yes", "Visible feature: G-Sensor"),
    )

    assert record.eligible_for_autofill is True
    assert record.gate_reason == GATE_DETERMINISTIC_SYNTHESIS


def test_literal_wifi_mapping_can_be_code_verified():
    record = resolve_one(
        field("other_connectivity_features", "Other Connectivity Features"),
        synthesis("Other Connectivity Features", "WIFI", "Visible feature text: WIFI"),
    )

    assert record.eligible_for_autofill is True
    assert record.gate_reason == GATE_DETERMINISTIC_SYNTHESIS


def test_dual_camera_count_two_can_be_code_verified():
    record = resolve_one(
        field("number_of_cameras", "Number of Cameras"),
        synthesis("Number of Cameras", "2", "Front + cabin dual lens dash cam"),
    )

    assert record.eligible_for_autofill is True
    assert record.gate_reason == GATE_DETERMINISTIC_SYNTHESIS


def test_bracket_inclusion_can_be_code_verified():
    record = resolve_one(
        field("mounting_bracket_included", "Mounting Bracket Included"),
        synthesis(
            "Mounting Bracket Included",
            "Yes",
            "Package includes dash cam, charger, bracket and adhesive",
        ),
    )

    assert record.eligible_for_autofill is True
    assert record.gate_reason == GATE_DETERMINISTIC_SYNTHESIS


def test_open_ended_camera_type_mapping_stays_review_only():
    record = resolve_one(
        field("camera_type", "Camera_Type", multi_value=True),
        synthesis("Camera_Type", "Dashboard|In-Car", "Front + cabin dual lens dash cam"),
    )

    assert record.eligible_for_autofill is False
    assert record.preview_eligible is True
    assert record.gate_reason == GATE_LOW_CONFIDENCE


def test_storage_capacity_from_memory_card_variant_stays_review_only():
    record = resolve_one(
        field("storage_capacity", "Storage Capacity"),
        synthesis("Storage Capacity", "64", "Selected variant includes a 64GB memory card"),
    )

    assert record.eligible_for_autofill is False
    assert record.preview_eligible is True
    assert record.gate_reason == GATE_LOW_CONFIDENCE
